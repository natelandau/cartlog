"""`cartlog db` commands: database seeding and maintenance."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer
from rich.console import Console

from cartlog.bootstrap import prepare_runtime
from cartlog.config import get_settings
from cartlog.db.models import LineItem
from cartlog.db.session import create_session_factory
from cartlog.units import normalize_line_item

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

db_app = typer.Typer(help="Database maintenance commands.", no_args_is_help=True)
console = Console()


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


@db_app.command()
def seed() -> None:
    """Ensure the schema exists, then add any categories missing from the fixture.

    Runs the same migrate-then-seed path as startup, so it works on a brand-new database as
    well as an existing one. Both steps are idempotent and seeding never removes rows.
    """
    prepare_runtime(get_settings())
    console.print("Categories seeded from the fixture.")


@db_app.command("normalize-measures")
def normalize_measures() -> None:
    """Backfill normalized measure columns for all existing line items.

    Deterministic, idempotent backfill that recomputes the four normalization columns
    from stored unit/unit_size without calling the LLM. Safe to run multiple times.
    """
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    try:
        with session_factory() as session:
            changed = normalize_existing_measures(session)
            session.commit()
        console.print(f"Normalized {changed} line item(s).")
    finally:
        session_factory.kw["bind"].dispose()
