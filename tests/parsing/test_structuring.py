"""Tests for the write-time size-recovery helpers in parsing/structuring."""

from decimal import Decimal

import pytest

from cartlog.parsing.structuring import (
    StructuredMeasure,
    detect_count_sale,
    detect_measure_sale,
    extract_size,
    format_size_text,
    parse_size,
    repair_size_token,
    structure_line,
)
from cartlog.units import MeasureSource, SoldBy


def test_format_size_text_roundtrips_through_parse_size():
    """Verify format_size_text produces text that parse_size can recover exactly."""
    text = format_size_text(Decimal(330), "ml")
    assert parse_size(text) == (Decimal(330), "ml")


def test_repair_size_token():
    """Verify repair_size_token corrects the OCR confusion of oz -> 0z glued to a digit."""
    assert repair_size_token("1.150z") == "1.15oz"


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
def test_parse_size_comprehensive(text, expected):
    """Verify size strings are parsed into (value, token) pairs, with multipack quantities combined."""
    assert parse_size(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Granola, Maple Sea Salt, 11oz, Bob's", (Decimal(11), "oz")),
        ("Granola 11.150z Bob's", (Decimal("11.15"), "oz")),  # embedded size after 0z -> oz repair
        ("Milk, Whole, 64oz, Ithaca", (Decimal(64), "oz")),
        ("Jerky, Venison, S&P, 1.150z., Chomps", (Decimal("1.15"), "oz")),  # repaired 0z -> oz
        ("1602", None),  # bare trailing "02" is NOT fabricated into oz (LLM layer recovers it)
        ("2002", None),  # bare trailing "02" is not a size
        ("1202", None),  # bare trailing "02" is not a size
        ("Soda 6x330ml", (Decimal(1980), "ml")),  # multipack combined
        ("Whole Milk 2%", None),  # percentage is not a size
        ("Avocados, Organic", None),  # no unit token
        ("Receipt 2024", None),  # four-digit year is not a size
        ("Eggs 12ct Bob's", (Decimal(12), "ct")),  # a real count size is still extracted
        # "each"/"ea" attached to a number is per-each pricing, not a package size; never extract it
        ("2 Avocados, OG, Per Count $1.39 each", None),
        ("Grapefruit, OG, Per Count $2.93 each", None),
        ("Plums Per count 1.45 each", None),
        ("Lemons $0.50 ea", None),
    ],
)
def test_extract_size_comprehensive(text, expected):
    """Verify embedded size extraction handles OCR repair, multipacks, and false-positive rejection."""
    assert extract_size(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2 Avocados, Organic, Per Count", True),
        ("Peaches, OG, Per Each", True),
        ("Bananas Per Each", True),
        ("Plums Per count 1.45 each", True),
        ("Granola 11oz", False),
        ("Bananas 1.5kg", False),  # a weight-sold line is not a count sale
    ],
)
def test_detect_count_sale_comprehensive(text, expected):
    """Verify per-each phrasing is detected as a count sale and other text is not."""
    assert detect_count_sale(text) is expected


def _s(
    *,
    quantity: Decimal = Decimal(1),
    unit: str | None = None,
    unit_size: str | None = None,
    raw_description: str | None = None,
    canonical_name: str | None = None,
    llm_measure: tuple[float | Decimal, str | None] | None = None,
    product_typical: tuple[Decimal, str] | None = None,
) -> StructuredMeasure:
    return structure_line(
        quantity=quantity,
        unit=unit,
        unit_size=unit_size,
        raw_description=raw_description,
        canonical_name=canonical_name,
        llm_measure=llm_measure,
        product_typical=product_typical,
    )


def test_weight_unit_becomes_measure_mode():
    """Verify a weight unit produces MEASURE mode with PRINTED source."""
    out = _s(quantity=Decimal("1.47"), unit="lb")
    assert out == StructuredMeasure(SoldBy.MEASURE, "lb", None, None, MeasureSource.PRINTED)


def test_printed_unit_size_becomes_item_size():
    """Verify a printed unit_size field produces ITEM mode with PRINTED source."""
    out = _s(quantity=Decimal(2), unit="ea", unit_size="16oz")
    assert out == StructuredMeasure(SoldBy.ITEM, None, Decimal(16), "oz", MeasureSource.PRINTED)


def test_llm_measure_is_extracted_item_size():
    """Verify an LLM-recovered measure produces ITEM mode with EXTRACTED source."""
    out = _s(unit="ea", llm_measure=(1.5, "l"))
    assert out == StructuredMeasure(SoldBy.ITEM, None, Decimal("1.5"), "l", MeasureSource.EXTRACTED)


