"""Tests for the raw line-item export query and serializer."""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from decimal import Decimal

from cartlog.analytics.export import _FIELDS, ExportFormat, export_filename, render_export
from cartlog.analytics.results import LineItemExportRow
from cartlog.analytics.service import AnalyticsService
from cartlog.db.models import Product, ReceiptStatus, Store
from tests.factories import make_line, make_receipt


def test_export_line_items_includes_all_statuses(analytics_session):
    """Verify the export returns every line item, including failed receipts."""
    # Given the seeded analytics dataset (7 line items, one on a FAILED receipt)
    service = AnalyticsService(analytics_session)

    # When exporting with no filters
    rows = service.export_line_items()

    # Then all 7 line items are present and the failed receipt's line is included
    assert len(rows) == 7
    assert any(r.receipt_status == ReceiptStatus.FAILED for r in rows)
    # And rows are ordered oldest purchase first
    assert rows[0].purchase_date == date(2026, 1, 15)


def test_export_line_items_store_filter(analytics_session):
    """Verify the store filter narrows rows case-insensitively."""
    # Given the seeded dataset
    service = AnalyticsService(analytics_session)

    # When filtering to Safeway with mixed case
    rows = service.export_line_items(store="safeway")

    # Then only Safeway's line items (r1, r3, r4) come back
    assert len(rows) == 5
    assert {r.store_chain for r in rows} == {"Safeway"}


def test_export_line_items_category_filter(analytics_session):
    """Verify the category filter narrows rows case-insensitively."""
    # Given the seeded dataset
    service = AnalyticsService(analytics_session)

    # When filtering to the dairy category
    rows = service.export_line_items(category="DAIRY")

    # Then only eggs/milk line items come back
    assert len(rows) == 5
    assert {r.canonical_name for r in rows} == {"eggs", "milk"}


def test_export_line_items_date_filter(analytics_session):
    """Verify the inclusive date range filters by purchase date."""
    # Given the seeded dataset
    service = AnalyticsService(analytics_session)

    # When filtering from February 2026 onward
    rows = service.export_line_items(start=date(2026, 2, 1))

    # Then only r2, r3, r4 line items remain
    assert len(rows) == 5
    assert min(r.purchase_date for r in rows) == date(2026, 2, 10)


def test_export_line_items_uncategorized_product_has_none_category(session):
    """Verify products with no category export with category=None via the OUTER join."""
    # Given a store and a product with no category
    store = Store(chain_name="TestMart", location=None)
    product = Product(canonical_name="mystery item", category=None)
    receipt = make_receipt(
        store,
        date(2026, 4, 1),
        ReceiptStatus.PARSED,
        [make_line(product, raw="MYSTERY ITEM", qty="1", unit_price="5.00", line_total="5.00")],
    )
    session.add_all([store, product, receipt])
    session.commit()

    # When exporting all line items
    service = AnalyticsService(session)
    rows = service.export_line_items()

    # Then the exported row carries category=None
    assert len(rows) == 1
    assert rows[0].canonical_name == "mystery item"
    assert rows[0].category is None


def _sample_row() -> LineItemExportRow:
    return LineItemExportRow(
        purchase_date=date(2026, 1, 15),
        store_chain="Safeway",
        store_location="Main St",
        receipt_id=1,
        receipt_status="parsed",
        currency="USD",
        raw_description="LRG EGGS 12CT",
        canonical_name="eggs",
        category="dairy",
        quantity=Decimal(1),
        unit=None,
        unit_size="12CT",
        unit_price=Decimal("3.00"),
        line_total=Decimal("3.00"),
        measure_quantity=None,
        measure_dimension=None,
        normalized_unit_price=None,
        measure_status="not_applicable",
    )


def test_render_export_csv_has_header_and_rows():
    """Verify CSV output carries a header plus one row per item, with None as blank."""
    # Given one export row
    rows = [_sample_row()]

    # When rendering to CSV
    content, media_type, ext = render_export(rows, ExportFormat.CSV)

    # Then the header and a single data row are present
    parsed = list(csv.DictReader(io.StringIO(content)))
    assert media_type == "text/csv"
    assert ext == "csv"
    assert len(parsed) == 1
    assert parsed[0]["canonical_name"] == "eggs"
    assert parsed[0]["unit_price"] == "3.00"  # Decimal preserved as string
    assert parsed[0]["unit"] == ""  # None rendered blank
    assert parsed[0]["quantity"] == "1"


def test_render_export_json_shape():
    """Verify JSON output is a list of objects with Decimals as strings."""
    # Given one export row
    rows = [_sample_row()]

    # When rendering to JSON
    content, media_type, ext = render_export(rows, ExportFormat.JSON)

    # Then the payload parses to one object with string decimals
    payload = json.loads(content)
    assert media_type == "application/json"
    assert ext == "json"
    assert len(payload) == 1
    assert payload[0]["unit_price"] == "3.00"
    assert payload[0]["unit"] is None
    assert payload[0]["quantity"] == "1"


def test_render_export_empty_is_valid():
    """Verify an empty result still yields a valid CSV header and a JSON empty list."""
    # Given no rows
    csv_content, _, _ = render_export([], ExportFormat.CSV)
    json_content, _, _ = render_export([], ExportFormat.JSON)

    # Then the CSV is a header line only and the JSON is an empty array
    assert csv_content.splitlines()[0] == ",".join(_FIELDS)
    assert len(list(csv.DictReader(io.StringIO(csv_content)))) == 0
    assert json.loads(json_content) == []


def test_export_filename_is_dated():
    """Verify the filename embeds the date and format extension."""
    # Given a fixed date
    name = export_filename(ExportFormat.CSV, date(2026, 6, 18))

    # Then the filename is dated with the right extension
    assert name == "cartlog-export-2026-06-18.csv"
    assert export_filename(ExportFormat.JSON, date(2026, 6, 18)) == "cartlog-export-2026-06-18.json"
