"""HTTP middleware: first-run setup gate, forced password change, and CSRF protection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request  # noqa: TC002 — FastAPI resolves dependency annotations at runtime
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, Response

from cartlog.auth.users import UserService
from cartlog.web.security import make_csrf_token, verify_csrf_token

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

CSRF_COOKIE = "cartlog_csrf"
CSRF_HEADER = "x-csrf-token"
_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
_EXEMPT_PREFIXES = ("/static", "/setup")

# Paths a must_change_password user is allowed to reach without being redirected.
# /logout lets them abandon the session; /change-password is the destination itself.
_FORCE_CHANGE_EXEMPT = ("/change-password", "/logout", "/static")


class SetupGateMiddleware(BaseHTTPMiddleware):
    """Redirect all traffic to /setup until the first admin account exists."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Check for existing users and redirect to /setup if none exist.

        Args:
            request: The incoming request.
            call_next: The next middleware or route handler.

        Returns:
            A redirect to /setup when no users exist, or the normal response.
        """
        path = request.url.path
        if path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)
        factory = request.app.state.session_factory
        with factory() as session:
            has_users = UserService(session).count() > 0
        if not has_users:
            return RedirectResponse("/setup", status_code=303)
        return await call_next(request)


class ForcePasswordChangeMiddleware(BaseHTTPMiddleware):
    """Redirect authenticated users whose must_change_password flag is set to /change-password.

    This runs after CsrfMiddleware and SetupGateMiddleware so the CSRF token is already
    bootstrapped and the setup gate has already handled the no-users case. Anonymous
    users and users without the flag pass through unchanged.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Intercept requests from users who must change their password.

        Args:
            request: The incoming request.
            call_next: The next middleware or route handler.

        Returns:
            A redirect to /change-password for flagged users on non-exempt paths,
            or the normal response for everyone else.
        """
        path = request.url.path
        if path.startswith(_FORCE_CHANGE_EXEMPT):
            return await call_next(request)

        factory = request.app.state.session_factory
        with factory() as session:
            from cartlog.web.auth import load_user  # noqa: PLC0415

            user = load_user(request, session)

        if user is not None and user.must_change_password:
            return RedirectResponse("/change-password", status_code=303)

        return await call_next(request)


class CsrfMiddleware(BaseHTTPMiddleware):
    """Bootstrap the CSRF cookie and expose the token via request.state.

    Issues a fresh signed token cookie when none is present or when the existing
    cookie carries an invalid signature. Validation is delegated to the
    csrf_protect dependency so that body reads happen inside the endpoint's
    receive channel, not here.

    Settings are read from request.app.state.settings at dispatch time rather than at
    construction time so test fixtures can override them after the app is built.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Bootstrap the CSRF token cookie without reading the request body.

        Args:
            request: The incoming request.
            call_next: The next middleware or route handler.

        Returns:
            The normal response, with the CSRF cookie set when it was absent or invalid.
        """
        if request.url.path.startswith("/static"):
            return await call_next(request)

        # Read settings at dispatch time so tests can override via app.state.settings.
        app_settings = getattr(request.app.state, "settings", None)
        if app_settings is None:
            from cartlog.config import get_settings  # noqa: PLC0415

            app_settings = get_settings()
        secret: str = app_settings.secret_key
        secure: bool = app_settings.cookie_secure

        # Bootstrap: read existing cookie. Generate a fresh token if absent or invalid so the
        # template always has a usable token (setting the cookie only after call_next would
        # produce an empty token on first load, after the template has already rendered).
        existing_cookie = request.cookies.get(CSRF_COOKIE)
        if existing_cookie and verify_csrf_token(existing_cookie, secret):
            token = existing_cookie
            must_set_cookie = False
        else:
            token = make_csrf_token(secret)
            must_set_cookie = True

        # Store the chosen token so the template context processor can embed it before the
        # cookie reaches the browser.
        request.state.csrf_token = token

        response = await call_next(request)
        if must_set_cookie:
            response.set_cookie(
                CSRF_COOKIE,
                token,
                httponly=False,  # readable by JS/htmx so it can echo the token back in the header
                samesite="lax",
                secure=secure,
                path="/",
            )
        return response


async def csrf_protect(request: Request) -> None:
    """Reject unsafe requests lacking a valid CSRF token (double-submit cookie pattern).

    Reads the submitted token from the x-csrf-token header first. For form
    submissions that cannot set custom headers, falls back to reading the
    csrf_token field from the form body. Because this runs as a FastAPI
    dependency rather than BaseHTTPMiddleware, the body read here is cached by
    Starlette and remains available to the endpoint's own Form() parameters.

    Args:
        request: The incoming FastAPI request.
    """
    if request.method in _SAFE_METHODS:
        return

    # Read settings at call time so tests can override via app.state.settings.
    app_settings = getattr(request.app.state, "settings", None)
    if app_settings is None:
        from cartlog.config import get_settings  # noqa: PLC0415

        app_settings = get_settings()
    secret: str = app_settings.secret_key

    cookie = request.cookies.get(CSRF_COOKIE)
    submitted: str | None = request.headers.get(CSRF_HEADER)

    if submitted is None:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            value = form.get("csrf_token")
            submitted = value if isinstance(value, str) else None

    if not cookie or submitted != cookie or not verify_csrf_token(cookie, secret):
        from cartlog.web.auth import Forbidden  # noqa: PLC0415

        raise Forbidden
