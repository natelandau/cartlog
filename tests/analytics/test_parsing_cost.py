"""Tests for parsing-cost analytics."""

from datetime import date, datetime
from decimal import Decimal

from cartlog.analytics.service import AnalyticsService
from cartlog.db.models import ParseCostEvent
from cartlog.ingest.cost import record_size_extract_cost, record_standalone_size_extract_cost


def _event(cost, created):
    return ParseCostEvent(job_id=None, estimated_cost_usd=cost, created_at=created)


def test_parsing_cost_sums_in_range_and_averages(session_factory):
    """Verify parsing_cost totals in-range priced events, ignores nulls, and averages them."""
    # Given three priced events in June and one in May, plus one null-cost event in June
    with session_factory() as session:
        session.add_all(
            [
                _event(Decimal("0.10"), datetime(2026, 6, 2, 9, 0)),  # noqa: DTZ001
                _event(Decimal("0.20"), datetime(2026, 6, 10, 9, 0)),  # noqa: DTZ001
                _event(Decimal("0.30"), datetime(2026, 6, 20, 9, 0)),  # noqa: DTZ001
                _event(Decimal("0.99"), datetime(2026, 5, 31, 9, 0)),  # noqa: DTZ001
                _event(None, datetime(2026, 6, 5, 9, 0)),  # noqa: DTZ001
            ]
        )
        session.commit()

        # When summing June (end exclusive on July 1)
        summary = AnalyticsService(session).parsing_cost(
            start=date(2026, 6, 1), end=date(2026, 7, 1)
        )

    # Then only the three priced June events count
    assert summary.total == Decimal("0.60")
    assert summary.receipt_count == 3
    assert summary.avg_per_receipt == Decimal("0.20")


def test_parsing_cost_empty_range_returns_zero(session_factory):
    """Verify a range with no priced events returns zero without dividing by zero."""
    # Given no cost events
    with session_factory() as session:
        # When summing an empty range
        summary = AnalyticsService(session).parsing_cost(
            start=date(2026, 6, 1), end=date(2026, 7, 1)
        )

    # Then totals are zero
    assert summary.total == Decimal(0)
    assert summary.receipt_count == 0
    assert summary.avg_per_receipt == Decimal(0)


def test_parsing_cost_all_time_omits_bounds(session_factory):
    """Verify parsing_cost with no bounds totals every priced event."""
    # Given events in two different months
    with session_factory() as session:
        session.add_all(
            [
                _event(Decimal("0.40"), datetime(2026, 6, 15, 9, 0)),  # noqa: DTZ001
                _event(Decimal("0.60"), datetime(2026, 5, 15, 9, 0)),  # noqa: DTZ001
            ]
        )
        session.commit()

        # When summing with no range
        summary = AnalyticsService(session).parsing_cost()

    # Then both events count
    assert summary.total == Decimal("1.00")
    assert summary.receipt_count == 2


def test_parsing_cost_overview_splits_all_time_and_last_30_days(session_factory):
    """Verify the overview reports all-time total, rolling 30-day total, and all-time average."""
    # Given one recent event and one older than 30 days, as of 2026-06-18
    with session_factory() as session:
        session.add_all(
            [
                _event(Decimal("0.40"), datetime(2026, 6, 10, 9, 0)),  # noqa: DTZ001  # within 30 days
                _event(Decimal("0.60"), datetime(2026, 4, 1, 9, 0)),  # noqa: DTZ001  # older than 30 days
            ]
        )
        session.commit()

        # When building the overview as of a June date
        overview = AnalyticsService(session).parsing_cost_overview(today=date(2026, 6, 18))

    # Then all-time totals everything, last-30-days only the recent event, avg is all-time
    assert overview.total_all_time == Decimal("1.00")
    assert overview.total_last_30_days == Decimal("0.40")
    assert overview.avg_per_receipt == Decimal("0.50")


def test_record_size_extract_cost_adds_onto_event(session):
    """Verify size-extract cost adds onto an existing event and updates fields."""
    # Given an existing parse cost event
    event = ParseCostEvent(job_id=1, parse_model="m", estimated_cost_usd=Decimal("0.01"))
    session.add(event)
    session.commit()

    # When recording size-extract cost
    record_size_extract_cost(
        session,
        event,
        input_tokens=100,
        output_tokens=20,
        model="anthropic:claude-haiku-4-5",
        cost=Decimal("0.002"),
    )
    session.refresh(event)

    # Then size-extract fields are set and cost is added
    assert event.size_extract_input_tokens == 100
    assert event.size_extract_model == "anthropic:claude-haiku-4-5"
    assert event.estimated_cost_usd == Decimal("0.012")


def test_record_standalone_size_extract_cost_creates_jobless_event(session):
    """Verify standalone size-extract cost creates a job-less event."""
    # When recording standalone size-extract cost
    event = record_standalone_size_extract_cost(
        session, input_tokens=50, output_tokens=10, model="m", cost=Decimal("0.001")
    )

    # Then a job-less event is created with size-extract fields set
    assert event.job_id is None
    assert event.size_extract_output_tokens == 10
    assert event.estimated_cost_usd == Decimal("0.001")
