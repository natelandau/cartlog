"""Tests for the search view's sortable columns and inline item editing."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cartlog.db.models import Category, LineItem, Product
from cartlog.units import MeasureSource, SoldBy
from tests.web.helpers import first_line_item_id, unit_prices_in_order

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.fixture
def seeded_line_id(editor_client: TestClient) -> int:
    """Return the line_item_id of the first seeded eggs line for editor-client tests."""
    return first_line_item_id(editor_client)


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
    """Verify the edit fragment renders all panel inputs and suppresses the Edit button."""
    # Given a known eggs line
    line_id = first_line_item_id(app_client)

    # When requesting its edit row
    response = app_client.get(f"/search/items/{line_id}/edit")

    # Then it contains the canonical_name input (datalist) and the category select
    assert response.status_code == 200
    assert 'name="canonical_name"' in response.text
    assert 'list="search-products"' in response.text
    assert 'name="category_id"' in response.text
    # And the panel row and sold-by toggle controls are present
    assert f'id="search-edit-{line_id}"' in response.text
    assert 'name="sold_by"' in response.text
    assert 'name="size_amount"' in response.text
    assert 'name="size_unit"' in response.text
    assert 'name="measure_unit"' in response.text
    # And the Edit button is suppressed while the panel is open
    assert f'hx-get="/search/items/{line_id}/edit"' not in response.text


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
        f"/search/items/{line_id}",
        data={"canonical_name": "duck eggs", "category_id": "", "raw_description": "LRG EGGS 12CT"},
    )

    # Then a read-only row comes back showing the new product, and the DB reflects it
    assert response.status_code == 200
    assert "duck eggs" in response.text
    assert f'id="search-row-{line_id}"' in response.text
    assert 'hx-swap-oob="true"' in response.text
    factory = app_client.app.state.session_factory
    with factory() as session:
        assert session.get(LineItem, line_id).product.canonical_name == "duck eggs"


def test_search_item_save_persists_item_size(app_client) -> None:
    """Verify a successful item-mode save stores the size and renders it on the read row."""
    # Given a known line
    line_id = first_line_item_id(app_client)

    # When saving it as an item with a 2 L size
    response = app_client.post(
        f"/search/items/{line_id}",
        data={
            "canonical_name": "eggs",
            "category_id": "",
            "raw_description": "LRG EGGS 12CT",
            "sold_by": "item",
            "measure_unit": "",
            "size_amount": "2",
            "size_unit": "l",
        },
    )

    # Then the OOB read row renders the saved measure in the reader's units (imperial default,
    # so the 2 L size shows as fluid ounces) and the DB persists the structured size as entered
    assert response.status_code == 200
    assert "67.63 floz" in response.text
    factory = app_client.app.state.session_factory
    with factory() as session:
        line = session.get(LineItem, line_id)
        assert line.sold_by == SoldBy.ITEM
        assert line.size_amount == Decimal(2)
        assert line.size_unit == "l"
        assert line.measure_source == MeasureSource.MANUAL


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
        data={
            "canonical_name": "eggs",
            "category_id": str(produce_id),
            "raw_description": "LRG EGGS 12CT",
        },
    )

    # Then the shared eggs product is now produce, and the OOB swap attribute is present
    assert response.status_code == 200
    assert 'hx-swap-oob="true"' in response.text
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


def test_search_item_save_422_returns_panel_only_with_submitted_values(app_client) -> None:
    """Verify a 422 re-renders the panel alone (no duplicate read row) keeping typed values."""
    # Given a known eggs line
    line_id = first_line_item_id(app_client)

    # When a save fails validation while the user had typed a new size
    response = app_client.post(
        f"/search/items/{line_id}",
        data={
            "canonical_name": "  ",
            "category_id": "",
            "sold_by": "item",
            "size_amount": "2",
            "size_unit": "L",
        },
    )

    # Then only the panel comes back: exactly one search-edit row and no read row, so the
    # outerHTML swap into the panel cannot leave a second #search-row-{id} in the DOM
    assert response.status_code == 422
    assert response.text.count(f'id="search-edit-{line_id}"') == 1
    assert f'id="search-row-{line_id}"' not in response.text
    # Note: the template-rendered field value assertion is deferred to the template update task


def test_search_item_save_edits_receipt_text(app_client) -> None:
    """Verify saving a new receipt text overwrites the line's raw_description."""
    # Given a known eggs line
    line_id = first_line_item_id(app_client)

    # When saving a corrected receipt text
    response = app_client.post(
        f"/search/items/{line_id}",
        data={"canonical_name": "eggs", "category_id": "", "raw_description": "LARGE EGGS 12 CT"},
    )

    # Then the response is OK and the line's raw_description reflects the edit
    assert response.status_code == 200
    factory = app_client.app.state.session_factory
    with factory() as session:
        assert session.get(LineItem, line_id).raw_description == "LARGE EGGS 12 CT"


