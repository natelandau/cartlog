"""Tests for the unit factor table and free-text size parser in cartlog.units."""

from decimal import Decimal

import pytest

from cartlog.units import (
    COUNT,
    NEEDS_REVIEW,
    NOT_APPLICABLE,
    RESOLVED,
    UNIT_FACTORS,
    VOLUME,
    WEIGHT,
    normalize_line_item,
    normalize_unit_token,
    parse_size,
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.5L", (Decimal("1.5"), "l")),
        ("32 oz", (Decimal(32), "oz")),
        ("12CT", (Decimal(12), "ct")),
        ("6x330ml", (Decimal(1980), "ml")),
        ("750 ml", (Decimal(750), "ml")),
        ("family pack", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_size(text, expected):
    """Verify size strings are parsed into (value, token) pairs, with multipack quantities combined."""
    assert parse_size(text) == expected


def test_loose_weight_quantity_is_the_measure():
    """Verify that loose produce sold by weight uses quantity as the measure in grams."""
    # bananas: 2 lb @ line_total 3.48 -> grams base, $/g
    r = normalize_line_item(
        quantity=Decimal(2), unit="lb", unit_size=None, line_total=Decimal("3.48")
    )
    assert r.measure_status == RESOLVED
    assert r.measure_dimension == "weight"
    assert r.measure_quantity == Decimal("907.1840")
    assert r.normalized_unit_price == Decimal("0.003836")  # 3.48 / 907.184


def test_packaged_volume_from_unit_size():
    """Verify that packaged volume goods resolve measure from unit_size text."""
    # milk: 1 x 1.5L @ 4.50 -> 1500 ml, $/ml
    r = normalize_line_item(
        quantity=Decimal(1), unit="ea", unit_size="1.5L", line_total=Decimal("4.50")
    )
    assert r.measure_status == RESOLVED
    assert r.measure_dimension == "volume"
    assert r.measure_quantity == Decimal("1500.0000")
    assert r.normalized_unit_price == Decimal("0.003000")


def test_llm_measure_takes_precedence_over_unit_size():
    """Verify that the llm_measure overrides a garbled unit_size field."""
    r = normalize_line_item(
        quantity=Decimal(1),
        unit="ea",
        unit_size="garbled",
        line_total=Decimal("2.00"),
        llm_measure=(500, "ml"),
    )
    assert r.measure_dimension == "volume"
    assert r.measure_quantity == Decimal("500.0000")


def test_count_in_unit_size_is_count_dimension():
    """Verify that a count token in unit_size resolves to the count dimension."""
    # eggs: 1 x 12CT @ 3.48 -> $/each
    r = normalize_line_item(
        quantity=Decimal(1), unit=None, unit_size="12CT", line_total=Decimal("3.48")
    )
    assert r.measure_status == RESOLVED
    assert r.measure_dimension == "count"
    assert r.measure_quantity == Decimal("12.0000")
    assert r.normalized_unit_price == Decimal("0.290000")


def test_count_only_unit_is_each():
    """Verify that a count unit with no size resolves to quantity-as-each."""
    r = normalize_line_item(
        quantity=Decimal(3), unit="ea", unit_size=None, line_total=Decimal("1.50")
    )
    assert r.measure_dimension == "count"
    assert r.normalized_unit_price == Decimal("0.500000")


def test_no_measure_no_text_is_not_applicable():
    """Verify that a line with no unit or size text is flagged as not_applicable."""
    # an apple / loaf of bread with nothing printed
    r = normalize_line_item(
        quantity=Decimal(1), unit=None, unit_size=None, line_total=Decimal("0.99")
    )
    assert r.measure_status == NOT_APPLICABLE
    assert r.normalized_unit_price is None
    assert r.measure_dimension is None


def test_unparsable_measure_text_is_needs_review():
    """Verify that unrecognized unit text triggers needs_review rather than silently dropping."""
    r = normalize_line_item(
        quantity=Decimal(1), unit="family pack", unit_size=None, line_total=Decimal("8.00")
    )
    assert r.measure_status == NEEDS_REVIEW
    assert r.normalized_unit_price is None


def test_zero_measure_is_needs_review():
    """Verify that a zero quantity with a weight unit yields needs_review to avoid division by zero."""
    r = normalize_line_item(
        quantity=Decimal(0), unit="lb", unit_size=None, line_total=Decimal("3.00")
    )
    assert r.measure_status == NEEDS_REVIEW


def test_measurable_unit_wins_over_llm_measure():
    """Verify that a sold-by-weight unit takes precedence over an LLM per-package measure."""
    # Sold by the pound: quantity is the authoritative amount, so the lb path must win
    # even when the LLM also reported a per-package measure.
    r = normalize_line_item(
        quantity=Decimal(2),
        unit="lb",
        unit_size=None,
        line_total=Decimal("3.48"),
        llm_measure=(400, "g"),
    )
    assert r.measure_dimension == "weight"
    assert r.measure_quantity == Decimal("907.1840")  # 2 lb, not 400 g
