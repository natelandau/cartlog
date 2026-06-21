"""Write the append-only parse cost ledger from the ingestion pipeline."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from cartlog.db.models import ParseCostEvent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _add_costs(existing: Decimal | None, addition: Decimal | None) -> Decimal | None:
    """Sum two optional costs, treating None as 'no data' rather than zero.

    Returns None only when both inputs are None, so an event with no priceable call keeps a
    null cost while an event with one priced call keeps that call's cost.
    """
    if existing is None and addition is None:
        return None
    return (existing or Decimal(0)) + (addition or Decimal(0))


def record_parse_cost(
    session: Session,
    *,
    job_id: int | None,
    input_tokens: int,
    output_tokens: int,
    model: str | None,
    cost: Decimal | None,
) -> ParseCostEvent:
    """Insert a cost event for the parse call and commit immediately. Returns the event.

    Committed in its own transaction (record-on-spend) so tokens billed by the provider
    survive a later-step failure and rollback, keeping the monthly cost figure honest. The
    returned event is updated with classify usage by `record_classify_cost`.
    """
    event = ParseCostEvent(
        job_id=job_id,
        parse_input_tokens=input_tokens,
        parse_output_tokens=output_tokens,
        parse_model=model,
        estimated_cost_usd=cost,
    )
    session.add(event)
    session.commit()
    return event


def record_classify_cost(
    session: Session,
    event: ParseCostEvent,
    *,
    input_tokens: int,
    output_tokens: int,
    model: str | None,
    cost: Decimal | None,
) -> None:
    """Add the classify call's usage and cost onto an existing parse cost event. Commits.

    Adds onto estimated_cost_usd (already holding the parse cost) via _add_costs so a missing
    price on either call still leaves the other call's cost intact.
    """
    event.classify_input_tokens = input_tokens
    event.classify_output_tokens = output_tokens
    event.classify_model = model
    event.estimated_cost_usd = _add_costs(existing=event.estimated_cost_usd, addition=cost)
    session.commit()


def record_size_extract_cost(
    session: Session,
    event: ParseCostEvent,
    *,
    input_tokens: int,
    output_tokens: int,
    model: str | None,
    cost: Decimal | None,
) -> None:
    """Add the size-extraction call's usage and cost onto an existing parse cost event. Commits."""
    event.size_extract_input_tokens = input_tokens
    event.size_extract_output_tokens = output_tokens
    event.size_extract_model = model
    event.estimated_cost_usd = _add_costs(existing=event.estimated_cost_usd, addition=cost)
    session.commit()


def record_standalone_size_extract_cost(
    session: Session,
    *,
    input_tokens: int,
    output_tokens: int,
    model: str | None,
    cost: Decimal | None,
) -> ParseCostEvent:
    """Insert a job-less cost event for size-extraction spend during backfill. Commits.

    Backfill has no ingestion job, so the event carries job_id=None and only the size-extract
    columns, keeping the monthly cost figure complete.
    """
    event = ParseCostEvent(
        job_id=None,
        size_extract_input_tokens=input_tokens,
        size_extract_output_tokens=output_tokens,
        size_extract_model=model,
        estimated_cost_usd=cost,
    )
    session.add(event)
    session.commit()
    return event
