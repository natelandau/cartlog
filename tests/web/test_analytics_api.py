"""Tests for the read-only JSON analytics endpoints."""

from __future__ import annotations


def test_price_history_endpoint_returns_points(app_client):
    """Verify /api/analytics/price-history returns ordered points and a summary."""
    # When requesting egg price history
    response = app_client.get("/api/analytics/price-history", params={"product": "eggs"})

    # Then it returns the PriceHistory shape with seeded points
    assert response.status_code == 200
    body = response.json()
    assert body["product"] == "eggs"
    assert len(body["points"]) == 3  # parsed + parsed + needs_review (failed excluded)
    assert body["min_unit_price"] is not None


def test_store_comparison_endpoint_groups_by_store(app_client):
    """Verify /api/analytics/store-comparison returns one row per store."""
    # When comparing egg prices across stores
    response = app_client.get("/api/analytics/store-comparison", params={"product": "eggs"})

    # Then both seeded stores appear
    assert response.status_code == 200
    chains = {row["store_chain"] for row in response.json()["rows"]}
    assert chains == {"Safeway", "Costco"}


def test_category_spend_endpoint_full_breakdown(app_client):
    """Verify /api/analytics/category-spend returns a per-category breakdown."""
    # When requesting category spend with no category filter
    response = app_client.get("/api/analytics/category-spend")

    # Then categories are present with a total
    assert response.status_code == 200
    body = response.json()
    assert body["total_spend"] is not None
    assert len(body["rows"]) >= 1


def test_search_endpoint_matches_canonical_name(app_client):
    """Verify /api/analytics/search returns matching line items."""
    # When searching for eggs
    response = app_client.get("/api/analytics/search", params={"q": "eggs"})

    # Then matching results come back
    assert response.status_code == 200
    results = response.json()
    assert any(r["canonical_name"] == "eggs" for r in results)


def test_price_history_unknown_product_is_empty_not_error(app_client):
    """Verify an unmatched product yields an empty-but-valid 200 result."""
    # When requesting a product that does not exist
    response = app_client.get("/api/analytics/price-history", params={"product": "nope"})

    # Then the result is empty, not an error
    assert response.status_code == 200
    body = response.json()
    assert body["points"] == []
    assert body["min_unit_price"] is None


def test_price_history_requires_product(app_client):
    """Verify omitting the required product param is a 422."""
    # When omitting the product query param
    response = app_client.get("/api/analytics/price-history")

    # Then validation fails
    assert response.status_code == 422
