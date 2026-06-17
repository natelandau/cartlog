"""Tests for the search view's sortable columns and inline item editing."""

from __future__ import annotations

from cartlog.db.models import Category, LineItem, Product
from tests.web.helpers import first_line_item_id, unit_prices_in_order


def test_search_results_sorts_by_unit_price_ascending(app_client) -> None:
    """Verify ?sort=unit_price&direction=asc orders results by unit price ascending."""
    # When searching, sorted by unit price ascending
    response = app_client.get(
        "/search/results", params={"q": "eggs", "sort": "unit_price", "direction": "asc"}
    )

    # Then the rendered unit-price cells are non-decreasing
    assert response.status_code == 200
    prices = unit_prices_in_order(response.text)
    assert prices == sorted(prices)


def test_search_results_invalid_sort_is_422(app_client) -> None:
    """Verify an unknown sort key is rejected rather than silently mislabeling the sort."""
    # When passing a bogus sort key
    response = app_client.get("/search/results", params={"q": "eggs", "sort": "bogus"})

    # Then the request is rejected
    assert response.status_code == 422


def test_search_results_active_column_has_aria_sort(app_client) -> None:
    """Verify the active sort column exposes aria-sort for assistive tech."""
    # When sorting by unit price ascending
    response = app_client.get(
        "/search/results", params={"q": "eggs", "sort": "unit_price", "direction": "asc"}
    )

    # Then the active header carries aria-sort=ascending
    assert response.status_code == 200
    assert 'aria-sort="ascending"' in response.text


def test_search_item_edit_renders_controls(app_client) -> None:
    """Verify the edit fragment renders the product datalist input and category picker."""
    # Given a known eggs line
    line_id = first_line_item_id(app_client)

    # When requesting its edit row
    response = app_client.get(f"/search/items/{line_id}/edit")

    # Then it contains the canonical_name input (datalist) and the category select
    assert response.status_code == 200
    assert 'name="canonical_name"' in response.text
    assert 'list="search-products"' in response.text
    assert 'name="category_id"' in response.text


def test_search_item_edit_unknown_id_404(app_client) -> None:
    """Verify editing a non-existent line returns 404."""
    # When requesting an edit row for an unknown id
    response = app_client.get("/search/items/999999/edit")

    # Then it is a 404
    assert response.status_code == 404


def test_search_item_save_reassigns_product(app_client) -> None:
    """Verify saving a new canonical name repoints the line and returns the read-only row."""
    # Given a known eggs line
    line_id = first_line_item_id(app_client)

    # When saving a brand-new product name with no category change
    response = app_client.post(
        f"/search/items/{line_id}", data={"canonical_name": "duck eggs", "category_id": ""}
    )

    # Then a read-only row comes back showing the new product, and the DB reflects it
    assert response.status_code == 200
    assert "duck eggs" in response.text
    assert f'id="search-row-{line_id}"' in response.text
    factory = app_client.app.state.session_factory
    with factory() as session:
        assert session.get(LineItem, line_id).product.canonical_name == "duck eggs"


def test_search_item_save_recategorizes_shared_product(app_client) -> None:
    """Verify saving a category_id writes back to the shared product."""
    # Given a known eggs line and the 'produce' category id
    line_id = first_line_item_id(app_client)
    factory = app_client.app.state.session_factory
    with factory() as session:
        produce_id = session.query(Category).filter_by(name="produce").one().id

    # When saving the eggs line's category as produce (name unchanged)
    response = app_client.post(
        f"/search/items/{line_id}",
        data={"canonical_name": "eggs", "category_id": str(produce_id)},
    )

    # Then the shared eggs product is now produce
    assert response.status_code == 200
    with factory() as session:
        product = session.query(Product).filter_by(canonical_name="eggs").one()
        assert product.category.name == "produce"


def test_search_item_save_blank_name_returns_422_edit_row(app_client) -> None:
    """Verify a blank product name re-renders the edit row with an error, not a crash."""
    # Given a known eggs line
    line_id = first_line_item_id(app_client)

    # When saving with an empty canonical name
    response = app_client.post(
        f"/search/items/{line_id}", data={"canonical_name": "  ", "category_id": ""}
    )

    # Then the edit row comes back with a 422 and a visible error message
    assert response.status_code == 422
    assert 'name="canonical_name"' in response.text
    assert 'role="alert"' in response.text
    assert "Product name is required" in response.text


def test_search_item_save_non_numeric_category_returns_422(app_client) -> None:
    """Verify a non-numeric category_id surfaces as an inline 422, not a 500."""
    # Given a known eggs line
    line_id = first_line_item_id(app_client)

    # When posting a tampered, non-numeric category_id
    response = app_client.post(
        f"/search/items/{line_id}", data={"canonical_name": "eggs", "category_id": "abc"}
    )

    # Then the edit row comes back with a 422 and an error message
    assert response.status_code == 422
    assert 'role="alert"' in response.text


def test_search_item_save_unknown_id_404(app_client) -> None:
    """Verify saving against a non-existent line returns 404, not a crash."""
    # When posting an edit for an unknown id
    response = app_client.post(
        "/search/items/999999", data={"canonical_name": "eggs", "category_id": ""}
    )

    # Then it is a 404
    assert response.status_code == 404


def test_search_item_cancel_returns_readonly_row(app_client) -> None:
    """Verify the cancel endpoint returns the read-only row for a line."""
    # Given a known eggs line
    line_id = first_line_item_id(app_client)

    # When requesting the read-only row
    response = app_client.get(f"/search/items/{line_id}")

    # Then a read-only row (with an Edit button) comes back
    assert response.status_code == 200
    assert f'id="search-row-{line_id}"' in response.text
    assert f"/search/items/{line_id}/edit" in response.text
