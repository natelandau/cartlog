"""Route-level checks that each insights fragment renders the shared chart frame markup."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def test_price_history_fragment_has_shared_heading(app_client: TestClient) -> None:
    """Verify the price-history fragment renders the shared HTML heading placeholder."""
    # When loading the price-history fragment as an htmx request
    resp = app_client.get("/insights/price-history", headers={"HX-Request": "true"})

    # Then the fragment renders with the shared heading element the JS renderer fills after fetch
    assert resp.status_code == 200
    assert 'class="font-display' in resp.text
    assert 'id="ph-title"' in resp.text


def test_category_spend_fragment_has_shared_heading(app_client: TestClient) -> None:
    """Verify the category-spend fragment renders the shared HTML heading."""
    # When loading the category-spend fragment as an htmx request
    resp = app_client.get("/insights/category-spend", headers={"HX-Request": "true"})

    # Then the fragment renders with the shared heading and static title text
    assert resp.status_code == 200
    assert 'class="font-display' in resp.text
    assert "Spend by category" in resp.text


def test_store_comparison_fragment_renders(app_client: TestClient) -> None:
    """Verify the store-comparison fragment returns 200 with either the empty state or the shared heading."""
    # When loading the store-comparison fragment as an htmx request
    resp = app_client.get("/insights/store-comparison", headers={"HX-Request": "true"})

    # Then either the empty-state message or the comparison table with the shared heading is present
    assert resp.status_code == 200
    assert ("Add receipts from at least two stores" in resp.text) or (
        'class="font-display' in resp.text
    )
