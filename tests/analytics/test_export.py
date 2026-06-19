"""Tests for the raw line-item export query and serializer."""

from __future__ import annotations

from datetime import date

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
