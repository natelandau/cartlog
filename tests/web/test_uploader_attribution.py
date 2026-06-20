"""Tests that receipt uploads are attributed to the submitting user."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cartlog.auth.tokens import ApiTokenService
from cartlog.auth.users import UserService
from cartlog.db.models import Category, IngestionJob, Role, User
from cartlog.ingest.persistence import persist_receipt
from tests.factories import seed_user
from tests.web.conftest import _AuthClient
from tests.web.helpers import get_session_factory

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _png_bytes() -> bytes:
    # Minimal valid-looking PNG header; workers never run in these tests.
    return b"\x89PNG\r\n\x1a\n" + b"0" * 32


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def token_client(tmp_path: object, editor_client: TestClient) -> tuple[TestClient, str, int]:
    """Yield a plain (cookie-less) TestClient and a minted API token for an editor user.

    Returns:
        A (client, plaintext_token, editor_user_id) triple.
    """
    factory = get_session_factory(editor_client)

    # The editor_client fixture already seeds a user with username="editor" at Role.EDITOR.
    # Seed an admin so the setup gate never fires (the app needs at least one admin to be "set up").
    with factory() as s:
        seed_user(s, username="admin_for_token_test", role=Role.ADMIN)
        s.commit()

    # Mint a token for the editor user.
    with factory() as s:
        editor = UserService(s).get_by_username("editor")
        assert editor is not None
        editor_id = editor.id
        _, plaintext = ApiTokenService(s).mint(editor, "test-token")
        s.commit()

    # Build a plain TestClient (no session cookie) backed by the same app as editor_client.
    # Use _AuthClient so CSRF headers are injected on POST, matching what the middleware expects.
    plain_client: TestClient = _AuthClient(editor_client.app)
    return plain_client, plaintext, editor_id


# ---------------------------------------------------------------------------
# Web upload: session-cookie path
# ---------------------------------------------------------------------------


def test_web_upload_records_uploader_on_ingestion_job(editor_client: TestClient) -> None:
    """Verify a web upload tags the resulting IngestionJob with the editor's user_id."""
    factory = get_session_factory(editor_client)

    # Given the editor's user_id from the seeded database
    with factory() as s:
        editor = UserService(s).get_by_username("editor")
        assert editor is not None
        editor_id = editor.id

    # When the editor uploads a receipt via the browser
    response = editor_client.post(
        "/receipts",
        files=[("files", ("receipt.png", _png_bytes(), "image/png"))],
    )

    # Then the enqueued job is attributed to that editor
    assert response.status_code == 202
    with factory() as s:
        job = s.query(IngestionJob).order_by(IngestionJob.id.desc()).first()
        assert job is not None
        assert job.user_id == editor_id


# ---------------------------------------------------------------------------
# API-token upload: bearer-token path
# ---------------------------------------------------------------------------


def test_api_token_upload_records_uploader_on_ingestion_job(
    token_client: tuple[TestClient, str, int],
) -> None:
    """Verify a bearer-token upload tags the IngestionJob with the token owner's user_id."""
    client, plaintext, editor_id = token_client
    factory = get_session_factory(client)

    # When a request arrives via API token (no session cookie, only Authorization header)
    response = client.post(
        "/receipts",
        files=[("files", ("receipt.png", _png_bytes(), "image/png"))],
        headers={"Authorization": f"Bearer {plaintext}"},
    )

    # Then the enqueued job is attributed to the token owner
    assert response.status_code == 202
    with factory() as s:
        job = s.query(IngestionJob).order_by(IngestionJob.id.desc()).first()
        assert job is not None
        assert job.user_id == editor_id


# ---------------------------------------------------------------------------
# Persistence unit tests: user_id flows onto the Receipt
# ---------------------------------------------------------------------------


def test_persist_receipt_records_user_id(session, sample_parsed_receipt) -> None:
    """Verify persist_receipt sets user_id on the Receipt when a user_id is provided."""
    # Given a seeded category and a user_id
    session.add(Category(name="dairy & eggs"))
    session.add(Category(name="produce"))
    session.flush()
    user = User(username="test-user", password_hash="x", role=Role.EDITOR)
    session.add(user)
    session.flush()

    # When a receipt is persisted with that user_id
    receipt, _ = persist_receipt(
        session,
        sample_parsed_receipt,
        image_path="/tmp/x.png",  # noqa: S108
        source="web",
        status="parsed",
        raw_json="{}",
        user_id=user.id,
    )

    # Then the Receipt carries the uploader's id
    assert receipt.user_id == user.id


def test_persist_receipt_user_id_none_for_folder_ingest(session, sample_parsed_receipt) -> None:
    """Verify persist_receipt leaves user_id None when no user_id is supplied."""
    # Given seeded categories
    session.add(Category(name="dairy & eggs"))
    session.add(Category(name="produce"))
    session.flush()

    # When a receipt is persisted without a user_id (folder ingest path)
    receipt, _ = persist_receipt(
        session,
        sample_parsed_receipt,
        image_path="/tmp/x.png",  # noqa: S108
        source="folder",
        status="parsed",
        raw_json="{}",
    )

    # Then user_id is None, reflecting that no authenticated user submitted it
    assert receipt.user_id is None