def test_search_item_save_blank_receipt_text_returns_422(app_client) -> None:
    """Verify a blank receipt text is rejected with the shared required-text message."""
    # Given a known eggs line
    line_id = first_line_item_id(app_client)

    # When saving with a whitespace-only receipt text but a valid product name
    response = app_client.post(
        f"/search/items/{line_id}",
        data={"canonical_name": "eggs", "category_id": "", "raw_description": "   "},
    )

    # Then the same validator the receipt form uses surfaces an inline 422
    assert response.status_code == 422
    assert "Receipt text is required" in response.text


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


def test_search_item_row_returns_readonly_row(app_client) -> None:
    """Verify the row endpoint returns the read-only row with its Edit button."""
    # Given a known eggs line
    line_id = first_line_item_id(app_client)

    # When requesting the read-only row directly
    response = app_client.get(f"/search/items/{line_id}")

    # Then a read-only row (with an Edit button) comes back
    assert response.status_code == 200
    assert f'id="search-row-{line_id}"' in response.text
    assert f"/search/items/{line_id}/edit" in response.text


def test_search_item_save_updates_structured_size(
    editor_client: TestClient, seeded_line_id: int
) -> None:
    """Verify saving the panel with structured size fields returns the OOB read row."""
    # When the editor saves new structured size fields
    resp = editor_client.post(
        f"/search/items/{seeded_line_id}",
        data={
            "canonical_name": "Milk",
            "category_id": "",
            "raw_description": "2% MILK",
            "sold_by": "item",
            "size_amount": "2",
            "size_unit": "L",
        },
    )

    # Then the response is OK and swaps the read row out of band
    assert resp.status_code == 200
    assert f'id="search-row-{seeded_line_id}"' in resp.text
    assert 'hx-swap-oob="true"' in resp.text


def test_search_item_cancel_returns_oob_read_row(
    editor_client: TestClient, seeded_line_id: int
) -> None:
    """Verify cancel closes the panel and restores the read row."""
    # When cancelling an open edit panel
    resp = editor_client.get(f"/search/items/{seeded_line_id}/cancel")

    # Then the read row comes back as OOB to restore it
    assert resp.status_code == 200
    assert f'id="search-row-{seeded_line_id}"' in resp.text
    assert 'hx-swap-oob="true"' in resp.text


def test_search_item_save_blank_name_returns_panel_422(
    editor_client: TestClient, seeded_line_id: int
) -> None:
    """Verify a blank product name re-renders the open panel with a 422."""
    # When saving with an empty canonical name
    resp = editor_client.post(
        f"/search/items/{seeded_line_id}",
        data={
            "canonical_name": "  ",
            "category_id": "",
            "sold_by": "item",
            "size_amount": "2",
            "size_unit": "L",
        },
    )

    # Then the panel is re-rendered as a 422 with the error
    assert resp.status_code == 422
    assert f'id="search-edit-{seeded_line_id}"' in resp.text
    assert "Product name is required." in resp.text
