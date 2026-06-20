"""Fixtures for cartlog web tests: a seeded in-memory app client with auth and CSRF support."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cartlog.auth.sessions import SessionService
from cartlog.config import Settings, get_settings
from cartlog.db.base import Base
from cartlog.db.models import Role
from cartlog.db.seed import seed_app_config
from cartlog.web.app import create_app
from cartlog.web.dependencies import get_session
from cartlog.web.middleware import CSRF_COOKIE
from cartlog.web.security import make_csrf_token
from tests.factories import seed_receipts, seed_user

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine

# Must match the CARTLOG_SECRET_KEY set by the autouse _test_secret_key fixture
# in tests/conftest.py so the middleware and the test client use the same key.
_TEST_SECRET_KEY = "test-secret-key-0123456789abcdef"  # noqa: S105


class _AuthClient(TestClient):
    """TestClient subclass that auto-injects a CSRF cookie and header on every request.

    This ensures unsafe methods (POST, PUT, DELETE, PATCH) pass the CSRF middleware
    without each test having to manually manage the double-submit pattern.
    """

    def request(self, method: str, url: str, **kwargs: Any) -> Any:  # noqa: ANN401  # ty: ignore[invalid-method-override]  # starlette uses httpx2 internally; the two Response types are structurally identical but nominally distinct to ty
        token = make_csrf_token(_TEST_SECRET_KEY)
        # Set the CSRF cookie so the middleware sees a valid value in request.cookies.
        self.cookies.set(CSRF_COOKIE, token)
        headers = dict(kwargs.pop("headers", None) or {})
        # Echo the same token in the header so the double-submit check passes.
        headers.setdefault("x-csrf-token", token)
        return super().request(method, url, headers=headers, **kwargs)


def _make_client(
    tmp_path: Path,
    *,
    role: Role | None,
) -> tuple[TestClient, Engine]:
    """Build a TestClient over a fresh in-memory database seeded with receipts and app config.

    Args:
        tmp_path: Per-test temporary directory used for the image storage dir.
        role: The Role to authenticate as, or None for an anonymous client.

    Returns:
        A (client, engine) tuple; call engine.dispose() in the fixture teardown.
    """
    # StaticPool keeps all sessions on the same in-memory connection so rows seeded
    # before the client is created remain visible to the routes under test.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as s:
        seed_receipts(s)
        # seed_app_config ensures the AppConfig row exists, which AppConfigService needs.
        seed_app_config(s)
        s.commit()

    settings = Settings(
        database_url="sqlite://",
        image_storage_dir=tmp_path / "storage",
        secret_key=_TEST_SECRET_KEY,
        # Disable secure flag so the session cookie works over plain HTTP in tests.
        cookie_secure=False,
    )

    app = create_app()
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.settings = settings

    def _override_get_session() -> Iterator:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings] = lambda: settings

    # Use _AuthClient for role-based fixtures so CSRF is handled automatically.
    client: TestClient = _AuthClient(app) if role is not None else TestClient(app)

    if role is not None:
        with factory() as s:
            user = seed_user(s, username=role.value, role=role)
            sess = SessionService(s).create(user)
            s.commit()
            # __Host- cookies require HTTPS; the plain fallback is read by _cookie_value in auth.py.
            client.cookies.set("cartlog_session", sess.id)

    return client, engine


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield an authenticated ADMIN TestClient over a seeded in-memory database.

    Existing tests rely on this fixture; it is now admin-authenticated and CSRF-aware
    so mutation requests (POST/DELETE) pass middleware checks automatically.
    """
    client, engine = _make_client(tmp_path, role=Role.ADMIN)
    yield client
    engine.dispose()


@pytest.fixture
def admin_client(app_client: TestClient) -> TestClient:
    """Alias of app_client for tests that want an explicit name for the admin role."""
    return app_client


@pytest.fixture
def editor_client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield an authenticated EDITOR TestClient over a seeded in-memory database."""
    client, engine = _make_client(tmp_path, role=Role.EDITOR)
    yield client
    engine.dispose()


@pytest.fixture
def viewer_client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield an authenticated VIEWER TestClient over a seeded in-memory database."""
    client, engine = _make_client(tmp_path, role=Role.VIEWER)
    yield client
    engine.dispose()


@pytest.fixture
def anon_client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield an unauthenticated plain TestClient over a seeded in-memory database."""
    client, engine = _make_client(tmp_path, role=None)
    yield client
    engine.dispose()
