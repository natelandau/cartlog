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

# The format_normalized contract (imperial/metric conversion, n/a fallback) is unit-tested in
# test_units_display.py; these tests exercise the HTTP rendering of that value end to end.

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
            sold_by="item",
            size_amount=Decimal("1.5"),
            size_unit="L",
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


@pytest.fixture
def receipt_with_count_line(app_client) -> int:
    """Seed one receipt with a size-less count line whose quantity round-trips as 2.000.

    A count sale carries no per-item size, so the Qty cell falls back to the plain quantity;
    the Numeric(10,3) column stores Decimal(2) as 2.000, which must render trimmed as "2".
    """
    with app_client.app.state.session_factory() as session:
        produce = Category(name="count_produce")
        grapefruit = Product(canonical_name="count_grapefruit", category=produce)
        store = Store(chain_name="CountMart", location="Test Blvd")
        receipt = Receipt(
            store=store,
            purchase_date=date(2026, 6, 2),
            total=Decimal("5.86"),
            currency="USD",
            image_path="/tmp/count_test.png",  # noqa: S108
            raw_parser_json="{}",
            source="cli",
            status=ReceiptStatus.PARSED,
        )
        line = LineItem(
            product=grapefruit,
            raw_description="2 Grapefruit, OG, Per Count $2.93 each",
            quantity=Decimal(2),
            sold_by="item",
            size_amount=None,
            size_unit=None,
            unit_price=Decimal("2.93"),
            line_total=Decimal("5.86"),
            measure_quantity=Decimal("2.0000"),
            measure_dimension="count",
            normalized_unit_price=Decimal("2.930000"),
            measure_status="resolved",
        )
        receipt.line_items.append(line)
        session.add(receipt)
        session.commit()
        return receipt.id


def test_receipt_detail_trims_whole_quantity(app_client, receipt_with_count_line):
    """Verify a whole count quantity renders as an integer, not a fixed-decimal value."""
    # When loading the detail page for a receipt whose count line has quantity 2
    response = app_client.get(f"/receipts/{receipt_with_count_line}")

    # Then the Qty cell shows "2", never the stored "2.000"
    assert response.status_code == 200
    assert 'data-label="Qty">2</td>' in response.text
    assert "2.000" not in response.text


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
