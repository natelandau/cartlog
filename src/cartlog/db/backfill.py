"""Idempotent data backfills run at startup to keep stored rows consistent with current logic."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cartlog.db.models import LineItem
from cartlog.units import normalize_line_item

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def normalize_existing_measures(session: Session) -> int:
    """Recompute normalization for every line from stored unit/unit_size; return rows changed.

    Deterministic only: existing rows carry no LLM measure, so this resolves from unit_size.
    Idempotent. The caller owns the transaction (commit is not called here).

    Args:
        session: An active SQLAlchemy session. The caller is responsible for committing.

    Returns:
        The number of rows whose normalization columns were updated.
    """
    changed = 0
    for line in session.query(LineItem).all():
        norm = normalize_line_item(
            quantity=line.quantity,
            unit=line.unit,
            unit_size=line.unit_size,
            line_total=line.line_total,
            # Omit llm_measure so the backfill uses only deterministic stored fields.
        )
        if (
            line.measure_quantity != norm.measure_quantity
            or line.measure_dimension != norm.measure_dimension
            or line.normalized_unit_price != norm.normalized_unit_price
            or line.measure_status != norm.measure_status
        ):
            line.measure_quantity = norm.measure_quantity
            line.measure_dimension = norm.measure_dimension
            line.normalized_unit_price = norm.normalized_unit_price
            line.measure_status = norm.measure_status
            changed += 1
    # Flush so callers can session.refresh() and see updates within the same transaction
    # before the caller commits. The caller still owns the final commit.
    session.flush()
    return changed
