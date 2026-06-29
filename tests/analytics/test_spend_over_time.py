"""Tests for the spend-over-time analytics query."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cartlog.analytics.results import SpendGranularity, SpendSeries
from cartlog.analytics.service import AnalyticsService
from cartlog.db.models import Category, Product, ReceiptStatus, Store
from tests.factories import make_line, make_receipt


@pytest.fixture
def service(analytics_session):
    """Build an AnalyticsService over the shared seeded receipt dataset."""
    return AnalyticsService(analytics_session)


def test_spend_over_time_buckets_monthly_spend(service):
    """Verify monthly buckets sum itemized spend and exclude the failed receipt."""
    # When bucketing all counted spend by month
    result = service.spend_over_time(granularity=SpendGranularity.MONTHLY)

    # Then there is one bucket per counted month (Jan, Feb, Mar 2026), gap-free
    labels = [b.label for b in result.buckets]
    assert labels == ["Jan 2026", "Feb 2026", "Mar 2026"]
    totals = {b.label: b.total for b in result.buckets}
    # Jan: eggs 3.00 + bananas 1.00; Feb: eggs 2.50 + apples 3.00; Mar: eggs 3.20 + milk 2.00
    assert totals["Jan 2026"] == Decimal("4.00")
    assert totals["Feb 2026"] == Decimal("5.50")
    assert totals["Mar 2026"] == Decimal("5.20")
    # And the failed r4 (eggs 9.99) is excluded from the all-time total
    assert result.total_spend == Decimal("14.70")


def test_spend_over_time_fills_empty_buckets_with_zero(service):
    """Verify a month with no spend is kept as a zero bucket, not skipped."""
    # Given a range spanning a gap (no April spend) up to May
    result = service.spend_over_time(
        start=date(2026, 1, 1), end=date(2026, 5, 31), granularity=SpendGranularity.MONTHLY
    )

    # Then every month in the span is present, including the empty April and May
    labels = [b.label for b in result.buckets]
    assert labels == ["Jan 2026", "Feb 2026", "Mar 2026", "Apr 2026", "May 2026"]
    empty = next(b for b in result.buckets if b.label == "Apr 2026")
    assert empty.total == Decimal(0)
    assert empty.trips == 0
    assert empty.avg_basket == Decimal(0)


def test_spend_over_time_trips_and_avg_basket(service):
    """Verify each bucket reports distinct receipt count and average basket."""
    # When bucketing monthly
    result = service.spend_over_time(granularity=SpendGranularity.MONTHLY)

    # Then each seeded month had exactly one trip, so avg basket equals that month's total
    jan = next(b for b in result.buckets if b.label == "Jan 2026")
    assert jan.trips == 1
    assert jan.avg_basket == Decimal("4.00")


def test_spend_over_time_store_filter_restricts_to_one_store(service):
    """Verify the store filter narrows spend to that store's receipts."""
    # Given Costco's store id
    costco_id = next(s.id for s in service.stores_by_frequency() if s.chain_name == "Costco")

    # When filtering to Costco
    result = service.spend_over_time(store_id=costco_id, granularity=SpendGranularity.MONTHLY)

    # Then only Costco's single February receipt remains
    assert [b.label for b in result.buckets] == ["Feb 2026"]
    assert result.total_spend == Decimal("5.50")
    assert result.store_label == "Costco, Airport Rd"


def test_spend_over_time_category_filter_restricts_spend(service):
    """Verify the category filter keeps only line items in the chosen categories."""
    # Given the produce category id
    produce_id = next(
        cid for cid, name in service.spend_over_time().category_options if name == "produce"
    )

    # When filtering to produce
    result = service.spend_over_time(category_ids=[produce_id])

    # Then only produce line items count: bananas 1.00 (Jan) + apples 3.00 (Feb)
    assert result.total_spend == Decimal("4.00")
    totals = {b.label: b.total for b in result.buckets}
    assert totals["Jan 2026"] == Decimal("1.00")
    assert totals["Feb 2026"] == Decimal("3.00")


def test_spend_over_time_category_options_ignore_active_filter(service):
    """Verify the toolbar's category options list every category, not just the filtered one."""
    # Given a filter narrowed to produce
    produce_id = next(
        cid for cid, name in service.spend_over_time().category_options if name == "produce"
    )

    # When requesting spend filtered to produce
    result = service.spend_over_time(category_ids=[produce_id])

    # Then the option list still offers both seeded categories so the user can change the pick
    names = sorted(name for _, name in result.category_options)
    assert names == ["dairy", "produce"]


