"""Tests for the account page and forced password-change flow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cartlog.auth.sessions import SessionService
from cartlog.auth.users import UserService
from cartlog.config import Settings
from cartlog.config import get_settings as _get_settings
from cartlog.db.base import Base
from cartlog.db.models import Role, User
from cartlog.db.seed import seed_app_config
from cartlog.web.app import create_app
from cartlog.web.dependencies import get_session
from cartlog.web.middleware import CSRF_COOKIE
from cartlog.web.security import make_csrf_token
from tests.factories import seed_receipts
from tests.web.helpers import get_session_factory

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine

# Must match the CARTLOG_SECRET_KEY set by the autouse _test_secret_key fixture.
_TEST_KEY = "test-secret-key-0123456789abcdef"


class _CsrfClient(TestClient):
    """TestClient that auto-injects a CSRF cookie and header on every request."""

    def request(self, method: str, url: str, **kwargs: Any) -> Any:  # noqa: ANN401  # ty: ignore[invalid-method-override]  # starlette uses httpx2 internally; the two Response types are structurally identical but nominally distinct to ty
        t = make_csrf_token(_TEST_KEY)
        self.cookies.set(CSRF_COOKIE, t)
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("x-csrf-token", t)
        return super().request(method, url, headers=headers, **kwargs)


def _build_app(tmp_path: Path) -> tuple[_CsrfClient, Engine]:
    """Create a fresh in-memory app with an unauthenticated _CsrfClient.

    Args:
        tmp_path: Per-test temporary directory for image storage.

    Returns:
        A (client, engine) pair. Call engine.dispose() in teardown.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as s:
        seed_receipts(s)
        seed_app_config(s)
        s.commit()

    settings = Settings(
        database_url="sqlite://",
        image_storage_dir=tmp_path / "storage",
        secret_key=_TEST_KEY,
        cookie_secure=False,
    )

    app = create_app()
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.settings = settings

    def _override_session() -> Iterator:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[_get_settings] = lambda: settings

    client = _CsrfClient(app, follow_redirects=False)
    return client, engine


# ---------------------------------------------------------------------------
# Tests using the shared admin_client fixture from conftest.py
# ---------------------------------------------------------------------------


def test_change_password_updates_hash(admin_client: TestClient) -> None:
    """Verify POST /account/password with correct current password updates the stored hash."""
    # When the admin posts a valid password change
    resp = admin_client.post(
        "/account/password",
        data={
            "current": "violet pantry koala",
            "new": "amber pillow river12",
            "confirm": "amber pillow river12",
        },
        follow_redirects=False,
    )

    # Then the response redirects (303) to the account page confirming success
    assert resp.status_code == 303
    with get_session_factory(admin_client)() as s:
        assert UserService(s).authenticate("admin", "amber pillow river12") is not None


def test_change_password_rejects_wrong_current(admin_client: TestClient) -> None:
    """Verify POST /account/password with the wrong current password is rejected."""
    # When the admin posts the wrong current password
    resp = admin_client.post(
        "/account/password",
        data={
            "current": "this-is-the-wrong-password",
            "new": "amber pillow river12",
            "confirm": "amber pillow river12",
        },
        follow_redirects=False,
    )

    # Then the form is rejected (not a redirect to success)
    assert resp.status_code in (200, 422)
    # And the original password still works
    with get_session_factory(admin_client)() as s:
        assert UserService(s).authenticate("admin", "violet pantry koala") is not None


# ---------------------------------------------------------------------------
# Tests requiring a must_change_password user (need their own app instance)
# ---------------------------------------------------------------------------


@pytest.fixture
def must_change_client(tmp_path: Path) -> Iterator[tuple[_CsrfClient, Engine, int]]:
    """Yield a (client, engine, user_id) for a user with must_change_password=True."""
    client, engine = _build_app(tmp_path)
    factory = get_session_factory(client)

    with factory() as s:
        user = UserService(s).create_user(
            "mustchange", "violet pantry koala", Role.EDITOR, must_change_password=True
        )
        s.flush()
        sess = SessionService(s).create(user)
        s.commit()
        session_id = sess.id
        user_id = user.id

    client.cookies.set("cartlog_session", session_id)
    yield client, engine, user_id
    engine.dispose()


def test_must_change_password_redirects(
    must_change_client: tuple[_CsrfClient, Engine, int],
) -> None:
    """Verify a user with must_change_password=True is redirected to /change-password."""
    client, _engine, _user_id = must_change_client

    # When a must_change_password user visits any ordinary page
    resp = client.get("/")

    # Then they are redirected to /change-password
    assert resp.status_code == 303
    assert resp.headers["location"] == "/change-password"


def test_forced_change_password_clears_flag(
    must_change_client: tuple[_CsrfClient, Engine, int],
) -> None:
    """Verify completing the /change-password flow clears must_change_password and redirects to /."""
    client, _engine, user_id = must_change_client
    factory = get_session_factory(client)

    # When the user submits the forced change-password form
    resp = client.post(
        "/change-password",
        data={
            "new": "new secure pass123",
            "confirm": "new secure pass123",
        },
    )

    # Then they are redirected to /
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    # And the must_change_password flag is cleared
    with factory() as s:
        u = s.scalar(select(User).where(User.id == user_id))
        assert u is not None
        assert u.must_change_password is False