@pytest.mark.parametrize("token", ["ea", "each"])
def test_llm_measure_each_is_not_a_size(token):
    """Verify an LLM each/ea answer is rejected as a size, leaving the count sale size-less."""
    # Given the size extractor reads a per-each price as a "1 each" size on a count-sale line
    out = _s(
        quantity=Decimal(2),
        raw_description="2 Grapefruit, OG, Per Count $2.93 each",
        llm_measure=(1, token),
    )
    # Then the each/ea answer is not applied, so detect_count_sale keeps it a size-less $/each line
    assert out == StructuredMeasure(SoldBy.ITEM, None, None, None, MeasureSource.EXTRACTED)


def test_ocr_repaired_embedded_size_is_repaired_source():
    """Verify an OCR-repaired embedded size produces ITEM mode with REPAIRED source."""
    out = _s(raw_description="Granola 11.150z Bob's")
    assert out == StructuredMeasure(
        SoldBy.ITEM, None, Decimal("11.15"), "oz", MeasureSource.REPAIRED
    )


def test_count_sale_is_item_without_size():
    """Verify a per-each count sale produces ITEM mode with no size and EXTRACTED source."""
    out = _s(quantity=Decimal(4), raw_description="Avocado Per Each")
    assert out == StructuredMeasure(SoldBy.ITEM, None, None, None, MeasureSource.EXTRACTED)


def test_per_each_priced_count_sale_is_item_without_size():
    """Verify a per-count line priced per-each is size-less, not sized by the per-each price."""
    # Given a produce line sold by count and priced per-each (the price is in the text)
    out = _s(quantity=Decimal(2), raw_description="2 Avocados, OG, Per Count $1.39 each")
    # Then the per-each price is not read as a size, so it stays a size-less count sale
    assert out == StructuredMeasure(SoldBy.ITEM, None, None, None, MeasureSource.EXTRACTED)


def test_typical_size_inference():
    """Verify a product-typical size produces ITEM mode with INFERRED source and base-unit token."""
    out = _s(quantity=Decimal(1), product_typical=(Decimal("453.592"), "weight"))
    assert out == StructuredMeasure(
        SoldBy.ITEM, None, Decimal("453.592"), "g", MeasureSource.INFERRED
    )


def test_nothing_found_is_item_without_size_none_source():
    """Verify a line with no recoverable size produces ITEM mode with NONE source."""
    out = _s(quantity=Decimal(1), canonical_name="bread")
    assert out == StructuredMeasure(SoldBy.ITEM, None, None, None, MeasureSource.NONE)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bananas, OG, Per Pound 2.21 lb @ $1.02 per lb", "lb"),
        ("Ground Beef ($11.75/lb) x 2.04 lb (Manual Wt)", "lb"),
        ("W Limes, OG, Per LB 0.12 lb @ $2.26 per lb", "lb"),
        ("ORGANIC PLUOTS 1.41 lb @ $6.99/ lb", "lb"),
        ("Olive Oil $9 per liter", "l"),
        ("Avocado Per Each", None),  # count phrasing, not weight/volume
        ("Granola 11oz Bob's", None),  # a size, but no per-unit pricing
        ("Soda 2L", None),  # a size, not priced per unit
    ],
)
def test_detect_measure_sale(text, expected):
    """Verify per-weight/volume pricing is detected and count or size-only text is not."""
    assert detect_measure_sale(text) == expected


def test_per_weight_pricing_becomes_measure_mode():
    """Verify a line priced per pound is MEASURE mode, not an item with a per-item size."""
    # Given a banana line sold by the pound (the weight also appears as the quantity)
    out = _s(
        quantity=Decimal("2.21"),
        raw_description="Bananas, OG, Per Pound 2.21 lb @ $1.02 per lb",
    )
    # Then it is classified as sold by weight, so compute_measure uses quantity * factor
    assert out == StructuredMeasure(SoldBy.MEASURE, "lb", None, None, MeasureSource.EXTRACTED)


def test_per_weight_pricing_overrides_llm_per_item_size():
    """Verify per-pound pricing wins over an LLM measure that mistook the weight for a size."""
    # Given the parser double-encoded the weight as an llm per-item measure
    out = _s(
        quantity=Decimal("2.04"),
        raw_description="Ground Beef ($11.75/lb) x 2.04 lb (Manual Wt)",
        llm_measure=(2.04, "lb"),
    )
    # Then the per-pound pricing classifies it as MEASURE, not ITEM (which would square the weight)
    assert out == StructuredMeasure(SoldBy.MEASURE, "lb", None, None, MeasureSource.EXTRACTED)
