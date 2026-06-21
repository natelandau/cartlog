"""Project-wide constants shared across cartlog modules.

A home for values a developer is likely to tweak later (accepted file types, unit conversions,
external links), so they live in one obvious place instead of being hunted down inside feature
modules.
"""

from __future__ import annotations

from decimal import Decimal

# iCloud link to the maintained "Send Receipt to cartlog" Apple Shortcut, shown as the install
# button on the integrations page. Update this if the published Shortcut is ever replaced.
SHORTCUT_URL = "https://www.icloud.com/shortcuts/507525103f3944eab9656d458ca6cb10"

# Image suffixes cartlog ingests, mapped to the media type sent to the vision model. Add a row
# here to accept another image format (the model must be able to read it).
IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
PDF_SUFFIX = ".pdf"
PDF_MEDIA_TYPE = "application/pdf"

# Every file suffix cartlog accepts on upload and can parse; upload routes reject anything else.
SUPPORTED_SUFFIXES: frozenset[str] = frozenset({*IMAGE_MEDIA_TYPES, PDF_SUFFIX})

# How long a watched-folder file must sit unchanged before it is imported, so a receipt still
# being copied or synced is never grabbed mid-write. Fixed rather than user-configurable.
FOLDER_SETTLE_SECONDS: float = 10.0

# Default cap on how many times the LLM reclassifier is spent on one stubborn product. The
# runtime value is configurable via CARTLOG_MAX_RECLASSIFY_ATTEMPTS; this is the code default.
DEFAULT_MAX_RECLASSIFY_ATTEMPTS = 2

# Max times the LLM size extractor is spent on a line with no resolvable size before the line
# is left as-is (prevents unbounded retries on genuinely size-less items like a single apple).
DEFAULT_MAX_SIZE_EXTRACT_ATTEMPTS = 2

# Measurement dimensions, each with a canonical base unit: weight=gram, volume=milliliter,
# count=each. Stored values use the base unit so prices stay comparable across pack sizes.
WEIGHT = "weight"
VOLUME = "volume"
COUNT = "count"

# Canonical unit token -> (dimension, factor to convert one token-unit into the dimension's base
# unit). Add a token here to teach cartlog a new unit, and list its spellings in UNIT_ALIASES.
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

# Canonical tokens cartlog recognizes, sorted; shown to the vision model as the allowed units.
ALLOWED_UNIT_TOKENS: tuple[str, ...] = tuple(sorted(UNIT_FACTORS))

# Free-text spellings mapped onto a canonical token. Keys are matched after lowercasing,
# stripping, and collapsing internal whitespace. Add aliases when teaching cartlog a new unit.
UNIT_ALIASES: dict[str, str] = {
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
