"""Tests for the unit factor table and free-text size parser in cartlog.units."""

from decimal import Decimal
from enum import StrEnum

import pytest

from cartlog.constants import COUNT, UNIT_FACTORS, VOLUME, WEIGHT
from cartlog.units import (
    MeasureSource,
    MeasureStatus,
    detect_count_sale,
    extract_size,
    normalize_line_item,
    normalize_unit_token,
    parse_size,
    resolve_line_measure,
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
    assert r.measure_status == MeasureStatus.RESOLVED
    assert r.measure_dimension == "weight"
    assert r.measure_quantity == Decimal("907.1840")
    assert r.normalized_unit_price == Decimal("0.003836")  # 3.48 / 907.184


def test_packaged_volume_from_unit_size():
    """Verify that packaged volume goods resolve measure from unit_size text."""
    # milk: 1 x 1.5L @ 4.50 -> 1500 ml, $/ml
    r = normalize_line_item(
        quantity=Decimal(1), unit="ea", unit_size="1.5L", line_total=Decimal("4.50")
    )
    assert r.measure_status == MeasureStatus.RESOLVED
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
    assert r.measure_status == MeasureStatus.RESOLVED
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
    assert r.measure_status == MeasureStatus.NOT_APPLICABLE
    assert r.normalized_unit_price is None
    assert r.measure_dimension is None


def test_unparsable_measure_text_is_needs_review():
    """Verify that unrecognized unit text triggers needs_review rather than silently dropping."""
    r = normalize_line_item(
        quantity=Decimal(1), unit="family pack", unit_size=None, line_total=Decimal("8.00")
    )
    assert r.measure_status == MeasureStatus.NEEDS_REVIEW
    assert r.normalized_unit_price is None


def test_zero_measure_is_needs_review():
    """Verify that a zero quantity with a weight unit yields needs_review to avoid division by zero."""
    r = normalize_line_item(
        quantity=Decimal(0), unit="lb", unit_size=None, line_total=Decimal("3.00")
    )
    assert r.measure_status == MeasureStatus.NEEDS_REVIEW


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


def test_measure_status_is_a_str_enum_with_three_members():
    """Verify MeasureStatus owns the closed measure_status vocabulary as a StrEnum."""
    # Given the MeasureStatus enum
    # Then it is a StrEnum whose members equal their persisted string values
    assert issubclass(MeasureStatus, StrEnum)
    assert MeasureStatus.RESOLVED == "resolved"
    assert MeasureStatus.NOT_APPLICABLE == "not_applicable"
    assert MeasureStatus.NEEDS_REVIEW == "needs_review"
    assert {m.value for m in MeasureStatus} == {"resolved", "not_applicable", "needs_review"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Granola, Maple Sea Salt, 11oz, Bob's", (Decimal(11), "oz")),
        ("Milk, Whole, 64oz, Ithaca", (Decimal(64), "oz")),
        ("Jerky, Venison, S&P, 1.150z., Chomps", (Decimal("1.15"), "oz")),  # repaired 0z -> oz
        ("1602", None),  # bare trailing "02" is NOT fabricated into oz (LLM layer recovers it)
        ("2002", None),  # bare trailing "02" is not a size
        ("1202", None),  # bare trailing "02" is not a size
        ("Soda 6x330ml", (Decimal(1980), "ml")),  # multipack combined
        ("Whole Milk 2%", None),  # percentage is not a size
        ("Avocados, Organic", None),  # no unit token
        ("Receipt 2024", None),  # four-digit year is not a size
    ],
)
def test_extract_size(text, expected):
    """Verify embedded size extraction handles OCR repair, multipacks, and false-positive rejection."""
    assert extract_size(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2 Avocados, Organic, Per Count", True),
        ("Peaches, OG, Per Each", True),
        ("Plums Per count 1.45 each", True),
        ("Granola 11oz", False),
    ],
)
def test_detect_count_sale(text, expected):
    """Verify per-each phrasing is detected as a count sale and other text is not."""
    assert detect_count_sale(text) is expected


def test_resolve_uses_printed_structured_size():
    """Verify that a structured unit_size on the receipt is resolved as PRINTED."""
    out = resolve_line_measure(
        quantity=Decimal(1),
        unit=None,
        unit_size="64oz",
        raw_description="Milk 64oz",
        canonical_name="milk",
        line_total=Decimal("4.00"),
    )
    assert out.measure_source == MeasureSource.PRINTED
    assert out.result.measure_status == MeasureStatus.RESOLVED


def test_resolve_extracts_size_from_description_when_unit_size_blank():
    """Verify that a size embedded in the description text is extracted and sourced as EXTRACTED."""
    out = resolve_line_measure(
        quantity=Decimal(1),
        unit=None,
        unit_size=None,
        raw_description="Granola, Maple Sea Salt, 11oz, Bob's",
        canonical_name="granola",
        line_total=Decimal("5.00"),
    )
    assert out.measure_source == MeasureSource.EXTRACTED
    assert out.unit_size_out == "11oz"
    assert out.result.measure_status == MeasureStatus.RESOLVED


def test_resolve_detects_count_sale_for_per_each_produce():
    """Verify that per-each produce is resolved via count-sale detection with the correct price."""
    out = resolve_line_measure(
        quantity=Decimal(2),
        unit=None,
        unit_size=None,
        raw_description="2 Avocados, Organic, OG, Per Count",
        canonical_name="avocado",
        line_total=Decimal("4.04"),
    )
    assert out.measure_source == MeasureSource.EXTRACTED
    assert out.result.measure_dimension == "count"
    assert out.result.normalized_unit_price == Decimal("2.020000")


def test_resolve_infers_from_product_typical_as_last_resort():
    """Verify that product_typical is used as the final inference layer and never rewrites unit_size."""
    out = resolve_line_measure(
        quantity=Decimal(1),
        unit=None,
        unit_size=None,
        raw_description="Mystery Pasta",
        canonical_name="pasta",
        line_total=Decimal("2.00"),
        product_typical=(Decimal("453.592"), "weight"),  # one 1lb box in grams
    )
    assert out.measure_source == MeasureSource.INFERRED
    assert out.result.measure_status == MeasureStatus.RESOLVED
    assert out.unit_size_out is None  # inference never rewrites verbatim unit_size


def test_resolve_returns_none_source_when_nothing_resolves():
    """Verify that NONE source is returned when no layer can resolve the measure."""
    out = resolve_line_measure(
        quantity=Decimal(1),
        unit=None,
        unit_size=None,
        raw_description="A Single Apple",
        canonical_name="apple",
        line_total=Decimal("0.50"),
    )
    assert out.measure_source == MeasureSource.NONE


def test_resolve_round_number_size_in_description_is_parseable():
    """Verify that a round number size extracted from description resolves without scientific notation.

    Round numbers like 100g produce Decimal("100").  Before the fix, .normalize() would
    emit "1E+2g", which _SIZE_RE cannot parse, leaving the line NOT_APPLICABLE.  After the
    fix, ":f" formatting emits "100g", which parse_size handles correctly.
    """
    # Given a line with a round-number size embedded in its description and no structured size
    out = resolve_line_measure(
        quantity=Decimal(1),
        unit=None,
        unit_size=None,
        raw_description="Beans 100g",
        canonical_name="beans",
        line_total=Decimal("2.00"),
    )

    # Then the size is extracted deterministically and the line is resolved
    assert out.measure_source == MeasureSource.EXTRACTED
    assert out.result.measure_status == MeasureStatus.RESOLVED
    assert out.unit_size_out == "100g"
