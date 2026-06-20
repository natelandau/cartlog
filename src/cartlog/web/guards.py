"""FastAPI authentication/authorization dependencies and the role model glue."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, Request
from sqlalchemy.orm import (
    Session,  # noqa: TC002  # runtime import: Annotated[Session, Depends(...)] needs Session in this module's namespace at runtime
)

from cartlog.auth.app_config import AppConfigService
from cartlog.auth.sessions import SessionService
from cartlog.auth.tokens import ApiTokenService
from cartlog.db.models import Role, User
from cartlog.web.dependencies import get_session

# The __Host- prefix is enforced by browsers only over HTTPS; plain HTTP (dev/tests) drops it.
# Load from both names so cookies set in dev are resolved here.
COOKIE_NAME = "__Host-cartlog_session"

_RANK: dict[Role, int] = {Role.VIEWER: 1, Role.EDITOR: 2, Role.ADMIN: 3}


def role_satisfies(have: Role, need: Role) -> bool:
    """Return True if the held role meets or exceeds the required role.

    Args:
        have: The role the current user holds.
        need: The minimum role required for the operation.

    Returns:
        True when the user's rank is at or above the required rank.
    """
    return _RANK[have] >= _RANK[need]


class AuthRedirect(Exception):  # noqa: N818
    """Raised to signal an unauthenticated request must be sent to the login/setup page."""

    def __init__(self, location: str) -> None:
        """Store the target path for the redirect handler.

        Args:
            location: The URL the client should be redirected to.
        """
        self.location = location


class Forbidden(Exception):  # noqa: N818
    """Raised when an authenticated user lacks the required role."""


def _session_cookie_values(request: Request) -> list[str]:
    """Return candidate session ids from both cookie names, secure (__Host-) name first.

    Browsers only send __Host- prefixed cookies over HTTPS, so plain-HTTP dev servers set the
    bare name instead. Both can be present at once: a stale __Host- cookie from an earlier HTTPS
    (or secure-cookie) run lingers in the browser, and Chrome still sends it over http://localhost
    via its localhost secure-context exemption, alongside a fresh bare cookie. Returning both,
    rather than just the first present one, keeps a dead cookie from masking a valid session.
    """
    return [
        value for name in (COOKIE_NAME, "cartlog_session") if (value := request.cookies.get(name))
    ]


def load_user(request: Request, session: Session) -> User | None:
    """Resolve the current user from the session cookie, then from an API token.

    Use this when you need the user object outside a FastAPI dependency chain, for
    example in tests or middleware. For route dependencies, prefer get_current_user.

    Args:
        request: The incoming FastAPI request.
        session: The SQLAlchemy session for database lookups.

    Returns:
        The authenticated User, or None if no valid credential was found.
    """
    sessions = SessionService(session)
    for session_id in _session_cookie_values(request):
        user = sessions.resolve(session_id)
        if user is not None:
            return user
    # Fall through to token-based auth for API clients that cannot use cookies.
    header = request.headers.get("authorization", "")
    bearer = header[7:] if header.lower().startswith("bearer ") else None
    token = bearer or request.headers.get("x-cartlog-token")
    return ApiTokenService(session).resolve(token)


def get_current_user(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> User | None:
    """FastAPI dependency returning the current user or None.

    Resolves the user from the session cookie first; falls back to Bearer token or
    X-Cartlog-Token header for API clients.

    Args:
        request: The incoming FastAPI request.
        session: The database session injected by get_session.

    Returns:
        The authenticated User, or None if the request is unauthenticated.
    """
    return load_user(request, session)


# Annotated alias so routes can declare the dependency without repeating Depends().
CurrentUser = Annotated[User | None, Depends(get_current_user)]


def _login_redirect(request: Request) -> AuthRedirect:
    """Build a redirect to the login page that remembers the full path the user wanted.

    Preserves the query string and percent-encodes the next= value so the login
    page can safely reconstruct the original URL after authentication.
    """
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return AuthRedirect(f"/login?next={quote(target, safe='')}")


def require_read(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    user: CurrentUser,
) -> User | None:
    """FastAPI dependency allowing read access when authenticated or anonymous read is on.

    Raises AuthRedirect to /login when the request is unauthenticated and the app
    has not been configured to allow anonymous visitors.

    Args:
        request: The incoming FastAPI request.
        session: The database session used to check the anonymous-read setting.
        user: The resolved current user from get_current_user.

    Returns:
        The authenticated User, or None when anonymous access is permitted.
    """
    if user is not None:
        return user
    if AppConfigService(session).allow_anonymous_read():
        return None
    raise _login_redirect(request)


def require_editor(request: Request, user: CurrentUser) -> User:
    """FastAPI dependency requiring an authenticated Editor or Admin.

    Raises AuthRedirect when the request is unauthenticated; Forbidden when the
    user's role falls below Editor.

    Args:
        request: The incoming FastAPI request.
        user: The resolved current user from get_current_user.

    Returns:
        The authenticated User (Editor or Admin).
    """
    if user is None:
        raise _login_redirect(request)
    if not role_satisfies(user.role, Role.EDITOR):
        raise Forbidden
    return user


def require_admin(request: Request, user: CurrentUser) -> User:
    """FastAPI dependency requiring an authenticated Admin.

    Raises AuthRedirect when the request is unauthenticated; Forbidden when the
    user's role falls below Admin.

    Args:
        request: The incoming FastAPI request.
        user: The resolved current user from get_current_user.

    Returns:
        The authenticated User (Admin only).
    """
    if user is None:
        raise _login_redirect(request)
    if not role_satisfies(user.role, Role.ADMIN):
        raise Forbidden
    return user


# Role-guard aliases: inject these as `_x: RequireAdmin` (etc.) in route signatures to enforce
# the corresponding role without cluttering the handler body with auth logic.
# Placed after the function definitions so the Depends() calls resolve correctly at module load.
RequireRead = Annotated[User | None, Depends(require_read)]
RequireEditor = Annotated[User, Depends(require_editor)]
RequireAdmin = Annotated[User, Depends(require_admin)]
