"""Tests for the unit factor table and free-text size parser in cartlog.units."""

from decimal import Decimal
from enum import StrEnum

import pytest

from cartlog.constants import COUNT, UNIT_FACTORS, VOLUME, WEIGHT
from cartlog.units import (
    MeasureStatus,
    SoldBy,
    compute_measure,
    normalize_unit_token,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("L", "l"),
        (" Lbs ", "lb"),
        ("Ounce", "oz"),
        ("fl oz", "floz"),
        ("CT", "ct"),
        ("each", "each"),
        ("grams", "g"),
        ("litre", "l"),
        ("", None),
        (None, None),
        ("bunch", None),
    ],
)
def test_normalize_unit_token(raw, expected):
    """Verify free-text unit strings map to canonical tokens or None when unrecognized."""
    assert normalize_unit_token(raw) == expected


def test_unit_factors_dimensions():
    """Verify UNIT_FACTORS entries carry the correct dimension and conversion factor."""
    assert UNIT_FACTORS["oz"] == (WEIGHT, Decimal("28.3495"))
    assert UNIT_FACTORS["l"] == (VOLUME, Decimal(1000))
    assert UNIT_FACTORS["ct"] == (COUNT, Decimal(1))


def test_measure_status_is_a_str_enum_with_three_members():
    """Verify MeasureStatus owns the closed measure_status vocabulary as a StrEnum."""
    # Given the MeasureStatus enum
    # Then it is a StrEnum whose members equal their persisted string values
    assert issubclass(MeasureStatus, StrEnum)
    assert MeasureStatus.RESOLVED == "resolved"
    assert MeasureStatus.NOT_APPLICABLE == "not_applicable"
    assert MeasureStatus.NEEDS_REVIEW == "needs_review"
    assert {m.value for m in MeasureStatus} == {"resolved", "not_applicable", "needs_review"}


def test_measure_mode_uses_quantity_as_amount():
    """Verify that MEASURE mode uses quantity as the measure amount."""
    out = compute_measure(
        sold_by=SoldBy.MEASURE,
        quantity=Decimal("1.47"),
        measure_unit="lb",
        size_amount=None,
        size_unit=None,
        line_total=Decimal("7.34"),
    )
    assert out.measure_status == MeasureStatus.RESOLVED
    assert out.measure_dimension == "weight"
    # 1.47 lb converts to 666.78024 g (pre-quantized); 7.34 / 666.78024 yields the price
    assert out.measure_quantity == Decimal("666.7802")


def test_item_mode_with_size_multiplies_quantity_by_size():
    """Verify that ITEM mode with size multiplies quantity by size amount."""
    out = compute_measure(
        sold_by=SoldBy.ITEM,
        quantity=Decimal(2),
        measure_unit=None,
        size_amount=Decimal(16),
        size_unit="oz",
        line_total=Decimal("20.60"),
    )
    assert out.measure_dimension == "weight"
    # 2 * 16 oz = 32 oz -> 907.184 g
    assert out.measure_quantity == Decimal("907.1840")


def test_item_mode_count_size_yields_count_dimension():
    """Verify that ITEM mode with count size yields count dimension."""
    out = compute_measure(
        sold_by=SoldBy.ITEM,
        quantity=Decimal(1),
        measure_unit=None,
        size_amount=Decimal(12),
        size_unit="ct",
        line_total=Decimal("3.00"),
    )
    assert out.measure_dimension == "count"
    assert out.measure_quantity == Decimal("12.0000")
    assert out.normalized_unit_price == Decimal("0.250000")


def test_item_mode_without_size_resolves_to_per_each():
    """Verify that ITEM mode without size resolves to per-each count."""
    out = compute_measure(
        sold_by=SoldBy.ITEM,
        quantity=Decimal(3),
        measure_unit=None,
        size_amount=None,
        size_unit=None,
        line_total=Decimal("1.50"),
    )
    assert out.measure_status == MeasureStatus.RESOLVED
    assert out.measure_dimension == "count"
    assert out.normalized_unit_price == Decimal("0.500000")


def test_item_mode_with_size_amount_but_no_unit_falls_through_to_per_each():
    """Verify that ITEM mode with size_amount but no size_unit falls through to per-each."""
    out = compute_measure(
        sold_by=SoldBy.ITEM,
        quantity=Decimal(2),
        measure_unit=None,
        size_amount=Decimal(16),
        size_unit=None,
        line_total=Decimal("4.00"),
    )
    assert out.measure_status == MeasureStatus.RESOLVED
    assert out.measure_dimension == "count"
    assert out.normalized_unit_price == Decimal("2.000000")


def test_measure_mode_rejects_count_unit():
    """Verify that MEASURE mode rejects count units."""
    out = compute_measure(
        sold_by=SoldBy.MEASURE,
        quantity=Decimal(2),
        measure_unit="ea",
        size_amount=None,
        size_unit=None,
        line_total=Decimal("4.00"),
    )
    assert out.measure_status == MeasureStatus.NEEDS_REVIEW


@pytest.mark.parametrize("qty", [Decimal(0), Decimal(-1)])
def test_nonpositive_measure_needs_review(qty):
    """Verify that non-positive quantities yield NEEDS_REVIEW status."""
    out = compute_measure(
        sold_by=SoldBy.ITEM,
        quantity=qty,
        measure_unit=None,
        size_amount=None,
        size_unit=None,
        line_total=Decimal("1.00"),
    )
    assert out.measure_status == MeasureStatus.NEEDS_REVIEW
