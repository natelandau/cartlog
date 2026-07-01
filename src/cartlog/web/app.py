"""FastAPI application factory for the cartlog web UI.

Creates one SQLAlchemy engine for the app's lifetime (via lifespan) and exposes it as
`app.state.session_factory`; requests pull sessions through the `get_session` dependency.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from cartlog.config import get_settings
from cartlog.db.session import create_session_factory
from cartlog.web.guards import AuthRedirect, Forbidden
from cartlog.web.middleware import (
    CsrfMiddleware,
    ForcePasswordChangeMiddleware,
    SetupGateMiddleware,
    csrf_protect,
)
from cartlog.web.routers import (
    account,
    admin,
    analytics,
    auth_routes,
    categories,
    dashboard,
    health,
    insights,
    integrations,
    jobs,
    preferences,
    receipts,
    settings,
    setup,
    tokens,
    users,
)
from cartlog.web.templating import templates

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.requests import Request

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the session factory at startup and dispose its engine at shutdown."""
    session_factory = create_session_factory(get_settings().database_url)
    app.state.session_factory = session_factory
    try:
        yield
    finally:
        session_factory.kw["bind"].dispose()


def create_app(*, dev: bool = False) -> FastAPI:
    """Build the cartlog FastAPI app with static files and routers wired up.

    Args:
        dev: Run in development mode. Reloads templates from disk on each render so edits
            appear without restarting the server, and surfaces debug tracebacks. Production
            (the default) reuses the compiled template cache instead.
    """
    app = FastAPI(
        title="cartlog", lifespan=lifespan, debug=dev, dependencies=[Depends(csrf_protect)]
    )
    # Dev re-stats templates so edits show live; production reuses the compiled cache.
    templates.env.auto_reload = dev
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(health.router)
    app.include_router(setup.router)
    app.include_router(auth_routes.router)
    app.include_router(account.router)
    app.include_router(dashboard.router)
    app.include_router(receipts.router)
    app.include_router(jobs.router)
    app.include_router(analytics.router)
    app.include_router(insights.router)
    app.include_router(categories.router)
    app.include_router(admin.router)
    app.include_router(preferences.router)
    app.include_router(integrations.router)
    app.include_router(settings.router)
    app.include_router(users.router)
    app.include_router(tokens.router)

    # Middleware registration order: add_middleware pushes each layer onto a stack, so the last
    # one added is the outermost (runs first). Desired order outermost-to-innermost:
    #   1. SetupGateMiddleware  - redirects to /setup when no users exist
    #   2. ForcePasswordChangeMiddleware - redirects must_change_password users
    #   3. CsrfMiddleware       - bootstraps the CSRF token cookie
    # To achieve this we add them in reverse: CsrfMiddleware first, then Force, then Setup.
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(ForcePasswordChangeMiddleware)
    app.add_middleware(SetupGateMiddleware)

    @app.exception_handler(AuthRedirect)
    async def _auth_redirect(request: Request, exc: AuthRedirect) -> Response:
        # htmx requests cannot follow a 3xx directly; instruct the browser via HX-Redirect instead.
        if request.headers.get("hx-request") == "true":
            return Response(status_code=204, headers={"HX-Redirect": exc.location})
        return RedirectResponse(exc.location, status_code=303)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden) -> Response:  # noqa: ARG001
        return Response("Forbidden", status_code=403)

    return app


app = create_app()
