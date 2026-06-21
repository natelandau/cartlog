"""Idempotent data backfills run at startup to keep stored rows consistent with current logic."""

from __future__ import annotations

import dataclasses
import logging
from collections import Counter
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic_ai.usage import RunUsage
from sqlalchemy.orm import joinedload, selectinload

from cartlog.constants import DEFAULT_MAX_SIZE_EXTRACT_ATTEMPTS, UNIT_FACTORS, VOLUME, WEIGHT
from cartlog.db.models import LineItem, Product
from cartlog.ingest.cost import record_standalone_size_extract_cost
from cartlog.parsing.pricing import estimate_cost
from cartlog.sizes.extract import extract_sizes_for_lines
from cartlog.units import (
    MeasureSource,
    MeasureStatus,
    ResolvedMeasure,
    normalize_unit_token,
    resolve_line_measure,
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

    # Pass 1: deterministic re-resolution (printed/extracted/repaired/count). No inference yet.
    # MANUAL is human-pinned and INFERRED is owned by pass 4, so neither is re-resolved here.
    # joinedload(product) avoids an N+1 lazy load of line.product.canonical_name below.
    for line in session.query(LineItem).options(joinedload(LineItem.product)).all():
        if line.measure_source in (MeasureSource.MANUAL, MeasureSource.INFERRED):
            continue
        out = resolve_line_measure(
            quantity=line.quantity,
            unit=line.unit,
            unit_size=line.unit_size,
            raw_description=line.raw_description,
            canonical_name=line.product.canonical_name,
            line_total=line.line_total,
        )
        # Preserve a recovered provenance: a line already RESOLVED as EXTRACTED/REPAIRED (with its
        # size persisted into unit_size) would otherwise re-resolve as PRINTED, losing the record
        # that the size was recovered rather than printed on the receipt.
        if (
            line.measure_status == MeasureStatus.RESOLVED
            and line.measure_source in (MeasureSource.EXTRACTED, MeasureSource.REPAIRED)
            and out.result.measure_status == MeasureStatus.RESOLVED
        ):
            out = dataclasses.replace(out, measure_source=line.measure_source)
        if _apply_resolved(line, out):
            changed += 1
    session.flush()

    # Pass 2: LLM size extraction for still-unresolved, under-cap, non-manual lines. Best-effort:
    # an extractor/network error must never crash `serve` at startup, so passes 3-4 still run.
    if size_extractor is not None:
        usage = RunUsage()
        try:
            unresolved = [
                line
                for line in session.query(LineItem).options(joinedload(LineItem.product)).all()
                if line.measure_status != MeasureStatus.RESOLVED
                and line.measure_source != MeasureSource.MANUAL
            ]
            extract_sizes_for_lines(
                session,
                unresolved,
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

    # Pass 4: infer for lines still unresolved after passes 1-2. Only non-RESOLVED, non-MANUAL
    # lines are touched, so already-inferred (RESOLVED) lines stay put and the run is stable.
    changed += infer_line_measures(
        session.query(LineItem).options(joinedload(LineItem.product)).all()
    )
    session.flush()
    return changed


def infer_line_measures(lines: Iterable[LineItem]) -> int:
    """Apply product-typical inference to unresolved lines; return the count changed.

    For each line that is not MANUAL, not already RESOLVED, and whose product carries a learned
    typical measure, re-resolve via the inference layer and write the result. Used both by the
    backfill's inference pass and by ingestion so a size learned earlier in a batch applies to its
    siblings immediately. The caller owns the flush/commit; the lines are mutated in place.

    Args:
        lines: The line items to consider for inference.

    Returns:
        The number of lines whose normalization columns changed.
    """
    changed = 0
    for line in lines:
        product = line.product
        if (
            line.measure_status == MeasureStatus.RESOLVED
            or line.measure_source == MeasureSource.MANUAL
            or product.typical_measure_value is None
            or product.typical_measure_dimension is None
        ):
            continue
        out = resolve_line_measure(
            quantity=line.quantity,
            unit=line.unit,
            unit_size=line.unit_size,
            raw_description=line.raw_description,
            canonical_name=product.canonical_name,
            line_total=line.line_total,
            product_typical=(product.typical_measure_value, product.typical_measure_dimension),
        )
        if _apply_resolved(line, out):
            changed += 1
    return changed


def _apply_resolved(line: LineItem, out: ResolvedMeasure) -> bool:
    """Write a ResolvedMeasure onto a line; return True if any column changed."""
    if (
        line.measure_quantity == out.result.measure_quantity
        and line.measure_dimension == out.result.measure_dimension
        and line.normalized_unit_price == out.result.normalized_unit_price
        and line.measure_status == out.result.measure_status
        and line.measure_source == out.measure_source
        and line.unit_size == out.unit_size_out
    ):
        return False
    line.unit_size = out.unit_size_out
    line.measure_quantity = out.result.measure_quantity
    line.measure_dimension = out.result.measure_dimension
    line.normalized_unit_price = out.result.normalized_unit_price
    line.measure_status = out.result.measure_status
    line.measure_source = out.measure_source
    return True


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
            unit_token = normalize_unit_token(line.unit)
            sold_by_measure = unit_token is not None and UNIT_FACTORS[unit_token][0] in (
                WEIGHT,
                VOLUME,
            )
            if (
                line.measure_status != MeasureStatus.RESOLVED
                or line.measure_source not in _LEARNABLE_SOURCES
                or line.measure_quantity is None
                or line.measure_dimension is None
                or line.quantity <= 0
                or sold_by_measure
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
