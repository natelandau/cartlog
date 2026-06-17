"""FastAPI application factory for the cartlog web UI.

Creates one SQLAlchemy engine for the app's lifetime (via lifespan) and exposes it as
`app.state.session_factory`; requests pull sessions through the `get_session` dependency.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cartlog.config import get_settings
from cartlog.db.session import create_session_factory
from cartlog.web.routers import admin, analytics, categories, dashboard, jobs, receipts
from cartlog.web.templating import templates

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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
    app = FastAPI(title="cartlog", lifespan=lifespan, debug=dev)
    # Dev re-stats templates so edits show live; production reuses the compiled cache.
    templates.env.auto_reload = dev
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(dashboard.router)
    app.include_router(receipts.router)
    app.include_router(jobs.router)
    app.include_router(analytics.router)
    app.include_router(categories.router)
    app.include_router(admin.router)
    return app


app = create_app()
