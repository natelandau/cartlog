"""Run the focused LLM size extractor over lines that still lack a resolvable size.

Mirrors categories/reclassify.py: each eligible line is sent to the extractor, an attempt is
spent whether or not a size comes back, and a returned size is applied via resolve_line_measure
so the line gains a normalized, comparable measure. The per-line attempt cap stops us paying to
re-examine a genuinely size-less line on every run.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from cartlog.constants import DEFAULT_MAX_SIZE_EXTRACT_ATTEMPTS
from cartlog.parsing.size_extractor import LineToSize
from cartlog.units import (
    MeasureSource,
    MeasureStatus,
    format_size_text,
    resolve_line_measure,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.usage import RunUsage
    from sqlalchemy.orm import Session

    from cartlog.db.models import LineItem, Receipt
    from cartlog.parsing.size_extractor import SizeExtractor


def _is_eligible(line: LineItem, max_attempts: int) -> bool:
    """A line is eligible for LLM size extraction when unresolved, not manual, and under cap."""
    return (
        line.measure_status != MeasureStatus.RESOLVED
        and line.measure_source != MeasureSource.MANUAL
        and line.size_extract_attempts < max_attempts
    )


def extract_sizes_for_lines(
    session: Session,
    lines: Sequence[LineItem],
    extractor: SizeExtractor | None,
    *,
    max_attempts: int = DEFAULT_MAX_SIZE_EXTRACT_ATTEMPTS,
    usage: RunUsage | None = None,
) -> int:
    """Recover sizes for eligible lines with the extractor; return the count newly resolved.

    Increments size_extract_attempts on every eligible line (hit or miss) so exhaustion is
    durable. Flushes; the caller owns the commit. A None extractor (no model configured) is a
    no-op that leaves attempt counters untouched.

    Args:
        session: The session to mutate.
        lines: The line items to sweep.
        extractor: The LLM size extractor. When None, nothing is resolved.
        max_attempts: Per-line cap on extraction attempts.
        usage: Optional accumulator; when provided, the extractor's token counts are added to it.

    Returns:
        The number of lines newly resolved during this sweep.
    """
    if extractor is None:
        return 0
    eligible = [line for line in lines if _is_eligible(line, max_attempts)]
    if not eligible:
        return 0

    requests = [
        LineToSize(
            key=str(line.id),
            canonical_name=line.product.canonical_name,
            raw_description=line.raw_description,
        )
        for line in eligible
    ]
    answers = extractor.extract(requests, usage=usage)

    resolved = 0
    for line in eligible:
        line.size_extract_attempts += 1  # spend an attempt regardless of outcome
        size = answers.get(str(line.id))
        if size is None:
            continue
        out = resolve_line_measure(
            quantity=line.quantity,
            unit=line.unit,
            unit_size=line.unit_size,
            raw_description=line.raw_description,
            canonical_name=line.product.canonical_name,
            line_total=line.line_total,
            llm_measure=(size.value, size.unit),
        )
        if out.result.measure_status == MeasureStatus.RESOLVED:
            # The orchestrator's unit_size_out echoes the original (still blank) unit_size, so
            # persist a textual size derived from the recovered measure. Without this the next
            # backfill pass 1 finds no structured size and downgrades the row.
            line.unit_size = out.unit_size_out or format_size_text(
                Decimal(str(size.value)), size.unit
            )
            line.measure_quantity = out.result.measure_quantity
            line.measure_dimension = out.result.measure_dimension
            line.normalized_unit_price = out.result.normalized_unit_price
            line.measure_status = out.result.measure_status
            line.measure_source = MeasureSource.EXTRACTED
            resolved += 1
    session.flush()
    return resolved


def extract_sizes_receipt(
    session: Session,
    receipt: Receipt,
    extractor: SizeExtractor | None,
    *,
    max_attempts: int = DEFAULT_MAX_SIZE_EXTRACT_ATTEMPTS,
    usage: RunUsage | None = None,
) -> int:
    """Run the size sweep over one receipt's lines (used during ingestion).

    Args:
        session: The session to mutate.
        receipt: The receipt whose lines are swept.
        extractor: The LLM size extractor. When None, nothing is resolved.
        max_attempts: Per-line cap on extraction attempts.
        usage: Optional accumulator for token tracking.

    Returns:
        The number of lines newly resolved during this sweep.
    """
    return extract_sizes_for_lines(
        session, list(receipt.line_items), extractor, max_attempts=max_attempts, usage=usage
    )
