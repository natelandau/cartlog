"""Login, logout, and session-cookie helpers for the cartlog web UI."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session  # noqa: TC002

from cartlog.auth.ratelimit import LoginRateLimiter
from cartlog.auth.sessions import SessionService
from cartlog.auth.users import UserService
from cartlog.config import get_settings
from cartlog.web.auth import COOKIE_NAME
from cartlog.web.dependencies import get_session
from cartlog.web.templating import templates

router = APIRouter()

# Module-level rate limiter; shared across all login requests in the process.
# Single-process state is intentional; see LoginRateLimiter docstring.
_rate_limiter = LoginRateLimiter()


def _safe_next(next_value: str) -> str:
    r"""Return next_value only if it is a local path, else '/' (prevents open redirects).

    Accepts a path only when it starts with a single '/' and is not the '//' or '/\'
    vectors that browsers may treat as a host prefix.

    Args:
        next_value: The raw 'next' query/form value from an unauthenticated request.

    Returns:
        The validated path, or '/' when validation fails.
    """
    if next_value.startswith("/") and not next_value.startswith(("//", "/\\")):
        return next_value
    return "/"


def _set_session_cookie(response: Response, session_id: str, *, secure: bool) -> None:
    """Set the session cookie with appropriate name and security flags for the environment.

    The __Host- prefix requires Secure + path=/ and is only valid over HTTPS. Plain HTTP
    dev/test environments use the bare name so the cookie round-trips without error.

    Args:
        response: The FastAPI/Starlette response to attach the cookie to.
        session_id: The opaque session token to store in the cookie.
        secure: When True, use the __Host- prefixed name and the Secure attribute.
    """
    name = COOKIE_NAME if secure else "cartlog_session"
    response.set_cookie(
        key=name,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    """Render the login page with an empty error state and the validated next destination.

    Reads ?next= from the query string so the hidden form field preserves the intended
    destination across the login round-trip. The value is validated by _safe_next to
    prevent open-redirect attacks before it is embedded in the template.
    """
    next_value = _safe_next(request.query_params.get("next", "/"))
    return templates.TemplateResponse(
        request, "auth/login.html", {"error": None, "next": next_value}
    )


@router.post("/login")
def login_submit(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_url: Annotated[str, Form(alias="next")] = "/",
) -> Response:
    """Authenticate the user and start a session, or re-render with a generic error.

    Rate-limits by username+IP to slow brute-force attacks. On success, creates a
    server-side session row and sets the session cookie before redirecting.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
        username: The submitted login handle.
        password: The submitted plaintext password.
        next_url: The path to redirect to after a successful login (validated for safety).

    Returns:
        A 303 redirect on success, or a 422/429 HTML response on failure.
    """
    client_host = request.client.host if request.client else "?"
    key = f"{username.lower()}|{client_host}"

    if not _rate_limiter.check(key):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "Too many attempts. Wait a few minutes and try again.", "next": next_url},
            status_code=429,
        )

    user = UserService(session).authenticate(username, password)
    if user is None:
        _rate_limiter.record_failure(key)
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "That username and password don't match.", "next": next_url},
            status_code=422,
        )

    _rate_limiter.reset(key)
    settings = get_settings()
    sess = SessionService(
        session,
        lifetime_days=settings.session_lifetime_days,
        idle_timeout_days=settings.session_idle_timeout_days,
    ).create(
        user,
        user_agent=request.headers.get("user-agent"),
        ip=client_host,
    )
    session.commit()

    # Redirect to the forced password-change page when required, else the intended destination.
    target = "/change-password" if user.must_change_password else _safe_next(next_url)
    response = RedirectResponse(target, status_code=303)
    _set_session_cookie(response, sess.id, secure=settings.cookie_secure)
    return response


@router.post("/logout")
def logout(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Revoke the current session and clear both cookie variants, then redirect to login.

    Checks both the __Host- prefixed name (HTTPS) and the bare name (HTTP/dev) so a
    user can log out regardless of which cookie was set at login time.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.

    Returns:
        A 303 redirect to /login.
    """
    sid = request.cookies.get(COOKIE_NAME) or request.cookies.get("cartlog_session")
    if sid:
        SessionService(session).revoke(sid)
        session.commit()

    response = RedirectResponse("/login", status_code=303)
    # Delete both names so stale cookies from environment switches are cleaned up.
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie("cartlog_session", path="/")
    return response
