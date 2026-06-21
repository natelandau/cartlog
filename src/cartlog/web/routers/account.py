"""Account management routes: self-service password change and forced password change."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session  # noqa: TC002 - runtime import for FastAPI Depends

from cartlog.auth.passwords import validate_password
from cartlog.auth.sessions import SessionService
from cartlog.auth.users import UserService
from cartlog.db.models import User
from cartlog.web.dependencies import cookie_is_secure, get_session, resolve_settings
from cartlog.web.guards import AuthRedirect, load_user
from cartlog.web.routers.auth_routes import _set_session_cookie
from cartlog.web.templating import templates

router = APIRouter()


def _require_login(request: Request, session: Session) -> None:
    """Redirect unauthenticated requests to /login by raising AuthRedirect.

    Call this at the top of any route that requires a logged-in user. The
    exception is caught by the app-level AuthRedirect exception handler.

    Args:
        request: The incoming FastAPI request.
        session: The active database session for user resolution.
    """
    if load_user(request, session) is None:
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        encoded = quote(target, safe="")
        location = "/login?next=" + encoded
        raise AuthRedirect(location)


def _revoke_and_refresh(
    session: Session,
    request: Request,
    user_id: int,
    response: Response,
) -> None:
    """Revoke all sessions for a user and create a fresh one, then set the new cookie.

    Revoking all sessions after a password change invalidates any stolen or stale
    tokens. Creating a fresh session immediately keeps the current browser logged in
    so the user is not forced to sign in again after changing their own password.

    Args:
        session: The active SQLAlchemy session.
        request: The incoming request (used to read UA and IP for the new session).
        user_id: The PK of the user whose sessions to revoke.
        response: The response object to attach the new session cookie to.
    """
    settings = resolve_settings(request)
    svc = SessionService(
        session,
        lifetime_days=settings.session_lifetime_days,
        idle_timeout_days=settings.session_idle_timeout_days,
    )
    # Revoke everything first so all other browsers/devices are signed out.
    svc.revoke_all_for_user(user_id)
    # Create a replacement session for the current request so the user stays logged in.
    user = session.get(User, user_id)
    if user is None:
        return
    client_host = request.client.host if request.client else None
    new_sess = svc.create(
        user,
        user_agent=request.headers.get("user-agent"),
        ip=client_host,
    )
    _set_session_cookie(response, new_sess.id, secure=cookie_is_secure(request))


@router.get("/account", response_class=HTMLResponse)
def account_page(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Render the account settings page for the current user.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
    """
    _require_login(request, session)
    return templates.TemplateResponse(
        request, "auth/account.html", {"error": None, "success": None}
    )


@router.post("/account/password")
def account_change_password(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    current: Annotated[str, Form()],
    new: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
) -> Response:
    """Change the authenticated user's password after verifying the current one.

    Verifies the current password, validates the new one, then revokes all existing
    sessions and issues a fresh cookie so the user stays logged in without disruption.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
        current: The user's current plaintext password.
        new: The desired new plaintext password.
        confirm: Repeated new password for typo prevention.
    """
    _require_login(request, session)
    current_user = load_user(request, session)
    if current_user is None:
        # Should not reach here after _require_login, but guards against a race.
        login_url = "/login"
        raise AuthRedirect(login_url)

    users = UserService(session)

    # Verify the current password by re-authenticating; this prevents an
    # attacker with only a stolen session cookie from changing the password.
    if users.authenticate(current_user.username, current) is None:
        return templates.TemplateResponse(
            request,
            "auth/account.html",
            {"error": "Current password is incorrect.", "success": None},
            status_code=422,
        )

    if new != confirm:
        return templates.TemplateResponse(
            request,
            "auth/account.html",
            {"error": "New passwords don't match.", "success": None},
            status_code=422,
        )

    policy_error = validate_password(new)
    if policy_error:
        return templates.TemplateResponse(
            request,
            "auth/account.html",
            {"error": policy_error, "success": None},
            status_code=422,
        )

    users.set_password(current_user, new)
    # Revoke and refresh before committing so the new session is inside this transaction.
    response: Response = RedirectResponse("/account?changed=1", status_code=303)
    _revoke_and_refresh(session, request, current_user.id, response)
    session.commit()
    return response


@router.get("/change-password", response_class=HTMLResponse)
def change_password_form(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Render the forced password-change page for users flagged with must_change_password.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
    """
    _require_login(request, session)
    return templates.TemplateResponse(
        request,
        "auth/change_password.html",
        {"error": None},
    )


@router.post("/change-password")
def change_password_submit(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    new: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
) -> Response:
    """Handle the forced password-change form submission.

    Does not require the current password because the user may have been
    assigned an initial password by an admin and may not know it. Clears
    the must_change_password flag on success so the user is no longer gated.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
        new: The desired new plaintext password.
        confirm: Repeated new password for typo prevention.
    """
    _require_login(request, session)
    current_user = load_user(request, session)
    if current_user is None:
        login_url = "/login"
        raise AuthRedirect(login_url)

    if new != confirm:
        return templates.TemplateResponse(
            request,
            "auth/change_password.html",
            {"error": "Passwords don't match."},
            status_code=422,
        )

    policy_error = validate_password(new)
    if policy_error:
        return templates.TemplateResponse(
            request,
            "auth/change_password.html",
            {"error": policy_error},
            status_code=422,
        )

    # must_change=False clears the flag so the middleware no longer redirects this user.
    UserService(session).set_password(current_user, new, must_change=False)
    response: Response = RedirectResponse("/", status_code=303)
    _revoke_and_refresh(session, request, current_user.id, response)
    session.commit()
    return response
