"""First-run onboarding wizard. Creates the first admin, then locks itself.

The /setup route is exempt from the SetupGateMiddleware redirect so the wizard
can actually render. Once one user exists, every endpoint here redirects to / so
the setup surface cannot be used to mint a second admin.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session  # noqa: TC002

from cartlog.auth.config import AppConfigService
from cartlog.auth.passwords import validate_password
from cartlog.auth.sessions import SessionService
from cartlog.auth.users import UserService
from cartlog.config import get_settings
from cartlog.db.models import Role
from cartlog.web.auth import load_user, role_satisfies
from cartlog.web.dependencies import get_session
from cartlog.web.routers.auth_routes import _set_session_cookie
from cartlog.web.templating import templates

router = APIRouter()


def _locked(session: Session) -> bool:
    """Return True when at least one user exists, meaning setup is complete.

    Used as a guard so the setup endpoints cannot be replayed once the first
    admin is created.

    Args:
        session: The active SQLAlchemy session.
    """
    return UserService(session).count() > 0


def _account_error(
    session: Session,
    *,
    username: str,
    password: str,
    confirm: str,
) -> str | None:
    """Validate account-creation form fields and return a human-readable error or None.

    Checks run in order so the first failure is shown; a clean return means the
    server is ready to create the user.

    Args:
        session: The active SQLAlchemy session.
        username: The chosen login handle.
        password: The submitted password plaintext.
        confirm: The confirmation field value.
    """
    if not username.strip():
        return "Pick a username."
    if UserService(session).get_by_username(username) is not None:
        return "That username is taken. Try another."
    if password != confirm:
        return "Passwords don't match."
    return validate_password(password)


@router.get("/setup/step/account", response_class=HTMLResponse)
def setup_step_account(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Return the account-creation partial so the welcome panel can swap in step 1.

    Triggered by the "Set up cartlog" button on the welcome panel via hx-get. Returns
    the same partial that POST /setup/account re-renders on validation errors so the
    step-indicator listener fires correctly in both cases.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
    """
    if _locked(session):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "auth/_setup_account.html",
        {"error": None, "username": ""},
    )


@router.get("/setup", response_class=HTMLResponse)
def setup_index(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Render the wizard shell, or redirect to the dashboard if setup is already complete.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
    """
    if _locked(session):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "auth/setup.html",
        {"today": datetime.now(UTC).date().isoformat()},
    )


@router.post("/setup/account")
def setup_account(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
    name: Annotated[str, Form()] = "",
) -> Response:
    """Validate the account form, create the first admin, log them in, and advance the wizard.

    This is the commit point: on success the user row and session row are written to the
    database, the session cookie is set, and the access-step partial is returned so htmx
    swaps it into #setup-step. On validation failure the account partial is re-rendered at
    422 so the error appears inline without a page reload.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
        username: The chosen login handle.
        password: The submitted password plaintext.
        confirm: The password confirmation field.
        name: Optional display name; defaults to username when empty.
    """
    if _locked(session):
        return RedirectResponse("/", status_code=303)

    error = _account_error(session, username=username, password=password, confirm=confirm)
    if error:
        return templates.TemplateResponse(
            request,
            "auth/_setup_account.html",
            {"error": error, "username": username},
            status_code=422,
        )

    users = UserService(session)
    user = users.create_user(username, password, Role.ADMIN, name=name or None)
    # Flush to assign the user.id before SessionService needs it for the FK.
    session.flush()
    settings = get_settings()
    sess = SessionService(session).create(user)
    session.commit()

    response = templates.TemplateResponse(request, "auth/_setup_access.html", {})
    _set_session_cookie(response, sess.id, secure=settings.cookie_secure)
    return response


@router.post("/setup/access")
def setup_access(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    posture: Annotated[str, Form()] = "open",
) -> Response:
    """Persist the chosen read-access posture and show the launchpad.

    The posture field maps "open" to allow_anonymous_read=True (the safe default already
    set during seeding) and any other value to False. Only the authenticated admin created
    in the preceding wizard step may call this endpoint; anonymous and non-admin callers
    are redirected to / so they cannot flip the setting after setup is complete.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.
        posture: Form field; "open" grants anonymous read, anything else requires sign-in.
    """
    user = load_user(request, session)
    if user is None or not role_satisfies(user.role, Role.ADMIN):
        return RedirectResponse("/", status_code=303)

    AppConfigService(session).set_allow_anonymous_read(value=(posture == "open"))
    session.commit()
    return templates.TemplateResponse(request, "auth/_setup_done.html", {})
