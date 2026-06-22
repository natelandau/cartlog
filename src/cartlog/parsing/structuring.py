"""Write-time recovery of a structured measure from a receipt line's free text.

Turns the LLM parser's messy `unit`/`unit_size`/`raw_description` into the structured
(sold_by, measure_unit, size_amount, size_unit) columns plus provenance. All free-text
parsing lives here so cartlog.units stays pure arithmetic over already-structured fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from cartlog.constants import COUNT, UNIT_FACTORS, VOLUME, WEIGHT
from cartlog.units import MeasureSource, SoldBy, normalize_unit_token

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
# Per-unit pricing ("$1.02 per lb", "Per Pound", "$11.75/lb"): the word after "per " or "/".
# When that word is a weight/volume unit the line was sold by that measure (see detect_measure_sale).
_PER_UNIT_RE = re.compile(r"(?:\bper\s+|/)\s*([a-z][a-z.]*)")
# Plausible magnitude ceilings per dimension to reject a misparse (e.g. a 9000 oz package).
_MAX_PLAUSIBLE = {WEIGHT: Decimal(1000), VOLUME: Decimal(20000), COUNT: Decimal(1000)}


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


def detect_measure_sale(text: str | None) -> str | None:
    """Return the weight/volume token when the text prices the line per unit of that measure.

    Detect per-weight/volume pricing such as "$1.02 per lb", "Per Pound", or "$11.75/lb",
    which marks a sale priced by weight or volume: the line quantity is the amount purchased,
    not a per-item package size. Mirrors detect_count_sale for the count case. Returns None
    for count phrasing ("per each") or text with no per-unit pricing.
    """
    if not text:
        return None
    for match in _PER_UNIT_RE.finditer(text.lower()):
        token = normalize_unit_token(match.group(1))
        if token is not None and UNIT_FACTORS[token][0] in (WEIGHT, VOLUME):
            return token
    return None


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


_BASE_TOKEN = {WEIGHT: "g", VOLUME: "ml", COUNT: "ea"}


@dataclass(frozen=True)
class StructuredMeasure:
    """The structured measure for one line: its mode, size, and how the size was obtained."""

    sold_by: SoldBy
    measure_unit: str | None
    size_amount: Decimal | None
    size_unit: str | None
    source: MeasureSource


def _item(amount: Decimal | None, unit: str | None, source: MeasureSource) -> StructuredMeasure:
    return StructuredMeasure(SoldBy.ITEM, None, amount, unit, source)


def structure_line(  # noqa: PLR0911, PLR0913
    *,
    quantity: Decimal,
    unit: str | None,
    unit_size: str | None,
    raw_description: str | None,
    canonical_name: str | None,
    llm_measure: tuple[float | Decimal, str | None] | None = None,
    product_typical: tuple[Decimal, str] | None = None,
) -> StructuredMeasure:
    """Recover a line's structured measure through the trust-ordered layers.

    Order: sold-by-weight unit -> per-weight/volume pricing -> LLM size -> printed size text
    -> embedded text size (with OCR repair) -> per-each count sale -> product-typical
    inference -> none.
    """
    # 1. A weight/volume unit means the line was sold by that measure; quantity is the amount.
    unit_token = normalize_unit_token(unit)
    if unit_token is not None and UNIT_FACTORS[unit_token][0] in (WEIGHT, VOLUME):
        return StructuredMeasure(SoldBy.MEASURE, unit_token, None, None, MeasureSource.PRINTED)

    # 1b. Per-weight/volume pricing in the text ("$1.02 per lb") marks a sale priced by that
    # measure even when the parser did not put the unit in `unit`. The line quantity is the
    # amount purchased, so classify as MEASURE. This runs before the size layers so the weight
    # is never misread as a per-item size (which compute_measure would square into the base).
    measure_token = detect_measure_sale(raw_description)
    if measure_token is not None and quantity > 0:
        return StructuredMeasure(SoldBy.MEASURE, measure_token, None, None, MeasureSource.EXTRACTED)

    # 2. An LLM-recovered size is a per-item size (treated as printed-grade by the parser path).
    if llm_measure is not None:
        token = normalize_unit_token(llm_measure[1])
        if token is not None:
            return _item(Decimal(str(llm_measure[0])), token, MeasureSource.EXTRACTED)

    # 3. A size printed in the unit_size field.
    printed = parse_size(unit_size)
    if printed is not None:
        return _item(printed[0], printed[1], MeasureSource.PRINTED)

    # 4. A size embedded in the line text; REPAIRED when an OCR fix was needed to read it.
    for text in (raw_description, canonical_name):
        size = extract_size(text)
        if size is None:
            continue
        repaired = repair_size_token((text or "").lower()) != (text or "").lower()
        source = MeasureSource.REPAIRED if repaired else MeasureSource.EXTRACTED
        return _item(size[0], size[1], source)

    # 5. Per-each phrasing: an item line with no size (resolves to $/each downstream).
    if detect_count_sale(raw_description) and quantity > 0:
        return _item(None, None, MeasureSource.EXTRACTED)

    # 6. Inference from the product's learned typical size, stored as a base-unit size.
    if product_typical is not None and quantity > 0:
        value_base, dimension = product_typical
        return _item(value_base, _BASE_TOKEN[dimension], MeasureSource.INFERRED)

    # 7. Nothing recovered: an item with no size.
    return _item(None, None, MeasureSource.NONE)
