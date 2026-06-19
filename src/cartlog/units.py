"""Pure unit conversion and measure extraction for price normalization.

Canonical bases per dimension: weight=gram, volume=milliliter, count=each. Storing one
metric base per dimension keeps a single comparable value; display converts as needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from cartlog.constants import COUNT, UNIT_ALIASES, UNIT_FACTORS, VOLUME, WEIGHT

# Optional "Nx" multiplier, a magnitude, then unit text (letters, spaces, dots).
_SIZE_RE = re.compile(r"^\s*(?:(\d+(?:\.\d+)?)\s*[x*]\s*)?(\d+(?:\.\d+)?)\s*([a-z.\s]+?)\s*$")


def normalize_unit_token(raw: str | None) -> str | None:
    """Map a free-text unit onto a canonical token, or None when unrecognized."""
    if not raw:
        return None
    cleaned = re.sub(r"\s+", " ", raw.strip().lower()).rstrip(".")
    if not cleaned:
        return None
    token = UNIT_ALIASES.get(cleaned, cleaned)
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


RESOLVED = "resolved"
NOT_APPLICABLE = "not_applicable"
NEEDS_REVIEW = "needs_review"

_MEASURE_PLACES = Decimal("0.0001")  # Numeric(12,4)
_PRICE_PLACES = Decimal("0.000001")  # Numeric(12,6)


@dataclass(frozen=True)
class NormalizationResult:
    """The normalized measure for one line; mirrors the four persisted LineItem columns."""

    measure_quantity: Decimal | None
    measure_dimension: str | None
    normalized_unit_price: Decimal | None
    measure_status: str


def _resolved(total_base: Decimal, dimension: str, line_total: Decimal) -> NormalizationResult:
    """Build a RESOLVED result, guarding against non-positive measure to avoid division by zero."""
    # A non-positive measure means the inputs are nonsense; flag rather than divide.
    if total_base <= 0:
        return NormalizationResult(None, None, None, NEEDS_REVIEW)
    measure = total_base.quantize(_MEASURE_PLACES, rounding=ROUND_HALF_UP)
    price = (line_total / total_base).quantize(_PRICE_PLACES, rounding=ROUND_HALF_UP)
    return NormalizationResult(measure, dimension, price, RESOLVED)


def _has_text(*values: str | None) -> bool:
    """Return True if any value contains non-whitespace characters."""
    return any(v and v.strip() for v in values)


def normalize_line_item(
    *,
    quantity: Decimal,
    unit: str | None,
    unit_size: str | None,
    line_total: Decimal,
    llm_measure: tuple[float | Decimal, str | None] | None = None,
) -> NormalizationResult:
    """Resolve a line's total measure and normalized price-per-base-unit.

    The measurable amount may live in `quantity` (loose produce sold by weight) or in
    `unit_size` (packaged goods). Resolution order matches the design's five-step rule.

    Args:
        quantity: The number of units purchased.
        unit: Free-text unit string from the receipt (e.g. "lb", "ea").
        unit_size: Package size text (e.g. "1.5L", "12CT").
        line_total: The total price paid for this line item.
        llm_measure: Optional LLM-extracted (amount, unit) pair; takes precedence over unit_size.

    Returns:
        NormalizationResult with measure and normalized price, or status indicating why
        normalization could not be completed.
    """
    unit_token = normalize_unit_token(unit)

    # The unit itself is a measurable unit, so the line was sold by that unit and quantity
    # is the authoritative amount. This intentionally takes priority over any llm_measure,
    # which only describes a per-package size (relevant when unit is a count/null token).
    if unit_token is not None and UNIT_FACTORS[unit_token][0] in (WEIGHT, VOLUME):
        dimension, factor = UNIT_FACTORS[unit_token]
        return _resolved(total_base=quantity * factor, dimension=dimension, line_total=line_total)

    # Steps 1/3: a per-package measure from the LLM (preferred) or parsed from unit_size.
    measure: tuple[Decimal, str] | None = None
    if llm_measure is not None:
        token = normalize_unit_token(llm_measure[1])
        if token is not None:
            measure = (Decimal(str(llm_measure[0])), token)
    if measure is None:
        measure = parse_size(unit_size)

    if measure is not None:
        value, token = measure
        dimension, factor = UNIT_FACTORS[token]
        if dimension in (WEIGHT, VOLUME):
            return _resolved(
                total_base=quantity * value * factor, dimension=dimension, line_total=line_total
            )
        # Count packaging (e.g. "12CT"): base is total count.
        return _resolved(total_base=quantity * value, dimension=COUNT, line_total=line_total)

    # Step 4: only a count unit is present -> $/each.
    if unit_token is not None and UNIT_FACTORS[unit_token][0] == COUNT:
        return _resolved(total_base=quantity, dimension=COUNT, line_total=line_total)

    # Step 5: nothing parseable. Leftover measure-looking text means a bad read; otherwise
    # the line genuinely has no measure (a single apple, a loaf of bread).
    if _has_text(unit, unit_size):
        return NormalizationResult(None, None, None, NEEDS_REVIEW)
    return NormalizationResult(None, None, None, NOT_APPLICABLE)
