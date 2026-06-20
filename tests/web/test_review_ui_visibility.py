"""Tests that review/edit UI affordances are hidden from read-only (viewer/anonymous) visitors.

The server already blocks the mutations with RequireEditor; these tests assert the controls
do not even render for non-editors, so a viewer never sees edit buttons or the review workflow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cartlog.db.models import Receipt, ReceiptStatus, Role
from tests.factories import seed_user
from tests.web.helpers import get_session_factory

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _needs_review_receipt_id(client: TestClient) -> int:
    """Return the id of the seeded needs-review receipt for this client's database."""
    with get_session_factory(client)() as session:
        receipt = (
            session.query(Receipt).filter(Receipt.status == ReceiptStatus.NEEDS_REVIEW).first()
        )
        assert receipt is not None
        return receipt.id


def _seed_admin_so_setup_gate_passes(client: TestClient) -> None:
    """Seed an admin in an anonymous client's db so the first-run setup gate does not redirect."""
    with get_session_factory(client)() as session:
        seed_user(session, username="dad", role=Role.ADMIN)
        session.commit()


def test_receipt_detail_hides_edit_controls_from_anon(anon_client: TestClient) -> None:
    """Verify the receipt detail page shows no edit/review controls to an anonymous visitor."""
    # Given an admin exists (so the setup gate is satisfied) and a needs-review receipt
    _seed_admin_so_setup_gate_passes(anon_client)
    receipt_id = _needs_review_receipt_id(anon_client)

    # When an anonymous visitor opens its detail page
    resp = anon_client.get(f"/receipts/{receipt_id}")

    # Then the page renders but exposes no mutating controls or review state
    assert resp.status_code == 200
    assert "Edit receipt" not in resp.text
    assert "Mark reviewed" not in resp.text
    assert "Reparse" not in resp.text
    assert "Delete receipt" not in resp.text
    assert "badge-warning" not in resp.text
    # The unit toggle is a per-visitor display preference and stays available to everyone.
    assert "Units:" in resp.text


def test_receipt_detail_shows_edit_controls_to_editor(editor_client: TestClient) -> None:
    """Verify the receipt detail page shows edit/review controls to an editor."""
    # Given a needs-review receipt
    receipt_id = _needs_review_receipt_id(editor_client)

    # When an editor opens its detail page
    resp = editor_client.get(f"/receipts/{receipt_id}")

    # Then the mutating controls and review state are present
    assert resp.status_code == 200
    assert "Edit receipt" in resp.text
    assert "Delete receipt" in resp.text
    assert "Mark reviewed" in resp.text


def test_receipt_list_hides_review_affordances_from_anon(anon_client: TestClient) -> None:
    """Verify the receipt list hides the status filter, status badges, and Review links from anon."""
    # Given an admin exists so the setup gate is satisfied
    _seed_admin_so_setup_gate_passes(anon_client)

    # When an anonymous visitor opens the receipt list
    resp = anon_client.get("/receipts")

    # Then no review-workflow affordances render
    assert resp.status_code == 200
    assert "Needs review" not in resp.text
    assert "badge-warning" not in resp.text
    assert ">Review</a>" not in resp.text


def test_receipt_list_shows_review_affordances_to_editor(editor_client: TestClient) -> None:
    """Verify the receipt list shows the status filter and review affordances to an editor."""
    # When an editor opens the receipt list
    resp = editor_client.get("/receipts")

    # Then the review-workflow affordances render
    assert resp.status_code == 200
    assert "Needs review" in resp.text


def test_dashboard_hides_review_count_from_anon(anon_client: TestClient) -> None:
    """Verify the dashboard hides the needs-review count badge from an anonymous visitor."""
    # Given an admin exists so the setup gate is satisfied
    _seed_admin_so_setup_gate_passes(anon_client)

    # When an anonymous visitor opens the dashboard
    resp = anon_client.get("/")

    # Then the needs-review call-out is absent
    assert resp.status_code == 200
    assert "need review" not in resp.text


def test_dashboard_shows_review_count_to_editor(editor_client: TestClient) -> None:
    """Verify the dashboard shows the needs-review count badge to an editor."""
    # When an editor opens the dashboard
    resp = editor_client.get("/")

    # Then the needs-review call-out renders (seed has one needs-review receipt)
    assert resp.status_code == 200
    assert "need review" in resp.text
