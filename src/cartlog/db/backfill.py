"""Idempotent data backfills run at startup to keep stored rows consistent with current logic."""

from __future__ import annotations

import logging
from collections import Counter
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic_ai.usage import RunUsage
from sqlalchemy.orm import joinedload, selectinload

from cartlog.constants import DEFAULT_MAX_SIZE_EXTRACT_ATTEMPTS
from cartlog.db.models import LineItem, Product
from cartlog.ingest.cost import record_standalone_size_extract_cost
from cartlog.parsing.pricing import estimate_cost
from cartlog.parsing.structuring import StructuredMeasure, structure_line
from cartlog.sizes.extract import extract_sizes_for_lines
from cartlog.units import (
    MeasureSource,
    MeasureStatus,
    NormalizationResult,
    SoldBy,
    compute_measure,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.orm import Session

    from cartlog.parsing.size_extractor import SizeExtractor

logger = logging.getLogger(__name__)

# Minimum sample agreement for a size to count as the product's typical size.
_DOMINANCE_RATIO = Decimal("0.70")
_MIN_OCCURRENCES = 2
# Sources we learn from; never learn from an inferred or manual value.
_LEARNABLE_SOURCES = frozenset(
    {MeasureSource.PRINTED, MeasureSource.EXTRACTED, MeasureSource.REPAIRED}
)


def normalize_existing_measures(
    session: Session,
    *,
    size_extractor: SizeExtractor | None = None,
    max_size_extract_attempts: int = DEFAULT_MAX_SIZE_EXTRACT_ATTEMPTS,
    assist_model: str | None = None,
) -> int:
    """Re-resolve every line's measure, recover missing sizes, learn typical sizes, and infer.

    Four idempotent, convergent passes. Pass 2 (LLM extraction) is the only one that no-ops when
    no extractor is configured; the deterministic passes always run. A line whose measure_source
    is MANUAL is never modified. Flushes; the caller commits.

    Args:
        session: Active session; the caller commits.
        size_extractor: Optional LLM size extractor for pass 2. None disables that pass and
            leaves attempt counters untouched.
        max_size_extract_attempts: Per-line cap forwarded to the size sweep.
        assist_model: Provider-prefixed id used to price pass-2 spend; None skips pricing.

    Returns:
        The number of line rows whose normalization columns changed across all passes.
    """
    changed = 0

    # Pass 1: deterministic re-resolution. MANUAL is human-pinned and INFERRED is owned by
    # pass 4, so neither is re-resolved here. joinedload(product) avoids N+1 lazy loads.
    # Two branches: lines that already carry a real measure get derived columns recomputed;
    # blank lines (size_amount and measure_unit both None) are re-derived from raw_description.
    for line in session.query(LineItem).options(joinedload(LineItem.product)).all():
        if line.measure_source in (MeasureSource.MANUAL, MeasureSource.INFERRED):
            continue
        if line.size_amount is not None or line.measure_unit is not None:
            # Already structured with a real measure; recompute derived columns only so
            # existing structured fields and source provenance are preserved, not downgraded.
            norm = compute_measure(
                sold_by=SoldBy(line.sold_by),
                quantity=line.quantity,
                measure_unit=line.measure_unit,
                size_amount=line.size_amount,
                size_unit=line.size_unit,
                line_total=line.line_total,
            )
            structured = StructuredMeasure(
                SoldBy(line.sold_by),
                line.measure_unit,
                line.size_amount,
                line.size_unit,
                MeasureSource(line.measure_source),
            )
            if _apply_structured(line, structured, norm):
                changed += 1
            continue
        # Blank line: re-derive all structured fields from raw_description/canonical_name.
        structured, norm = _resolve_and_compute(line)
        if _apply_structured(line, structured, norm):
            changed += 1
    session.flush()

    # Pass 2: LLM size extraction for still-unresolved, under-cap, non-manual lines. Best-effort:
    # an extractor/network error must never crash `serve` at startup, so passes 3-4 still run.
    if size_extractor is not None:
        usage = RunUsage()
        try:
            needs_size = [
                line
                for line in session.query(LineItem).options(joinedload(LineItem.product)).all()
                if line.sold_by == SoldBy.ITEM
                and line.size_amount is None
                and line.measure_source != MeasureSource.MANUAL
            ]
            extract_sizes_for_lines(
                session,
                needs_size,
                size_extractor,
                max_attempts=max_size_extract_attempts,
                usage=usage,
            )
            session.flush()
            _price_backfill_size_extract(session, usage, assist_model)
        except Exception:  # noqa: BLE001  # size recovery is best-effort; never fail startup
            logger.warning("Backfill size extraction failed; continuing without it", exc_info=True)

    # Pass 3: learn each product's dominant typical size from resolved, non-inferred lines.
    recompute_product_typical_sizes(session)

    # Pass 4: infer for ITEM lines still lacking a per-item size after passes 1-2. MANUAL and
    # already-sized lines are skipped, so lines with a real size stay unchanged.
    changed += infer_line_measures(
        session.query(LineItem).options(joinedload(LineItem.product)).all()
    )
    session.flush()
    return changed


def infer_line_measures(lines: Iterable[LineItem]) -> int:
    """Apply product-typical inference to ITEM lines that still lack a per-item size.

    Targets sold-per-item lines with no size_amount: lines sold loose by weight/volume already
    carry their measure in measure_unit and need no per-item inference. Skips MANUAL (user-pinned)
    and lines whose size is already known (size_amount is not None). Used both by the backfill's
    inference pass and by ingestion so a size learned earlier in a batch immediately applies to
    its siblings. The caller owns the flush/commit; the lines are mutated in place.

    Args:
        lines: The line items to consider for inference.

    Returns:
        The number of lines whose normalization columns changed.
    """
    changed = 0
    for line in lines:
        product = line.product
        if (
            line.sold_by != SoldBy.ITEM
            or line.size_amount is not None
            or line.measure_source == MeasureSource.MANUAL
            or product.typical_measure_value is None
            or product.typical_measure_dimension is None
        ):
            continue
        structured, norm = _resolve_and_compute(
            line,
            product_typical=(product.typical_measure_value, product.typical_measure_dimension),
        )
        if _apply_structured(line, structured, norm):
            changed += 1
    return changed


def _apply_structured(
    line: LineItem,
    structured: StructuredMeasure,
    norm: NormalizationResult,
) -> bool:
    """Write structured and derived columns onto a line; return True if any column changed."""
    if (
        line.sold_by == structured.sold_by
        and line.measure_unit == structured.measure_unit
        and line.size_amount == structured.size_amount
        and line.size_unit == structured.size_unit
        and line.measure_quantity == norm.measure_quantity
        and line.measure_dimension == norm.measure_dimension
        and line.normalized_unit_price == norm.normalized_unit_price
        and line.measure_status == norm.measure_status
        and line.measure_source == structured.source
    ):
        return False
    line.sold_by = structured.sold_by
    line.measure_unit = structured.measure_unit
    line.size_amount = structured.size_amount
    line.size_unit = structured.size_unit
    line.measure_quantity = norm.measure_quantity
    line.measure_dimension = norm.measure_dimension
    line.normalized_unit_price = norm.normalized_unit_price
    line.measure_status = norm.measure_status
    line.measure_source = structured.source
    return True


def _resolve_and_compute(
    line: LineItem,
    *,
    product_typical: tuple[Decimal, str] | None = None,
) -> tuple[StructuredMeasure, NormalizationResult]:
    """Run structure_line then compute_measure for a line, optionally with a typical size hint.

    Used by both pass 1 (blank lines) and pass 4 (inference) so the full re-derivation
    pipeline is centralized in one place.

    Args:
        line: The line item to re-derive.
        product_typical: Optional (per_package_base_value, dimension) for inference.

    Returns:
        A (StructuredMeasure, NormalizationResult) pair ready for _apply_structured.
    """
    structured = structure_line(
        quantity=line.quantity,
        unit=None,
        unit_size=None,
        raw_description=line.raw_description,
        canonical_name=line.product.canonical_name,
        product_typical=product_typical,
    )
    norm = compute_measure(
        sold_by=structured.sold_by,
        quantity=line.quantity,
        measure_unit=structured.measure_unit,
        size_amount=structured.size_amount,
        size_unit=structured.size_unit,
        line_total=line.line_total,
    )
    return structured, norm


def _price_backfill_size_extract(
    session: Session, usage: RunUsage, assist_model: str | None
) -> None:
    """Record a job-less cost event for backfill size-extraction spend, best-effort."""
    if usage.input_tokens == 0 and usage.output_tokens == 0:
        return
    try:
        cost = estimate_cost(model=assist_model, usage=usage) if assist_model else None
        record_standalone_size_extract_cost(
            session,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model=assist_model,
            cost=cost,
        )
    except Exception:  # noqa: BLE001  # cost tracking must never fail the backfill
        logger.warning("Failed to record backfill size-extract cost", exc_info=True)


def recompute_product_typical_sizes(
    session: Session, product_ids: Iterable[int] | None = None
) -> int:
    """Learn each product's dominant per-package size from its resolved, non-inferred lines.

    For every product, collect the per-package base measure (measure_quantity / quantity) of
    its resolved lines whose measure_source is printed/extracted/repaired, pick the modal value
    per dimension, and store it only when it dominates (>= 70% of the sample and >= 2
    occurrences). Otherwise clear the product's typical size. Flushes; the caller commits.

    Args:
        session: An active SQLAlchemy session. The caller is responsible for committing.
        product_ids: When given, recompute only these products (e.g. the ones a just-ingested
            receipt touched) instead of scanning the whole catalog.

    Returns:
        The number of products whose typical size columns changed.
    """
    query = session.query(Product).options(selectinload(Product.line_items))
    if product_ids is not None:
        query = query.filter(Product.id.in_(list(product_ids)))
    changed = 0
    for product in query.all():
        samples: list[tuple[Decimal, str]] = []
        for line in product.line_items:
            # A line sold loose by weight/volume has no package: its per_package would be the
            # unit conversion factor (e.g. 453.592 g per lb), not a size, so exclude it.
            if (
                line.measure_status != MeasureStatus.RESOLVED
                or line.measure_source not in _LEARNABLE_SOURCES
                or line.measure_quantity is None
                or line.measure_dimension is None
                or line.quantity <= 0
                or line.sold_by == SoldBy.MEASURE
            ):
                continue
            per_package = (line.measure_quantity / line.quantity).quantize(Decimal("0.0001"))
            samples.append((per_package, line.measure_dimension))

        value, dimension = _dominant_sample(samples)
        if product.typical_measure_value != value or product.typical_measure_dimension != dimension:
            product.typical_measure_value = value
            product.typical_measure_dimension = dimension
            changed += 1
    session.flush()
    return changed


def _dominant_sample(samples: list[tuple[Decimal, str]]) -> tuple[Decimal | None, str | None]:
    """Return the modal (value, dimension) when it dominates the sample, else (None, None).

    Use to decide whether a product's most common size is reliable enough to treat as its
    typical size. Requires both a minimum occurrence count and a minimum share of the sample.

    Args:
        samples: Pairs of (per-package measure value, dimension) from learnable line items.

    Returns:
        The dominant (value, dimension) pair, or (None, None) when no value dominates.
    """
    if len(samples) < _MIN_OCCURRENCES:
        return None, None
    counts: Counter[tuple[Decimal, str]] = Counter(samples)
    (top, top_count) = counts.most_common(1)[0]
    if Decimal(top_count) / Decimal(len(samples)) >= _DOMINANCE_RATIO:
        return top
    return None, None
