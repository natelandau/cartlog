# tests/analytics/test_dashboard.py
"""Tests for the dashboard aggregate queries on AnalyticsService."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cartlog.analytics.ranges import RangePreset
from cartlog.analytics.service import AnalyticsService

TODAY = date(2026, 6, 14)


def test_monthly_spend_buckets_counted_receipts_by_month(dashboard_session):
    """Verify monthly spend sums counted receipts per month, excluding failed ones."""
    # Given the dashboard dataset over an all-time window
    svc = AnalyticsService(dashboard_session)

    # When bucketing spend by month
    rows = svc.monthly_spend(start=None, end=None)

    # Then every counted month appears in order and the failed May 25 receipt is excluded
    by_month = {r.month: r for r in rows}
    assert len(rows) == 5  # all five counted months present
    assert by_month["2026-01"].total == Decimal("3.00")  # d1: 2.00 eggs + 1.00 bananas
    assert by_month["2026-05"].total == Decimal("4.10")  # d5 only; d6 (failed) excluded
    assert by_month["2026-05"].receipt_count == 1
    assert [r.month for r in rows] == sorted(r.month for r in rows)


def test_month_comparison_contrasts_two_calendar_months(dashboard_session):
    """Verify month comparison contrasts a month with the one before it."""
    # Given the dashboard dataset, anchored so "this month" is May 2026
    svc = AnalyticsService(dashboard_session)

    # When comparing May (this) to April (prev)
    cmp = svc.month_comparison(today=date(2026, 5, 31))

    # Then trips and spend reflect each month's counted receipts
    assert cmp.trips_this == 1  # d5 in May (d6 failed, excluded)
    assert cmp.trips_prev == 1  # d4 in April
    assert cmp.spend_this == Decimal("4.10")  # d5 total
    assert cmp.spend_prev == Decimal("3.20")  # d4 total


def test_kpis_report_totals_with_prior_period_delta(dashboard_session):
    """Verify KPI cards carry counts/spend and a delta versus the prior window."""
    # Given the dashboard dataset
    svc = AnalyticsService(dashboard_session)

    # When building KPI cards for the trailing 12 months
    kpis = svc.kpis(RangePreset.LAST_12_MONTHS, today=TODAY)

    # Then the receipts KPI carries the counted (non-failed) total
    labels = {k.label: k for k in kpis}
    assert labels["Receipts"].value == "5"  # d1-d5; d6 failed excluded
    assert labels["Items"].value == "8"
    # And spend is preformatted as currency
    assert labels["Total spend"].value.startswith("$")
    # Prior 12-month window is empty so delta is undefined, not zero
    assert labels["Receipts"].delta_pct is None


def test_activity_heatmap_has_one_cell_per_shopping_day(dashboard_session):
    """Verify the heatmap emits a cell per counted shopping day, shaded by spend."""
    # Given the dashboard dataset
    svc = AnalyticsService(dashboard_session)

    # When building the heatmap over all time
    cells = svc.activity_heatmap(start=None, end=None)

    # Then each counted purchase date is present with its day's spend
    spend_by_day = {c.day: c.spend for c in cells}
    assert spend_by_day[date(2026, 1, 10)] == Decimal("3.00")
    assert spend_by_day[date(2026, 5, 9)] == Decimal("4.10")  # d5 only
    assert date(2026, 5, 25) not in spend_by_day  # failed receipt's day excluded


def test_top_products_rank_by_count_and_spend(dashboard_session):
    """Verify top products can be ranked by purchase frequency and by spend."""
    # Given the dashboard dataset
    svc = AnalyticsService(dashboard_session)

    # When ranking by purchase count
    by_count = svc.top_products(start=None, end=None, limit=10, by="count")

    # Then eggs (bought on d1, d2, d3, d5) leads
    assert by_count[0].name == "eggs"
    assert by_count[0].purchase_count == 4

    # And ranking by spend also puts eggs first ($11.00)
    by_spend = svc.top_products(start=None, end=None, limit=10, by="spend")
    assert by_spend[0].name == "eggs"
    assert by_spend[0].total_spend == Decimal("11.00")


def test_store_breakdown_reports_visits_and_avg_per_trip(dashboard_session):
    """Verify the store table reports visits, spend, and average spend per trip."""
    # Given the dashboard dataset
    svc = AnalyticsService(dashboard_session)

    # When breaking spend down by store
    rows = svc.store_breakdown(start=None, end=None)

    # Then each store's visit count and average per trip are present
    by_store = {r.store_chain: r for r in rows}
    assert by_store["Hubmart"].visits == 3  # d1, d3, d5
    assert by_store["Hubmart"].total_spend == Decimal("10.10")
    assert by_store["Hubmart"].avg_per_trip == (
        by_store["Hubmart"].total_spend / by_store["Hubmart"].visits
    )


def test_price_watch_tracks_top_staples_with_trend(dashboard_session):
    """Verify price watch returns frequent staples with a price series and latest price."""
    # Given the dashboard dataset
    svc = AnalyticsService(dashboard_session)

    # When building the price watch for the top 3 staples
    rows = svc.price_watch(start=None, end=None, limit=3)

    # Then eggs (the most-bought product) is tracked with its latest unit price
    by_product = {r.product: r for r in rows}
    assert "eggs" in by_product
    assert by_product["eggs"].current_price == Decimal("3.50")  # d5, the latest egg buy
    assert len(by_product["eggs"].points) == 4  # d1, d2, d3, d5


def test_price_movers_rank_by_first_half_to_second_half_change(dashboard_session):
    """Verify movers compare first-half and second-half average price and rank by change."""
    # Given the dashboard dataset
    svc = AnalyticsService(dashboard_session)

    # When computing movers over all time
    rows = svc.price_movers(start=None, end=None, limit=5)

    # Then every mover has a defined percent change and at least two price points
    assert rows  # eggs/milk/bananas each bought multiple times
    assert all(r.change_pct is not None for r in rows)
    assert all(len(r.points) >= 2 for r in rows)
    # And eggs (steadily rising 2.00 -> 3.50) is the biggest mover
    assert rows[0].product == "eggs"
    top_change = rows[0].change_pct
    assert top_change is not None
    assert top_change > 0


def test_dashboard_assembles_every_section(dashboard_session):
    """Verify dashboard() returns a populated DashboardData for a preset range."""
    # Given the dashboard dataset
    svc = AnalyticsService(dashboard_session)

    # When assembling the dashboard for the trailing 12 months
    data = svc.dashboard(RangePreset.LAST_12_MONTHS, today=TODAY)

    # Then the headline sections are populated and the range is labeled
    assert data.range_label == "Last 12 months"
    assert data.kpis
    assert data.monthly_spend
    assert data.categories  # reuses category_spend
    assert data.stores
    assert data.heatmap
    assert data.needs_review == 1  # d4 is the only needs_review receipt
