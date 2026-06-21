"""Pure unit conversion and measure extraction for price normalization.

Canonical bases per dimension: weight=gram, volume=milliliter, count=each. Storing one
metric base per dimension keeps a single comparable value; display converts as needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from cartlog.constants import COUNT, UNIT_ALIASES, UNIT_FACTORS, VOLUME, WEIGHT

# Optional "Nx" multiplier, a magnitude, then unit text (letters, spaces, dots).
_SIZE_RE = re.compile(r"^\s*(?:(\d+(?:\.\d+)?)\s*[x*]\s*)?(\d+(?:\.\d+)?)\s*([a-z.\s]+?)\s*$")

# Embedded-text size scan: a number then unit text, anywhere in the string. Unlike _SIZE_RE
# (anchored), this finds "11oz" inside "Granola 11oz Bob's". Optional "Nx" multipack prefix.
_EMBEDDED_SIZE_RE = re.compile(r"(?:(\d+(?:\.\d+)?)\s*[x*]\s*)?(\d+(?:\.\d+)?)\s*([a-z]{1,4})\b")
# OCR confusion: a unit "oz" misread as "0z" attached to a number. Conservative: only rewrite
# when glued to digits so we never corrupt real words. We deliberately do NOT repair bare
# trailing "02" (e.g. "1602") into oz: that fabricates a size out of an unrelated number. Such
# genuinely-corrupted tokens are recovered by the LLM size extractor layer instead.
_OZ_REPAIR_RE = re.compile(r"(\d)\s*0z\b")  # "1.150z" -> "1.15oz"
# Per-each phrasing that marks a count sale (quantity carries the count).
_COUNT_SALE_RE = re.compile(r"\bper\s+(?:count|each|ct|ea)\b")
# Plausible magnitude ceilings per dimension to reject a misparse (e.g. a 9000 oz package).
_MAX_PLAUSIBLE = {WEIGHT: Decimal(1000), VOLUME: Decimal(20000), COUNT: Decimal(1000)}


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


def format_size_text(value: Decimal, token: str) -> str:
    """Format a (value, canonical token) measure as a unit_size string parse_size can re-read.

    Use fixed-point formatting deliberately: Decimal.normalize() emits scientific notation for
    round multiples of ten (e.g. Decimal("330").normalize() -> "3.3E+2"), which parse_size cannot
    read, so a recovered size would be silently dropped on the next re-resolve.
    """
    return f"{value:f}{token}"


def repair_size_token(text: str) -> str:
    """Repair the common OCR corruption of "oz" misread as "0z" glued to a number.

    Conservative: only rewrites a digit-adjacent "0z" into "oz" (e.g. "1.150z" -> "1.15oz"), so
    plain words are never touched and bare numbers are never fabricated into a size. Returns the
    possibly-rewritten text.
    """
    return _OZ_REPAIR_RE.sub(r"\1oz", text)


def detect_count_sale(text: str | None) -> bool:
    """Return True when the line text marks a per-each sale (e.g. 'Per Count', 'Per Each')."""
    if not text:
        return False
    return _COUNT_SALE_RE.search(text.lower()) is not None


def extract_size(text: str | None) -> tuple[Decimal, str] | None:
    """Find a (value, canonical_token) size embedded in free text, repairing OCR tokens first.

    Scans left to right for the first number+unit that maps to a known unit token and a
    plausible magnitude. Multipacks ('6x330ml') return the combined value. Ignores percentages
    and bare four-digit years. Returns None when no recognizable size is present.
    """
    if not text:
        return None
    lowered = repair_size_token(text.lower())
    for match in _EMBEDDED_SIZE_RE.finditer(lowered):
        # A percentage like "2%" cannot reach here: the unit group is [a-z] only, so "2%" never
        # matches and the digits are rejected as having no unit token below.
        token = normalize_unit_token(match.group(3))
        if token is None:
            continue
        multiplier = Decimal(match.group(1)) if match.group(1) else Decimal(1)
        value = multiplier * Decimal(match.group(2))
        dimension = UNIT_FACTORS[token][0]
        if value <= 0 or value > _MAX_PLAUSIBLE[dimension]:
            continue
        return value, token
    return None


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
        return NormalizationResult(None, None, None, MeasureStatus.NEEDS_REVIEW)
    return NormalizationResult(None, None, None, MeasureStatus.NOT_APPLICABLE)


def normalized_from_base(
    total_base: Decimal, dimension: str, line_total: Decimal
) -> NormalizationResult:
    """Build a NormalizationResult from an already-computed total base measure.

    Used by inference, where the per-package size comes from the product's learned typical
    size rather than from this line's own text.

    Args:
        total_base: The total base measure in canonical units (grams, ml, or each).
        dimension: The measure dimension (weight, volume, or count).
        line_total: The total price paid for the line.

    Returns:
        A NormalizationResult with RESOLVED status, or NEEDS_REVIEW if total_base is
        non-positive.
    """
    return _resolved(total_base=total_base, dimension=dimension, line_total=line_total)


@dataclass(frozen=True)
class ResolvedMeasure:
    """The orchestrator's outcome for one line item.

    Carries the normalized result, its provenance, and the unit_size to persist
    (rewritten only when extraction recovered a size from text).
    """

    result: NormalizationResult
    measure_source: MeasureSource
    unit_size_out: str | None


def resolve_line_measure(  # noqa: PLR0913
    *,
    quantity: Decimal,
    unit: str | None,
    unit_size: str | None,
    raw_description: str | None,
    canonical_name: str | None,
    line_total: Decimal,
    llm_measure: tuple[float | Decimal, str | None] | None = None,
    product_typical: tuple[Decimal, str] | None = None,
) -> ResolvedMeasure:
    """Resolve a line's measure through the trust-ordered layers, recording its provenance.

    Order: (1) structured printed size, (2) deterministic extraction/repair from text, the
    count-sale rule for per-each produce, then (last) inference from the product's typical
    size. The LLM extraction layer is applied by the caller before inference, by re-invoking
    this function with llm_measure set; it is not part of this pure function.

    Args:
        quantity: Units purchased.
        unit: Verbatim receipt unit string.
        unit_size: Verbatim receipt package-size string.
        raw_description: The line text, scanned when no structured size is present.
        canonical_name: The normalized product name, scanned as a fallback to raw_description.
        line_total: Total price paid for the line.
        llm_measure: Optional (value, unit) recovered by the LLM size extractor; treated as a
            printed-grade structured size by normalize_line_item.
        product_typical: Optional (per_package_base_value, dimension) learned for the product,
            applied only as the final inference layer.

    Returns:
        A ResolvedMeasure carrying the normalized result, the MeasureSource, and the unit_size
        to persist.
    """
    # Layer 1 + the LLM path: structured input (sold-by-weight unit, llm_measure, unit_size).
    norm = normalize_line_item(
        quantity=quantity,
        unit=unit,
        unit_size=unit_size,
        line_total=line_total,
        llm_measure=llm_measure,
    )
    if norm.measure_status == MeasureStatus.RESOLVED:
        return ResolvedMeasure(norm, MeasureSource.PRINTED, unit_size)

    # Layer 2: deterministic extraction from the line text, then re-normalize with it.
    for text in (raw_description, canonical_name):
        size = extract_size(text)
        if size is None:
            continue
        value, token = size
        size_text = format_size_text(value, token)
        norm2 = normalize_line_item(
            quantity=quantity, unit=unit, unit_size=size_text, line_total=line_total
        )
        if norm2.measure_status == MeasureStatus.RESOLVED:
            # REPAIRED when the raw text needed OCR repair to yield this token.
            repaired = repair_size_token((text or "").lower()) != (text or "").lower()
            source = MeasureSource.REPAIRED if repaired else MeasureSource.EXTRACTED
            return ResolvedMeasure(norm2, source, size_text)

    # Layer 2 (count sale): per-each produce -> count dimension, quantity is the count.
    if detect_count_sale(raw_description) and quantity > 0:
        norm_count = normalize_line_item(
            quantity=quantity, unit="ea", unit_size=None, line_total=line_total
        )
        if norm_count.measure_status == MeasureStatus.RESOLVED:
            return ResolvedMeasure(norm_count, MeasureSource.EXTRACTED, unit_size)

    # Layer 4: inference from the product's typical size (last resort).
    if product_typical is not None and quantity > 0:
        value_base, dimension = product_typical
        inferred = normalized_from_base(
            total_base=quantity * value_base, dimension=dimension, line_total=line_total
        )
        if inferred.measure_status == MeasureStatus.RESOLVED:
            return ResolvedMeasure(inferred, MeasureSource.INFERRED, unit_size)

    # Nothing resolved: keep the original (not_applicable/needs_review) result.
    return ResolvedMeasure(norm, MeasureSource.NONE, unit_size)
