"""Tests for the normalize_existing_measures backfill function."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cartlog.db.cli import normalize_existing_measures
from cartlog.db.models import Category, LineItem, Product, Receipt, Store


def _seed(session, *, unit, unit_size):
    product = Product(canonical_name="milk", category=Category(name="dairy"))
    store = Store(chain_name="Safeway", location="Main St")
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 3, 1),
        total=Decimal("4.50"),
        currency="USD",
        image_path="/tmp/x.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status="parsed",
    )
    receipt.line_items.append(
        LineItem(
            product=product,
            raw_description="MILK",
            quantity=Decimal(1),
            unit=unit,
            unit_size=unit_size,
            unit_price=Decimal("4.50"),
            line_total=Decimal("4.50"),
        )
    )
    session.add(receipt)
    session.commit()
    return receipt.line_items[0]


def test_backfill_resolves_from_unit_size(session):
    """Verify backfill computes normalized columns from stored unit_size."""
    # Given a line item with a measurable unit_size but no normalization yet
    line = _seed(session, unit="ea", unit_size="1.5L")

    # When the backfill runs
    updated = normalize_existing_measures(session)
    session.refresh(line)

    # Then the normalization columns are populated and the count is correct
    assert updated == 1
    assert line.measure_status == "resolved"
    assert line.normalized_unit_price == Decimal("0.003000")


def test_backfill_is_idempotent(session):
    """Verify a second backfill run detects no further changes."""
    # Given a line item that has already been normalized
    _seed(session, unit="ea", unit_size="1.5L")
    normalize_existing_measures(session)

    # When the backfill runs a second time
    second = normalize_existing_measures(session)

    # Then no rows are changed
    assert second == 0
