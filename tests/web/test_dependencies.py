"""Tests for web request dependencies."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cartlog.analytics.service import AnalyticsService
from cartlog.db.base import Base
from cartlog.web.dependencies import get_analytics_service, get_session


class _FakeApp:
    """Minimal stand-in exposing `state.session_factory` like a FastAPI app."""

    def __init__(self, factory):
        self.state = type("S", (), {"session_factory": factory})()


class _FakeRequest:
    """Minimal stand-in exposing `.app` like a FastAPI Request."""

    def __init__(self, app):
        self.app = app


def test_get_session_yields_session_from_app_factory():
    """Verify get_session yields a usable Session bound to the app factory."""
    # Given an in-memory DB and an app exposing its session factory
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    request = _FakeRequest(_FakeApp(factory))

    # When pulling a session out of the generator dependency
    gen = get_session(request)  # type: ignore[ty:invalid-argument-type]  # _FakeRequest stand-in
    session = next(gen)

    # Then it is a live Session
    assert isinstance(session, Session)
    assert session.is_active
    # And exhausting the generator closes it without error
    for _ in gen:
        pass
    engine.dispose()


def test_get_analytics_service_wraps_session():
    """Verify get_analytics_service returns a service bound to the given session."""
    # Given a session
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        # When constructing the service dependency
        service = get_analytics_service(session=session)

        # Then it is an AnalyticsService over that session
        assert isinstance(service, AnalyticsService)
    engine.dispose()
