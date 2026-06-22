"""Pure unit conversion and measure extraction for price normalization.

Canonical bases per dimension: weight=gram, volume=milliliter, count=each. Storing one
metric base per dimension keeps a single comparable value; display converts as needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from cartlog.constants import COUNT, UNIT_ALIASES, UNIT_FACTORS


def normalize_unit_token(raw: str | None) -> str | None:
    """Map a free-text unit onto a canonical token, or None when unrecognized."""
    if not raw:
        return None
    cleaned = re.sub(r"\s+", " ", raw.strip().lower()).rstrip(".")
    if not cleaned:
        return None
    token = UNIT_ALIASES.get(cleaned, cleaned)
    return token if token in UNIT_FACTORS else None


class MeasureStatus(StrEnum):
    """The closed set of normalization outcomes persisted on LineItem.measure_status."""

    RESOLVED = "resolved"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_REVIEW = "needs_review"


class MeasureSource(StrEnum):
    """How a line's effective measure was obtained; orthogonal to MeasureStatus.

    Recorded on LineItem.measure_source so every filled size is auditable and the future
    edit UI can pin a human value (MANUAL) that backfill must never overwrite.
    """

    PRINTED = "printed"  # a structured size came straight from the receipt
    EXTRACTED = "extracted"  # recovered from line text (deterministic or LLM)
    REPAIRED = "repaired"  # recovered after fixing an OCR-corrupted token
    INFERRED = "inferred"  # filled from the product's learned typical size
    MANUAL = "manual"  # set by a human; never overwritten by backfill
    NONE = "none"  # no measure resolved


class SoldBy(StrEnum):
    """How a line was sold, selects which measure inputs apply.

    ITEM: quantity is a count of items, with an optional per-item size_amount/size_unit.
    MEASURE: quantity is a weighed/volumetric amount in measure_unit, no per-item size.
    """

    ITEM = "item"
    MEASURE = "measure"


_MEASURE_PLACES = Decimal("0.0001")  # Numeric(12,4)
_PRICE_PLACES = Decimal("0.000001")  # Numeric(12,6)


@dataclass(frozen=True)
class NormalizationResult:
    """The normalized measure for one line; mirrors the four persisted LineItem columns."""

    measure_quantity: Decimal | None
    measure_dimension: str | None
    normalized_unit_price: Decimal | None
    measure_status: MeasureStatus


def _resolved(total_base: Decimal, dimension: str, line_total: Decimal) -> NormalizationResult:
    """Build a RESOLVED result, guarding against non-positive measure to avoid division by zero."""
    # A non-positive measure means the inputs are nonsense; flag rather than divide.
    if total_base <= 0:
        return NormalizationResult(None, None, None, MeasureStatus.NEEDS_REVIEW)
    measure = total_base.quantize(_MEASURE_PLACES, rounding=ROUND_HALF_UP)
    price = (line_total / total_base).quantize(_PRICE_PLACES, rounding=ROUND_HALF_UP)
    return NormalizationResult(measure, dimension, price, MeasureStatus.RESOLVED)


def compute_measure(
    *,
    sold_by: SoldBy,
    quantity: Decimal,
    measure_unit: str | None,
    size_amount: Decimal | None,
    size_unit: str | None,
    line_total: Decimal,
) -> NormalizationResult:
    """Compute the normalized measure from already-structured line fields (pure arithmetic).

    MEASURE: base = quantity * unit_factor(measure_unit) (weight/volume only).
    ITEM + size: base = quantity * size_amount * unit_factor(size_unit).
    ITEM without size: $/each (count dimension, base = quantity).
    """
    if sold_by == SoldBy.MEASURE:
        token = normalize_unit_token(measure_unit)
        if token is None or UNIT_FACTORS[token][0] == COUNT:
            return NormalizationResult(None, None, None, MeasureStatus.NEEDS_REVIEW)
        dimension, factor = UNIT_FACTORS[token]
        return _resolved(total_base=quantity * factor, dimension=dimension, line_total=line_total)

    if size_amount is not None and size_unit is not None:
        token = normalize_unit_token(size_unit)
        if token is None:
            return NormalizationResult(None, None, None, MeasureStatus.NEEDS_REVIEW)
        dimension, factor = UNIT_FACTORS[token]
        return _resolved(
            total_base=quantity * size_amount * factor, dimension=dimension, line_total=line_total
        )

    # ITEM with no per-item size: every item line is at least comparable per-each.
    return _resolved(total_base=quantity, dimension=COUNT, line_total=line_total)
