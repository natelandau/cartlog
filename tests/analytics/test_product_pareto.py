"""Tests for the product Pareto (top-products) analytics query."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from cartlog.analytics.results import ProductParetoMetric
from cartlog.analytics.service import AnalyticsService
from cartlog.db.models import Product, ReceiptStatus, Store
from tests.factories import make_line, make_receipt

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_ranks_products_by_spend_with_share_and_pareto_count(analytics_session: Session):
    """Verify spend ranking, each product's share of the total, grand total, and the 80% count."""
    # When ranking the seeded counted line items by spend
    result = AnalyticsService(analytics_session).product_pareto(metric=ProductParetoMetric.SPEND)

    # Then products come back highest-spend first, excluding the FAILED receipt's 9.99 eggs line
    assert [r.name for r in result.rows] == ["eggs", "apples", "milk", "bananas"]
    assert result.rows[0].value == Decimal("8.70")
    assert result.grand_total == Decimal("14.70")
    assert result.product_total == 4
    # And each row carries its own share of the total (eggs = 8.70 / 14.70), not a running sum
    assert round(result.rows[0].share_pct, 1) == 59.2
    # And with every product shown (no tail dropped), the shares sum to 100
    assert abs(sum(r.share_pct for r in result.rows) - 100.0) < 0.1
    # And eggs+apples reach 79.6% (<80), so milk is needed to cross 80%
    assert result.pareto_count == 3


def test_ranks_products_by_trips(analytics_session: Session):
    """Verify the trips metric ranks by distinct receipts a product appears on."""
    # When ranking by trips
    result = AnalyticsService(analytics_session).product_pareto(metric=ProductParetoMetric.TRIPS)

    # Then eggs (on all three counted receipts) lead with a trip count of 3
    assert result.metric == ProductParetoMetric.TRIPS
    assert result.rows[0].name == "eggs"
    assert result.rows[0].value == Decimal(3)
    assert result.grand_total == Decimal(6)
    # And total_receipts is the real trip count (3 distinct receipts), not the summed
    # per-product trip counts that make up grand_total (6)
    assert result.total_receipts == 3


def test_truncates_to_the_top_limit_without_an_other_band(analytics_session: Session):
    """Verify only the top `limit` products are returned; the long tail is dropped, not folded."""
    # When only the top two products are kept
    result = AnalyticsService(analytics_session).product_pareto(
        metric=ProductParetoMetric.SPEND, limit=2
    )

    # Then just the top two rows come back (no synthetic "Other" band)
    assert [r.name for r in result.rows] == ["eggs", "apples"]
    # And product_total / pareto_count still describe the full ranking behind the chart
    assert result.product_total == 4
    assert result.pareto_count == 3


def test_ties_break_deterministically_by_name(analytics_session: Session):
    """Verify products tied on the metric resolve by name, so the ranking is stable."""
    # When ranking by trips, where apples, bananas, and milk each appear on exactly one receipt
    result = AnalyticsService(analytics_session).product_pareto(metric=ProductParetoMetric.TRIPS)

    # Then the three tied products resolve alphabetically rather than by arbitrary SQL row order
    assert [r.name for r in result.rows] == ["eggs", "apples", "bananas", "milk"]


def test_negative_line_total_keeps_share_within_bounds(session: Session):
    """Verify a refund/coupon line (negative line_total) never pushes a product's share over 100."""
    # Given a receipt with two full-price items and one negative coupon line
    store = Store(chain_name="Store", location=None)
    apple = Product(canonical_name="apple")
    bread = Product(canonical_name="bread")
    coupon = Product(canonical_name="coupon")
    receipt = make_receipt(
        store,
        date(2026, 5, 1),
        ReceiptStatus.PARSED,
        [
            make_line(apple, raw="APPLE", qty="1", unit_price="100.00", line_total="100.00"),
            make_line(bread, raw="BREAD", qty="1", unit_price="100.00", line_total="100.00"),
            make_line(coupon, raw="COUPON", qty="1", unit_price="-50.00", line_total="-50.00"),
        ],
    )
    session.add_all([store, receipt])
    session.commit()

    # When ranking by spend (grand total 150 with the coupon sorted last)
    result = AnalyticsService(session).product_pareto(metric=ProductParetoMetric.SPEND)

    # Then every product's share stays within 0..100 even though the coupon shrinks the total
    assert result.grand_total == Decimal("150.00")
    assert all(0.0 <= r.share_pct <= 100.0 for r in result.rows)


def test_store_filter_restricts_the_ranking(analytics_session: Session):
    """Verify a store filter limits the ranking to that store's line items."""
    svc = AnalyticsService(analytics_session)
    costco_id = next(s.id for s in svc.stores_by_frequency() if s.chain_name == "Costco")

    # When ranking only Costco's receipt (eggs 2.50, apples 3.00)
    result = svc.product_pareto(metric=ProductParetoMetric.SPEND, store_id=costco_id)

    # Then apples outrank eggs and nothing from the other stores appears
    assert [r.name for r in result.rows] == ["apples", "eggs"]
    assert result.grand_total == Decimal("5.50")
    # And total_receipts respects the store filter: Costco's single counted receipt
    assert result.total_receipts == 1


def test_category_filter_restricts_the_ranking(analytics_session: Session):
    """Verify a category filter keeps only products in the selected categories."""
    svc = AnalyticsService(analytics_session)
    produce_id = next(
        cid for cid, name in svc.product_pareto().category_options if name == "produce"
    )

    # When ranking produce only (apples 3.00, bananas 1.00)
    result = svc.product_pareto(metric=ProductParetoMetric.SPEND, category_ids=[produce_id])

    # Then only produce products remain
    assert [r.name for r in result.rows] == ["apples", "bananas"]
    assert result.grand_total == Decimal("4.00")


def test_empty_range_returns_no_rows(analytics_session: Session):
    """Verify a range with no counted line items yields an empty, well-formed result."""
    # When the date window predates every receipt
    result = AnalyticsService(analytics_session).product_pareto(
        metric=ProductParetoMetric.SPEND, end=date(2020, 1, 1)
    )

    # Then there are no rows and the totals are zero rather than raising
    assert result.rows == []
    assert result.product_total == 0
    assert result.pareto_count == 0
    assert result.grand_total == Decimal(0)
    assert result.total_receipts == 0
