"""Tests for the /account/tokens API token management routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient  # noqa: TC002

from cartlog.auth.sessions import SessionService
from cartlog.auth.tokens import ApiTokenService
from cartlog.auth.users import UserService
from cartlog.db.models import ApiToken, Role
from tests.factories import seed_user
from tests.web.conftest import _AuthClient
from tests.web.helpers import get_session_factory

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def second_editor_client(editor_client: TestClient) -> TestClient:
    """Return the shared editor_client with a second editor user seeded into its database.

    Returns:
        The same editor_client with a second editor user seeded into its database.
    """
    factory = get_session_factory(editor_client)
    with factory() as s:
        seed_user(s, username="editor2", role=Role.EDITOR)
        s.commit()
    return editor_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_tokens_panel_renders(editor_client: TestClient) -> None:
    """Verify GET /account/tokens returns 200 and includes the create-token form."""
    # When an editor requests the tokens panel
    resp = editor_client.get("/account/tokens")

    # Then the panel renders successfully
    assert resp.status_code == 200
    assert "API tokens" in resp.text or "api tokens" in resp.text.lower()


def test_editor_mints_token(editor_client: TestClient) -> None:
    """Verify POST /account/tokens mints a token and shows the plaintext once."""
    # When the editor submits the mint form
    resp = editor_client.post("/account/tokens", data={"name": "iphone"})

    # Then the response contains the plaintext token
    assert resp.status_code in (200, 201)
    assert "cartlog_" in resp.text


def test_viewer_cannot_mint_token(viewer_client: TestClient) -> None:
    """Verify POST /account/tokens returns 403 for a Viewer."""
    # When a viewer attempts to mint a token
    resp = viewer_client.post("/account/tokens", data={"name": "x"})

    # Then access is denied
    assert resp.status_code == 403


def test_viewer_cannot_get_tokens_panel(viewer_client: TestClient) -> None:
    """Verify GET /account/tokens returns 403 for a Viewer."""
    # When a viewer visits the tokens panel
    resp = viewer_client.get("/account/tokens")

    # Then access is denied
    assert resp.status_code == 403


def test_revoke_marks_token_inactive(editor_client: TestClient) -> None:
    """Verify revoking a token makes it unresolvable by ApiTokenService."""
    factory = get_session_factory(editor_client)

    # Given a minted token
    resp = editor_client.post("/account/tokens", data={"name": "test-token"})
    assert resp.status_code in (200, 201)

    # Extract the plaintext from the response to verify resolution later
    text = resp.text
    # Find "cartlog_" prefix in the response to extract the token
    start = text.find("cartlog_")
    assert start != -1, "Expected plaintext token in response"
    # Extract the token: it ends at the next whitespace, quote, or tag boundary
    end = start
    while end < len(text) and text[end] not in (" ", '"', "'", "<", ">", "\n", "\t", "&"):
        end += 1
    plaintext = text[start:end]

    # Get the token id from the database
    with factory() as s:
        svc = ApiTokenService(s)
        # Resolve works before revocation
        user = svc.resolve(plaintext)
        assert user is not None

        # Get the token row to find its id
        tokens = svc.list_for_user(user.id)
        assert len(tokens) >= 1
        token_id = tokens[0].id

    # When the token is revoked
    rev_resp = editor_client.post(f"/account/tokens/{token_id}/revoke")
    assert rev_resp.status_code in (200, 204)

    # Then it no longer resolves
    with factory() as s:
        resolved = ApiTokenService(s).resolve(plaintext)
        assert resolved is None


def test_tokens_listed_on_panel(editor_client: TestClient) -> None:
    """Verify that previously minted tokens appear in the token list panel."""
    # Given two tokens minted by the editor
    editor_client.post("/account/tokens", data={"name": "phone"})
    editor_client.post("/account/tokens", data={"name": "laptop"})

    # When the editor fetches the panel
    resp = editor_client.get("/account/tokens")

    # Then both token names are shown
    assert "phone" in resp.text
    assert "laptop" in resp.text


def test_other_user_cannot_revoke_token(editor_client: TestClient) -> None:
    """Verify a token minted by one editor cannot be revoked through a second editor's session.

    This test seeds a second user; even if the second editor somehow knew the token_id,
    the revoke endpoint must silently ignore ownership mismatches (no row deleted).
    """
    factory = get_session_factory(editor_client)

    # Given a token minted by the primary editor
    editor_client.post("/account/tokens", data={"name": "primary-token"})

    with factory() as s:
        # Find the primary editor's user and their tokens
        primary = UserService(s).get_by_username("editor")
        assert primary is not None
        tokens = ApiTokenService(s).list_for_user(primary.id)
        assert len(tokens) >= 1
        token_id = tokens[0].id

    # Seed a second editor and build a separate client for them
    with factory() as s:
        second = seed_user(s, username="editor2", role=Role.EDITOR)
        sess2 = SessionService(s).create(second)
        s.commit()
        sess2_id = sess2.id

    second_client = _AuthClient(editor_client.app)
    second_client.cookies.set("cartlog_session", sess2_id)

    # When the second editor attempts to revoke the first editor's token
    rev_resp = second_client.post(f"/account/tokens/{token_id}/revoke")

    # Then the request succeeds (no 500) but the token is NOT revoked
    assert rev_resp.status_code in (200, 204)

    with factory() as s:
        row = s.get(ApiToken, token_id)
        # Token should still be active because ownership check blocked the revoke
        assert row is not None
        assert row.revoked_at is None
