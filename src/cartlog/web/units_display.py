"""Convert the stored metric-base normalized price into a display string.

Storage is always metric base ($/g, $/ml, $/each). Weight/volume use 3 decimals because
per-oz figures on cheap bulk goods round to zero at 2; count and per-100 use 2 decimals.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from cartlog.constants import COUNT, UNIT_FACTORS, UNIT_LABELS, VOLUME, WEIGHT
from cartlog.units import MeasureStatus

if TYPE_CHECKING:
    from fastapi import Request

# Derived from UNIT_FACTORS so the per-oz / per-fl-oz conversion has a single source of truth
# shared with _to_display_unit; correcting a factor in constants updates both renderers at once.
_OZ_PER_G = UNIT_FACTORS["oz"][1]
_FLOZ_PER_ML = UNIT_FACTORS["floz"][1]
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
    """Render a Decimal without trailing zeros (1.50 -> '1.5', 16.0 -> '16').

    Lossless: keeps every significant digit, so it is safe for editable form fields that re-save
    the rendered value (the receipt-editor quantity input). Use _round2 for read-only display.
    """
    # normalize() emits scientific notation for round powers of ten (200 -> '2E+2'); the
    # explicit "f" format keeps it fixed-point so the displayed number stays human-readable.
    return format(value.normalize(), "f")


def _round2(value: Decimal) -> str:
    """Round a measure to two decimals for read-only display, never collapsing a size to '0'.

    The measure column needs only two-decimal precision (an inferred size rebased to grams reads as
    '382.7183'), but a tiny measure converted across systems (a 100 mg supplement shown in ounces)
    would round to zero and erase the size, so fall back to finer precision when that happens.
    """
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rounded == 0 and value != 0:
        return _trim(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
    return _trim(rounded)


# The measurement system each weight/volume token belongs to, so a stored measure can be shown in
# the reader's units. Count tokens (ea/ct/each) belong to neither system and are never converted.
_UNIT_SYSTEM: dict[str, str] = {
    "oz": "imperial",
    "lb": "imperial",
    "floz": "imperial",
    "cup": "imperial",
    "pt": "imperial",
    "qt": "imperial",
    "gal": "imperial",
    "g": "metric",
    "kg": "metric",
    "mg": "metric",
    "ml": "metric",
    "l": "metric",
}
# When a stored unit must cross systems, the candidate display units for the target (dimension,
# system), ordered largest-first. _to_display_unit picks the largest unit the amount fills (>= 1),
# so a small package reads in oz/ml and a big one in lb/gal/kg/l rather than always the small unit.
_DISPLAY_LADDER: dict[tuple[str, str], tuple[str, ...]] = {
    (WEIGHT, "imperial"): ("lb", "oz"),
    (WEIGHT, "metric"): ("kg", "g", "mg"),
    (VOLUME, "imperial"): ("gal", "floz"),
    (VOLUME, "metric"): ("l", "ml"),
}


def _to_display_unit(amount: Decimal, unit: str, system: str) -> tuple[Decimal, str]:
    """Express a measure in the reader's unit system, leaving same-system measures untouched.

    A weight or volume already in the reader's system keeps its own unit, so a per-pound produce
    line stays '0.96 lb' instead of rebasing to ounces. Only a wrong-system unit (an inferred size
    rebased to grams shown to a US reader) is converted, and then to a magnitude-appropriate unit:
    a small package reads in ounces while a 2 kg bag reads as '4.41 lb', not '70.55 oz'. Count
    units and unrecognized tokens pass through unchanged.

    Args:
        amount: The stored measure amount in `unit`.
        unit: The canonical unit token the amount is stored in.
        system: The reader's unit system, "imperial" or "metric".

    Returns:
        The (amount, unit token) pair to display.
    """
    if unit not in UNIT_FACTORS:
        return amount, unit
    dimension, factor = UNIT_FACTORS[unit]
    if dimension == COUNT or _UNIT_SYSTEM.get(unit) == system:
        return amount, unit
    base = amount * factor  # the amount expressed in the dimension's base unit (g or ml)
    ladder = _DISPLAY_LADDER[(dimension, system)]
    # Largest unit the amount fills; fall back to the smallest so a tiny size still shows a unit.
    target = next((token for token in ladder if base >= UNIT_FACTORS[token][1]), ladder[-1])
    return base / UNIT_FACTORS[target][1], target


def format_quantity(quantity: Decimal | None) -> str:
    """Render a purchased quantity without trailing zeros, so a whole count reads as an integer.

    Use for the plain quantity column where a count line stores quantity as a fixed-decimal
    value (2.000): show '2' rather than '2.000', while keeping real fractions like '1.5'.

    Returns:
        The trimmed quantity, or '' when the quantity is missing.
    """
    if quantity is None:
        return ""
    return _trim(quantity)


def format_measure(
    *,
    sold_by: str,
    quantity: Decimal | None,
    measure_unit: str | None,
    size_amount: Decimal | None,
    size_unit: str | None,
    system: str,
) -> str:
    """Render the human measure string for a line, or '' when there is no measure to show.

    Use this to display the package measure alongside a receipt line item. MEASURE lines
    show the quantity with its unit (e.g. "1.47 lb"); ITEM lines show the package size and
    optionally a multipack count (e.g. "2 x 16 oz"). Returns '' for ITEM lines with no size
    so callers can fall back to the plain quantity number. The measure is shown in the reader's
    unit system (matching the NORM $/UNIT column), so an inferred gram size reads as ounces for a
    US reader rather than "382.7183 g".

    Args:
        sold_by: Either "measure" (priced by weight/volume) or "item" (priced per unit).
        quantity: The purchased quantity; used as the measure amount or multipack count.
        measure_unit: The unit token for MEASURE lines (e.g. "lb", "kg").
        size_amount: The package size for ITEM lines (e.g. Decimal("16") for 16 oz).
        size_unit: The unit token for the package size (e.g. "oz").
        system: The reader's unit system, "imperial" or "metric".

    Returns:
        A human-readable measure string, or '' when no measure is available.
    """
    if sold_by == "measure" and measure_unit:
        # A measure line always carries a quantity; guard None defensively so a malformed
        # row can never raise AttributeError mid-render and 500 the whole results page.
        if quantity is None:
            return measure_unit
        amount, unit = _to_display_unit(amount=quantity, unit=measure_unit, system=system)
        return f"{_round2(amount)} {unit}"
    if sold_by == "item" and size_amount is not None and size_unit:
        amount, unit = _to_display_unit(amount=size_amount, unit=size_unit, system=system)
        size = f"{_round2(amount)} {unit}"
        if quantity is None or quantity == 1:
            return size
        return f"{_round2(quantity)} x {size}"
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
