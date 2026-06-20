"""Tests for the admin public-read toggle and its effect on anonymous access."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cartlog.auth.app_config import AppConfigService
from cartlog.db.models import Role
from tests.factories import seed_user
from tests.web.helpers import get_session_factory

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def test_read_route_blocks_anon_when_private(anon_client: TestClient) -> None:
    """Verify /receipts redirects anonymous users to /login when allow_anonymous_read is False."""
    # Given an admin user (so setup gate does not fire) and private posture
    with get_session_factory(anon_client)() as s:
        seed_user(s, username="dad", role=Role.ADMIN)
        AppConfigService(s).set_allow_anonymous_read(value=False)
        s.commit()

    # When an anonymous user visits /receipts
    resp = anon_client.get("/receipts", follow_redirects=False)

    # Then they are redirected to /login
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


def test_read_route_open_to_anon_by_default(anon_client: TestClient) -> None:
    """Verify /receipts is accessible to anonymous users when allow_anonymous_read is True."""
    # Given an admin user exists (so setup gate does not fire) and the default public posture
    with get_session_factory(anon_client)() as s:
        seed_user(s, username="dad", role=Role.ADMIN)
        s.commit()

    # When an anonymous user visits /receipts
    resp = anon_client.get("/receipts")

    # Then they can access it
    assert resp.status_code == 200


def test_admin_can_toggle_to_private(admin_client: TestClient) -> None:
    """Verify POST /admin/settings/access with posture=private sets allow_anonymous_read False."""
    # When an admin posts posture=private
    resp = admin_client.post("/admin/settings/access", data={"posture": "private"})

    # Then the response indicates success
    assert resp.status_code in (200, 303)

    # And the config is updated
    with get_session_factory(admin_client)() as s:
        assert AppConfigService(s).allow_anonymous_read() is False


def test_admin_can_toggle_to_open(admin_client: TestClient) -> None:
    """Verify POST /admin/settings/access with posture=open sets allow_anonymous_read True."""
    # Given the app is currently private
    with get_session_factory(admin_client)() as s:
        AppConfigService(s).set_allow_anonymous_read(value=False)
        s.commit()

    # When an admin posts posture=open
    resp = admin_client.post("/admin/settings/access", data={"posture": "open"})

    # Then the response indicates success
    assert resp.status_code in (200, 303)

    # And the config is updated to True
    with get_session_factory(admin_client)() as s:
        assert AppConfigService(s).allow_anonymous_read() is True


def test_editor_cannot_toggle_access(editor_client: TestClient) -> None:
    """Verify POST /admin/settings/access returns 403 for editor-role users."""
    # When an editor tries to post to the access endpoint
    resp = editor_client.post("/admin/settings/access", data={"posture": "private"})

    # Then they are forbidden
    assert resp.status_code == 403
