"""Tests for the unit factor table and free-text size parser in cartlog.units."""

from decimal import Decimal

import pytest

from cartlog.units import (
    COUNT,
    UNIT_FACTORS,
    VOLUME,
    WEIGHT,
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
