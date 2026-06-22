"""Convert the stored metric-base normalized price into a display string.

Storage is always metric base ($/g, $/ml, $/each). Weight/volume use 3 decimals because
per-oz figures on cheap bulk goods round to zero at 2; count and per-100 use 2 decimals.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from cartlog.constants import COUNT, UNIT_LABELS, VOLUME, WEIGHT
from cartlog.units import MeasureStatus

if TYPE_CHECKING:
    from fastapi import Request

_OZ_PER_G = Decimal("28.3495")
_FLOZ_PER_ML = Decimal("29.5735")
_HUNDRED = Decimal(100)


def format_normalized(
    normalized_unit_price: Decimal | None,
    dimension: str | None,
    status: str,
    system: str,
) -> str:
    """Render a normalized price for display, or 'n/a' when the line is not resolved.

    Args:
        normalized_unit_price: The stored $/base-unit price ($/g, $/ml, or $/each).
        dimension: The measure dimension: "weight", "volume", or "count".
        status: The normalization status; only "resolved" lines produce a price.
        system: Display unit system: "imperial" or "metric".

    Returns:
        A formatted price string like "$0.109/oz" or "n/a" for unresolved rows.
    """
    if status != MeasureStatus.RESOLVED or normalized_unit_price is None or dimension is None:
        return "n/a"
    if dimension == COUNT:
        return f"${normalized_unit_price:.2f}/ea"
    if system == "metric":
        per_100 = normalized_unit_price * _HUNDRED
        suffix = "100g" if dimension == WEIGHT else "100ml"
        return f"${per_100:.2f}/{suffix}"
    if dimension == WEIGHT:
        return f"${normalized_unit_price * _OZ_PER_G:.3f}/oz"
    if dimension == VOLUME:
        return f"${normalized_unit_price * _FLOZ_PER_ML:.3f}/fl oz"
    return "n/a"


def _trim(value: Decimal) -> str:
    """Render a Decimal without trailing zeros (1.50 -> '1.5', 16.0 -> '16')."""
    return format(value.normalize(), "f")


def format_measure(
    *,
    sold_by: str,
    quantity: Decimal | None,
    measure_unit: str | None,
    size_amount: Decimal | None,
    size_unit: str | None,
) -> str:
    """Render the human measure string for a line, or '' when there is no measure to show.

    Use this to display the package measure alongside a receipt line item. MEASURE lines
    show the quantity with its unit (e.g. "1.47 lb"); ITEM lines show the package size and
    optionally a multipack count (e.g. "2 x 16 oz"). Returns '' for ITEM lines with no size
    so callers can fall back to the plain quantity number.

    Args:
        sold_by: Either "measure" (priced by weight/volume) or "item" (priced per unit).
        quantity: The purchased quantity; used as the measure amount or multipack count.
        measure_unit: The unit token for MEASURE lines (e.g. "lb", "kg").
        size_amount: The package size for ITEM lines (e.g. Decimal("16") for 16 oz).
        size_unit: The unit token for the package size (e.g. "oz").

    Returns:
        A human-readable measure string, or '' when no measure is available.
    """
    if sold_by == "measure" and measure_unit:
        # A measure line always carries a quantity; guard None defensively so a malformed
        # row can never raise AttributeError mid-render and 500 the whole results page.
        return f"{_trim(quantity)} {measure_unit}" if quantity is not None else measure_unit
    if sold_by == "item" and size_amount is not None and size_unit:
        size = f"{_trim(size_amount)} {size_unit}"
        if quantity is None or quantity == 1:
            return size
        return f"{_trim(quantity)} x {size}"
    return ""


# Which tokens each dropdown offers (order is irrelevant here; the helpers sort by label).
# The redundant 'each' token is omitted ('ea' already reads as "Each") so the menu has no
# duplicate entries.
_MEASURE_UNIT_TOKENS = ("oz", "lb", "g", "kg", "mg", "floz", "cup", "pt", "qt", "gal", "ml", "l")
_SIZE_UNIT_TOKENS = (*_MEASURE_UNIT_TOKENS, "ea", "ct")


def measure_unit_options() -> list[tuple[str, str]]:
    """Return (token, label) pairs of weight and volume units, sorted alphabetically by label.

    Excludes count tokens because measure_unit applies only to MEASURE (weight/volume) lines.
    The token is the option value; the label is the spelled-out name (e.g. ("lb", "Pound")).
    """
    return sorted(
        ((token, UNIT_LABELS[token]) for token in _MEASURE_UNIT_TOKENS), key=lambda p: p[1]
    )


def size_unit_options() -> list[tuple[str, str]]:
    """Return (token, label) pairs of all units (weight, volume, count), sorted by label.

    Used for ITEM package-size unit pickers, where count tokens like 'ct' or 'ea' are valid.
    The token is the option value; the label is the spelled-out name (e.g. ("ct", "Count")).
    """
    return sorted(((token, UNIT_LABELS[token]) for token in _SIZE_UNIT_TOKENS), key=lambda p: p[1])


def read_unit_system(request: Request) -> str:
    """Return the caller's preferred unit system from the cookie; imperial is the default.

    Args:
        request: The incoming FastAPI request carrying cookies.

    Returns:
        "metric" when the cookie is explicitly set to metric, otherwise "imperial".
    """
    return "metric" if request.cookies.get("unit_system") == "metric" else "imperial"
