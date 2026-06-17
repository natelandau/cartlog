"""Tests for HTMX server-side sorting of the receipt tables."""

from __future__ import annotations

import pytest

from tests.web.helpers import statuses_in_order, totals_in_order


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_receipt_list_sorts_by_total(app_client, direction: str) -> None:
    """Verify ?sort=total orders the list by total in the requested direction."""
    # When sorting the list by total in the given direction
    response = app_client.get("/receipts", params={"sort": "total", "direction": direction})

    # Then the rendered total cells are ordered accordingly
    assert response.status_code == 200
    totals = totals_in_order(response.text)
    assert totals == sorted(totals, reverse=direction == "desc")


def test_receipt_list_hx_request_returns_fragment_not_full_page(app_client) -> None:
    """Verify an HX-Request returns just the table fragment, not the full HTML document."""
    # When the request carries the htmx header
    response = app_client.get("/receipts", headers={"HX-Request": "true"})

    # Then the table fragment comes back without the page chrome
    assert response.status_code == 200
    assert 'id="receipt-table"' in response.text
    assert "<!doctype html>" not in response.text.lower()


def test_receipt_list_full_page_without_hx_header(app_client) -> None:
    """Verify a normal request returns the full page (no-JS fallback path)."""
    # When the request has no htmx header
    response = app_client.get("/receipts")

    # Then the full document is rendered and contains the sortable table
    assert response.status_code == 200
    assert "<!doctype html>" in response.text.lower()
    assert 'id="receipt-table"' in response.text


def test_receipt_list_sort_preserves_status_filter(app_client) -> None:
    """Verify sorting keeps the status filter and the header links carry it forward."""
    # When sorting a status-filtered list
    response = app_client.get(
        "/receipts", params={"status": "needs_review", "sort": "total", "direction": "asc"}
    )

    # Then only needs_review rows show and the header sort links keep status=needs_review
    assert response.status_code == 200
    assert "parsed" not in statuses_in_order(response.text)
    assert "status=needs_review" in response.text


def test_receipt_list_active_column_has_aria_sort(app_client) -> None:
    """Verify the active sort column exposes aria-sort for assistive tech."""
    # When sorting by total ascending
    response = app_client.get("/receipts", params={"sort": "total", "direction": "asc"})

    # Then the active header carries aria-sort=ascending
    assert response.status_code == 200
    assert 'aria-sort="ascending"' in response.text


def test_receipt_list_invalid_sort_is_422(app_client) -> None:
    """Verify an unknown sort key is rejected rather than silently sorting wrong."""
    # When passing a bogus sort key
    response = app_client.get("/receipts", params={"sort": "bogus"})

    # Then the request is rejected
    assert response.status_code == 422


def test_dashboard_recent_table_is_sortable(app_client) -> None:
    """Verify the dashboard recent table reorders by total and returns a fragment under htmx."""
    # When sorting the dashboard recent table by total descending via htmx
    response = app_client.get(
        "/", params={"sort": "total", "direction": "desc"}, headers={"HX-Request": "true"}
    )

    # Then a fragment comes back, reordered, with the active aria-sort
    assert response.status_code == 200
    assert "<!doctype html>" not in response.text.lower()
    assert 'id="recent-receipts-table"' in response.text
    assert 'aria-sort="descending"' in response.text
    totals = totals_in_order(response.text)
    assert totals == sorted(totals, reverse=True)
