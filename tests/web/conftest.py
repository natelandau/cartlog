"""Fixtures for cartlog web tests: a seeded in-memory app client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cartlog.config import Settings, get_settings
from cartlog.db.base import Base
from cartlog.web.app import create_app
from cartlog.web.dependencies import get_session
from tests.factories import seed_receipts

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def app_client(tmp_path) -> Iterator[TestClient]:
    """Yield a TestClient over the app with a seeded shared in-memory database."""
    # A StaticPool keeps every session on the SAME in-memory connection so seeded
    # rows are visible across requests (default in-memory SQLite gives each
    # connection its own empty database).
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as seed_session:
        seed_receipts(seed_session)

    # database_url is unused because get_session is overridden; image_storage_dir routes
    # uploads/image serving into the per-test tmp dir instead of the real storage dir.
    settings = Settings(database_url="sqlite://", image_storage_dir=tmp_path / "storage")

    app = create_app()
    # Expose the test engine/factory/settings so tests can seed extra rows and assert DB
    # state directly (lifespan does not run, so these are not set otherwise).
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.settings = settings

    def _override_get_session() -> Iterator:
        with factory() as session:
            yield session

    # Override both DB and settings as dependencies, keyed on the function objects, so the
    # routes honor the test settings regardless of which module bound `get_settings`.
    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings] = lambda: settings

    # No `with TestClient(...)`: skipping lifespan avoids creating a second engine,
    # and the overrides supply every dependency the routes need.
    client = TestClient(app)
    yield client
    engine.dispose()
