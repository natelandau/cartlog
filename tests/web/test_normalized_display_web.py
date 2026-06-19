"""End-to-end rendering tests for the normalized unit price column.

These tests seed a receipt with a fully resolved normalized line item, then hit
the real HTTP endpoints to confirm the "Norm $/unit" column appears and contains
a formatted value.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cartlog.db.models import Category, LineItem, Product, Receipt, ReceiptStatus, Store
from cartlog.web.units_display import format_normalized

# ---------------------------------------------------------------------------
# Filter contract guard: ensures the template's filter call produces the
# value the rendering assertions look for. If this breaks, the column is
# guaranteed to render wrong before we even touch the HTTP layer.
# ---------------------------------------------------------------------------


def test_format_normalized_volume_imperial_matches_expected_cell():
    """Verify the filter produces the exact string the receipt/search cells will show."""
    # Given a resolved volume line priced at $0.003/ml (milk 1.5 L)
    result = format_normalized(Decimal("0.003000"), "volume", "resolved", "imperial")

    # Then the display value matches the rendered cell
    assert result == "$0.089/fl oz"


def test_format_normalized_not_applicable_returns_na():
    """Verify lines without normalization render 'n/a' rather than crashing."""
    assert format_normalized(None, None, "not_applicable", "imperial") == "n/a"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def receipt_with_normalized(app_client) -> int:
    """Seed one receipt with a resolved volume line item and return its id.

    Adds directly to the shared in-memory DB so the TestClient's routes see
    the new row on the next request.
    """
    with app_client.app.state.session_factory() as session:
        dairy = Category(name="normalized_dairy")
        milk = Product(canonical_name="norm_milk", category=dairy)
        store = Store(chain_name="NormMart", location="Test Ave")
        receipt = Receipt(
            store=store,
            purchase_date=date(2026, 6, 1),
            total=Decimal("4.50"),
            currency="USD",
            image_path="/tmp/norm_test.png",  # noqa: S108
            raw_parser_json="{}",
            source="cli",
            status=ReceiptStatus.PARSED,
        )
        line = LineItem(
            product=milk,
            raw_description="NORM MILK 1.5L",
            quantity=Decimal(1),
            unit="ea",
            unit_size="1.5L",
            unit_price=Decimal("4.50"),
            line_total=Decimal("4.50"),
            # Stored as $/ml; 4.50 / 1500 ml = 0.003 $/ml
            measure_quantity=Decimal("1500.0000"),
            measure_dimension="volume",
            normalized_unit_price=Decimal("0.003000"),
            measure_status="resolved",
        )
        receipt.line_items.append(line)
        session.add(receipt)
        session.commit()
        return receipt.id


# ---------------------------------------------------------------------------
# Receipt detail route
# ---------------------------------------------------------------------------


def test_receipt_detail_shows_normalized_column_header(app_client, receipt_with_normalized):
    """Verify the receipt detail page includes the Norm $/unit header."""
    # When loading the detail page for a receipt with a resolved line
    response = app_client.get(f"/receipts/{receipt_with_normalized}")

    # Then the normalized column header is present
    assert response.status_code == 200
    assert "Norm $/unit" in response.text


def test_receipt_detail_shows_formatted_normalized_value(app_client, receipt_with_normalized):
    """Verify the receipt detail page renders the actual normalized price string."""
    # When loading the detail page
    response = app_client.get(f"/receipts/{receipt_with_normalized}")

    # Then the formatted $/fl oz value appears (default unit system is imperial)
    assert response.status_code == 200
    assert "fl oz" in response.text


def test_receipt_items_partial_shows_units_toggle(app_client, receipt_with_normalized):
    """Verify the items partial includes the unit-system toggle button."""
    # When fetching the items panel partial
    response = app_client.get(f"/receipts/{receipt_with_normalized}/items")

    # Then the toggle button is present
    assert response.status_code == 200
    assert "Units:" in response.text


def test_receipt_items_partial_metric_cookie_renders_metric(app_client, receipt_with_normalized):
    """Verify a metric cookie causes the items panel to render metric values."""
    # When loading the items partial with a metric cookie
    response = app_client.get(
        f"/receipts/{receipt_with_normalized}/items",
        cookies={"unit_system": "metric"},
    )

    # Then the metric suffix appears (100ml rather than fl oz)
    assert response.status_code == 200
    assert "100ml" in response.text


# ---------------------------------------------------------------------------
# Search results route
# ---------------------------------------------------------------------------


def test_search_results_shows_normalized_column_header(app_client, receipt_with_normalized):
    """Verify the search results fragment includes the Norm $/unit header."""
    # When searching for the seeded normalized product
    response = app_client.get("/search/results", params={"q": "NORM MILK"})

    # Then the normalized column header is present
    assert response.status_code == 200
    assert "Norm $/unit" in response.text


def test_search_results_shows_formatted_normalized_value(app_client, receipt_with_normalized):
    """Verify the search results fragment renders the actual normalized price string."""
    # When searching for the seeded normalized product (default imperial)
    response = app_client.get("/search/results", params={"q": "NORM MILK"})

    # Then the formatted $/fl oz value appears
    assert response.status_code == 200
    assert "fl oz" in response.text


def test_search_results_metric_cookie_renders_metric(app_client, receipt_with_normalized):
    """Verify a metric cookie makes search results render metric normalized prices."""
    # When searching with a metric cookie
    response = app_client.get(
        "/search/results",
        params={"q": "NORM MILK"},
        cookies={"unit_system": "metric"},
    )

    # Then the metric suffix appears
    assert response.status_code == 200
    assert "100ml" in response.text


def test_search_item_row_includes_unit_system(app_client, receipt_with_normalized):
    """Verify the single-row endpoint passes unit_system so the cell renders without error."""
    # Given the seeded line item's id
    with app_client.app.state.session_factory() as session:
        from cartlog.db.models import LineItem, Product  # noqa: PLC0415

        line = (
            session.query(LineItem)
            .join(Product, LineItem.product_id == Product.id)
            .filter(Product.canonical_name == "norm_milk")
            .first()
        )
        assert line is not None
        line_id = line.id

    # When fetching the read-only row for that item
    response = app_client.get(f"/search/items/{line_id}")

    # Then it renders successfully with the normalized cell
    assert response.status_code == 200
    assert "fl oz" in response.text
