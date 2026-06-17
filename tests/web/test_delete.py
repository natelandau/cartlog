"""Tests for the DELETE /receipts/{id} route."""

from __future__ import annotations

from cartlog.db.models import Receipt
from tests.web.helpers import first_receipt_id


def test_delete_receipt_returns_hx_redirect(app_client) -> None:
    """Verify DELETE /receipts/{id} deletes the receipt and tells htmx to redirect to the list."""
    # Given a seeded receipt
    rid = first_receipt_id(app_client)

    # When deleting it
    response = app_client.delete(f"/receipts/{rid}")

    # Then htmx is told to redirect to the list and the receipt is gone
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/receipts"
    state = app_client.app.state
    with state.session_factory() as session:
        assert session.get(Receipt, rid) is None


def test_delete_receipt_unknown_id_returns_404(app_client) -> None:
    """Verify deleting a nonexistent receipt returns 404."""
    # When deleting an id that does not exist
    response = app_client.delete("/receipts/99999")

    # Then the route reports not found
    assert response.status_code == 404


def test_receipt_detail_shows_delete_button(app_client) -> None:
    """Verify the receipt detail page renders an hx-delete button guarded by a confirm prompt."""
    # Given a seeded receipt
    rid = first_receipt_id(app_client)

    # When viewing its detail page
    response = app_client.get(f"/receipts/{rid}")

    # Then the page exposes an hx-delete control with a confirmation prompt
    assert response.status_code == 200
    assert f'hx-delete="/receipts/{rid}"' in response.text
    assert "hx-confirm=" in response.text
