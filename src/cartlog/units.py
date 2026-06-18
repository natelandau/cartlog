"""Pure unit conversion and measure extraction for price normalization.

Canonical bases per dimension: weight=gram, volume=milliliter, count=each. Storing one
metric base per dimension keeps a single comparable value; display converts as needed.
"""

from __future__ import annotations

import re
from decimal import Decimal

WEIGHT = "weight"
VOLUME = "volume"
COUNT = "count"

# token -> (dimension, factor to convert one token-unit into the dimension's base unit)
UNIT_FACTORS: dict[str, tuple[str, Decimal]] = {
    "g": (WEIGHT, Decimal(1)),
    "kg": (WEIGHT, Decimal(1000)),
    "mg": (WEIGHT, Decimal("0.001")),
    "oz": (WEIGHT, Decimal("28.3495")),
    "lb": (WEIGHT, Decimal("453.592")),
    "ml": (VOLUME, Decimal(1)),
    "l": (VOLUME, Decimal(1000)),
    "floz": (VOLUME, Decimal("29.5735")),
    "gal": (VOLUME, Decimal("3785.41")),
    "qt": (VOLUME, Decimal("946.353")),
    "pt": (VOLUME, Decimal("473.176")),
    "cup": (VOLUME, Decimal("236.588")),
    "ea": (COUNT, Decimal(1)),
    "ct": (COUNT, Decimal(1)),
    "each": (COUNT, Decimal(1)),
}

ALLOWED_UNIT_TOKENS: tuple[str, ...] = tuple(sorted(UNIT_FACTORS))

# Free-text spellings mapped onto a canonical token. Keys are matched after lowercasing,
# stripping, and collapsing internal whitespace.
_ALIASES: dict[str, str] = {
    "liter": "l",
    "litre": "l",
    "liters": "l",
    "litres": "l",
    "lt": "l",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "ounce": "oz",
    "ounces": "oz",
    "ozs": "oz",
    "gram": "g",
    "grams": "g",
    "grm": "g",
    "grms": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "kgs": "kg",
    "milligram": "mg",
    "milligrams": "mg",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "fl oz": "floz",
    "fluid ounce": "floz",
    "fluid ounces": "floz",
    "fl. oz": "floz",
    "fl-oz": "floz",
    "gallon": "gal",
    "gallons": "gal",
    "quart": "qt",
    "quarts": "qt",
    "pint": "pt",
    "pints": "pt",
    "cups": "cup",
    "count": "ct",
    "cnt": "ct",
    "pk": "ct",
    "pks": "ct",
    "pack": "ct",
    "packs": "ct",
    "pkg": "ct",
}

# Optional "Nx" multiplier, a magnitude, then unit text (letters, spaces, dots).
_SIZE_RE = re.compile(r"^\s*(?:(\d+(?:\.\d+)?)\s*[x*]\s*)?(\d+(?:\.\d+)?)\s*([a-z.\s]+?)\s*$")


def normalize_unit_token(raw: str | None) -> str | None:
    """Map a free-text unit onto a canonical token, or None when unrecognized."""
    if not raw:
        return None
    cleaned = re.sub(r"\s+", " ", raw.strip().lower()).rstrip(".")
    if not cleaned:
        return None
    token = _ALIASES.get(cleaned, cleaned)
    return token if token in UNIT_FACTORS else None


def parse_size(text: str | None) -> tuple[Decimal, str] | None:
    """Extract a (value, canonical_token) measure from free text like '1.5L' or '6x330ml'.

    Multipacks ('6x330ml') return the combined value (1980 ml). Returns None when no
    recognizable unit is present.
    """
    if not text:
        return None
    match = _SIZE_RE.match(text.strip().lower())
    if match is None:
        return None
    multiplier = Decimal(match.group(1)) if match.group(1) else Decimal(1)
    magnitude = Decimal(match.group(2))
    token = normalize_unit_token(match.group(3))
    if token is None:
        return None
    return multiplier * magnitude, token
