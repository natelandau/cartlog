"""Tests for the size-extraction sweep with a stub extractor (no network)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cartlog.db.backfill import normalize_existing_measures
from cartlog.db.models import LineItem, Product, Receipt, ReceiptStatus, Store
from cartlog.parsing.size_extractor import ParsedSize
from cartlog.sizes.extract import extract_sizes_for_lines
from cartlog.units import MeasureSource, MeasureStatus


class _StubExtractor:
    """Returns a fixed size for any line whose description contains 'granola', else declines."""

    def __init__(self):
        self.calls = 0

    def extract(self, lines, *, usage=None):
        self.calls += 1
        out = {}
        for line in lines:
            if "granola" in line.raw_description.lower():
                out[line.key] = ParsedSize(value=11.0, unit="oz")
            else:
                out[line.key] = None
        return out


class _RoundSizeExtractor:
    """Returns a round-number ParsedSize (330.0 ml) for any line whose description contains 'soda'."""

    def extract(self, lines, *, usage=None):
        out = {}
        for line in lines:
            if "soda" in line.raw_description.lower():
                out[line.key] = ParsedSize(value=330.0, unit="ml")
            else:
                out[line.key] = None
        return out


@pytest.fixture
def receipt(session) -> Receipt:
    """Return a minimal persisted receipt that line items can be attached to."""
    store = Store(chain_name="TestMart", location=None)
    r = Receipt(
        store=store,
        purchase_date=date(2026, 1, 1),
        total=Decimal("10.00"),
        currency="USD",
        image_path="/tmp/test.png",  # noqa: S108
        raw_parser_json="{}",
        source="test",
        status=ReceiptStatus.PARSED,
    )
    session.add(r)
    session.flush()
    return r


def _blank_line(receipt, product, desc):
    return LineItem(
        receipt=receipt,
        product=product,
        raw_description=desc,
        quantity=Decimal(1),
        unit_price=Decimal(1),
        line_total=Decimal("5.00"),
        measure_status=MeasureStatus.NOT_APPLICABLE,
        measure_source=MeasureSource.NONE,
    )


def test_sweep_resolves_hits_and_increments_attempts(session, receipt):
    """Verify hits are resolved and attempts are incremented on both hits and misses."""
    product = Product(canonical_name="granola")
    hit = _blank_line(receipt, product, "Granola 11oz Bob's")
    miss = _blank_line(receipt, Product(canonical_name="apple"), "A Single Apple")
    session.add_all([product, hit, miss])
    session.flush()

    resolved = extract_sizes_for_lines(session, [hit, miss], _StubExtractor(), max_attempts=2)

    assert resolved == 1
    assert hit.measure_status == MeasureStatus.RESOLVED
    assert hit.measure_source == MeasureSource.EXTRACTED
    # The recovered size is persisted into structured columns (size_amount + size_unit).
    assert hit.size_amount == Decimal(11)
    assert hit.size_unit == "oz"
    assert hit.measure_quantity is not None
    assert hit.normalized_unit_price is not None
    assert hit.size_extract_attempts == 1  # attempt spent even on the hit
    assert miss.size_extract_attempts == 1  # attempt spent on the miss too


def test_sweep_skips_lines_at_attempt_cap(session, receipt):
    """Verify lines at the attempt cap are skipped and the extractor is never called."""
    product = Product(canonical_name="granola")
    capped = _blank_line(receipt, product, "Granola 11oz Bob's")
    capped.size_extract_attempts = 2
    session.add_all([product, capped])
    session.flush()
    extractor = _StubExtractor()

    resolved = extract_sizes_for_lines(session, [capped], extractor, max_attempts=2)

    assert resolved == 0
    assert extractor.calls == 0  # capped line never reaches the model
    assert capped.measure_status == MeasureStatus.NOT_APPLICABLE


def test_round_number_size_survives_backfill(session, receipt):
    """Verify a round LLM-extracted size (330ml) stays RESOLVED after a normalize_existing_measures pass.

    With the structured model, sizes are stored as (size_amount, size_unit) Decimal + token
    columns -- not as free text -- so there is no longer a scientific-notation formatting bug.
    This test verifies the structured columns are persisted correctly and the backfill pass 1
    re-derives the same RESOLVED/EXTRACTED state from them.
    """
    # Given a soda line with no structured size (the LLM must recover the size)
    product = Product(canonical_name="sparkling water")
    line = _blank_line(receipt, product, "Soda 330ml Can")
    session.add_all([product, line])
    session.flush()

    # When the LLM sweep resolves it with a round size value
    resolved = extract_sizes_for_lines(session, [line], _RoundSizeExtractor(), max_attempts=3)

    # Then the sweep marked it resolved and persisted structured size columns
    assert resolved == 1
    assert line.measure_status == MeasureStatus.RESOLVED
    assert line.size_amount == Decimal(330)
    assert line.size_unit == "ml"

    # When a startup backfill re-resolves the line
    session.commit()
    normalize_existing_measures(session, size_extractor=None)
    session.refresh(line)

    # Then the line is still RESOLVED as EXTRACTED (not downgraded by the backfill)
    assert line.measure_status == MeasureStatus.RESOLVED
    assert line.measure_source == MeasureSource.EXTRACTED