def test_spend_over_time_by_category_stacks_each_category(service):
    """Verify the by-category series returns one aligned value vector per category."""
    # When requesting the by-category series
    result = service.spend_over_time(series=SpendSeries.BY_CATEGORY)

    # Then there is one series per real category, each aligned to the three monthly buckets
    by_name = {s.category: s.values for s in result.category_series}
    assert set(by_name) == {"dairy", "produce"}
    assert len(by_name["dairy"]) == len(result.buckets) == 3
    # dairy = eggs+milk: Jan 3.00, Feb 2.50, Mar 5.20
    assert by_name["dairy"] == [Decimal("3.00"), Decimal("2.50"), Decimal("5.20")]
    # produce = bananas+apples: Jan 1.00, Feb 3.00, Mar 0
    assert by_name["produce"] == [Decimal("1.00"), Decimal("3.00"), Decimal(0)]
    assert result.other_category_count == 0


def test_spend_over_time_weekly_granularity_labels_by_day(service):
    """Verify weekly buckets start on Monday and label by month, day, and year."""
    # When bucketing weekly
    result = service.spend_over_time(granularity=SpendGranularity.WEEKLY)

    # Then the first bucket is the Monday of the Jan 15 2026 (a Thursday) receipt's week, and the
    # label carries the year so weeks in different years never collide on the categorical axis
    assert result.buckets[0].start == date(2026, 1, 12)
    assert result.buckets[0].label == "Jan 12, 2026"


def test_spend_over_time_yearly_collapses_to_one_bucket_per_year(service):
    """Verify yearly buckets sum the whole year and label by year."""
    # When bucketing yearly (the seeded data is all within 2026)
    result = service.spend_over_time(granularity=SpendGranularity.YEARLY)

    # Then a single 2026 bucket holds the whole year's itemized spend
    assert [b.label for b in result.buckets] == ["2026"]
    assert result.buckets[0].start == date(2026, 1, 1)
    assert result.buckets[0].total == Decimal("14.70")


def test_spend_over_time_empty_when_no_counted_spend(session):
    """Verify an empty dataset yields no buckets rather than raising."""
    # Given a service over an empty database
    result = AnalyticsService(session).spend_over_time()

    # Then there are no buckets and the total is zero
    assert result.buckets == []
    assert result.total_spend == Decimal(0)


def test_spend_over_time_weekly_labels_distinct_across_years(session):
    """Verify weekly buckets in different years never share a label (no categorical-axis merge)."""
    # Given two purchases on Mondays that share a month-and-day five years apart (both Jan 6)
    dairy = Category(name="dairy")
    milk = Product(canonical_name="milk", category=dairy)
    store = Store(chain_name="Store", location=None)
    old = make_receipt(
        store,
        date(2020, 1, 6),
        ReceiptStatus.PARSED,
        [make_line(milk, raw="MILK", qty="1", unit_price="2.00", line_total="2.00")],
    )
    new = make_receipt(
        store,
        date(2025, 1, 6),
        ReceiptStatus.PARSED,
        [make_line(milk, raw="MILK", qty="1", unit_price="3.00", line_total="3.00")],
    )
    session.add_all([dairy, store, old, new])
    session.commit()

    # When bucketing weekly across the multi-year span
    result = AnalyticsService(session).spend_over_time(granularity=SpendGranularity.WEEKLY)

    # Then the two same-month-day weeks keep distinct, year-qualified labels and stay separate
    labels = [b.label for b in result.buckets]
    assert "Jan 6, 2020" in labels
    assert "Jan 6, 2025" in labels
    assert len(set(labels)) == len(labels)


def test_spend_over_time_reports_uncategorized_spend(session):
    """Verify uncategorized spend is surfaced and excluded from the category stack."""
    # Given a receipt with one categorized line and one uncategorized line
    produce = Category(name="produce")
    apples = Product(canonical_name="apples", category=produce)
    mystery = Product(canonical_name="mystery", category=None)
    store = Store(chain_name="Store", location=None)
    receipt = make_receipt(
        store,
        date(2026, 1, 6),
        ReceiptStatus.PARSED,
        [
            make_line(apples, raw="APPLES", qty="1", unit_price="3.00", line_total="3.00"),
            make_line(mystery, raw="???", qty="1", unit_price="5.00", line_total="5.00"),
        ],
    )
    session.add_all([produce, store, receipt])
    session.commit()

    # When requesting the by-category series
    result = AnalyticsService(session).spend_over_time(series=SpendSeries.BY_CATEGORY)

    # Then the headline total counts both, the uncategorized spend is surfaced, and the stack omits it
    assert result.total_spend == Decimal("8.00")
    assert result.uncategorized_spend == Decimal("5.00")
    assert {s.category for s in result.category_series} == {"produce"}
