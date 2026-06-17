"""Per-request FastAPI dependencies for the web layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import (
    Session,  # noqa: TC002  # runtime import: FastAPI resolves Annotated[Session, Depends(...)] in this module's namespace
)

from cartlog.analytics.service import AnalyticsService

if TYPE_CHECKING:
    from collections.abc import Generator


def get_session(request: Request) -> Generator[Session]:
    """Yield a database session from the app-lifespan session factory, closed per request."""
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


def get_analytics_service(
    session: Annotated[Session, Depends(get_session)],
) -> AnalyticsService:
    """Return an AnalyticsService bound to the request's session."""
    return AnalyticsService(session)
