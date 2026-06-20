"""Per-request FastAPI dependencies for the web layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import (
    Session,  # noqa: TC002  # runtime import: FastAPI resolves Annotated[Session, Depends(...)] in this module's namespace
)

from cartlog.analytics.service import AnalyticsService
from cartlog.config import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import Generator


def get_session(request: Request) -> Generator[Session]:
    """Yield a database session from the app-lifespan session factory, closed per request."""
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


def resolve_settings(request: Request) -> Settings:
    """Return the app's Settings, preferring app.state so test fixtures can override them.

    Read settings at request time rather than binding them at construction: a fixture that
    swaps ``app.state.settings`` after the app is built then takes effect. The cached
    ``get_settings()`` is the fallback when no override is bound.
    """
    app_settings = getattr(request.app.state, "settings", None)
    return app_settings if app_settings is not None else get_settings()


def get_analytics_service(
    session: Annotated[Session, Depends(get_session)],
) -> AnalyticsService:
    """Return an AnalyticsService bound to the request's session."""
    return AnalyticsService(session)
