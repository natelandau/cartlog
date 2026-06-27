"""re-clean per-each count sizes reinjected by llm

Revision ID: 69c08714c0c2
Revises: 6991acadaeb4
Create Date: 2026-06-27 15:27:30.705947

Migration 6991acadaeb4 re-derived per-each count lines to a size-less $/each measure, but on
that same startup the size backfill's LLM pass swept those now-size-less ITEM lines, the model
read the per-each price ("$2.93 each") as a "1 each" package size, and structure_line applied
it, so the misread "2 x 1 ea" size came straight back. The companion code change teaches the
LLM-size branch of structure_line to reject each/ea (as the deterministic scanner already does),
so a fresh re-derive now sticks. This one-time fix re-cleans the rows the LLM pass re-corrupted.

Scope mirrors 6991acadaeb4 exactly (`size_unit` in each/ea, non-manual, non-inferred): a real
ct/weight/volume size is untouched, and a "package size of N each" is nonsensical, so every
each/ea size is the misread. A database with no such rows no-ops.
"""

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from cartlog.parsing.structuring import structure_line
from cartlog.units import compute_measure

# revision identifiers, used by Alembic.
revision: str = "69c08714c0c2"
down_revision: str | Sequence[str] | None = "6991acadaeb4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LINES = sa.table(
    "line_items",
    sa.column("id", sa.Integer),
    sa.column("product_id", sa.Integer),
    sa.column("quantity", sa.Numeric),
    sa.column("raw_description", sa.String),
    sa.column("line_total", sa.Numeric),
    sa.column("measure_source", sa.String),
    sa.column("sold_by", sa.String),
    sa.column("measure_unit", sa.String),
    sa.column("size_amount", sa.Numeric),
    sa.column("size_unit", sa.String),
    sa.column("measure_quantity", sa.Numeric),
    sa.column("measure_dimension", sa.String),
    sa.column("normalized_unit_price", sa.Numeric),
    sa.column("measure_status", sa.String),
)
PRODUCTS = sa.table(
    "products",
    sa.column("id", sa.Integer),
    sa.column("canonical_name", sa.String),
)


def upgrade() -> None:
    """Re-derive rows whose each/ea size was an LLM-reinjected per-each price (deterministic)."""
    bind = op.get_bind()
    # Scope to the bug signature. MANUAL is human-pinned and INFERRED is owned by the typical-size
    # pass; neither is ever a deterministic each/ea misread, so both are left untouched.
    affected = (
        sa.select(LINES, PRODUCTS.c.canonical_name)
        .select_from(LINES.join(PRODUCTS, LINES.c.product_id == PRODUCTS.c.id))
        .where(
            LINES.c.size_unit.in_(("each", "ea")),
            LINES.c.measure_source.notin_(("manual", "inferred")),
        )
    )
    rows = bind.execute(affected).mappings().all()
    for row in rows:
        # Defensive Decimal conversion: SQLite may return floats for Numeric columns.
        s = structure_line(
            quantity=Decimal(str(row["quantity"])),
            unit=None,
            unit_size=None,
            raw_description=row["raw_description"],
            canonical_name=row["canonical_name"],
        )
        norm = compute_measure(
            sold_by=s.sold_by,
            quantity=Decimal(str(row["quantity"])),
            measure_unit=s.measure_unit,
            size_amount=s.size_amount,
            size_unit=s.size_unit,
            line_total=Decimal(str(row["line_total"])),
        )
        bind.execute(
            sa.update(LINES)
            .where(LINES.c.id == row["id"])
            .values(
                sold_by=s.sold_by.value,
                measure_unit=s.measure_unit,
                size_amount=s.size_amount,
                size_unit=s.size_unit,
                measure_quantity=norm.measure_quantity,
                measure_dimension=norm.measure_dimension,
                normalized_unit_price=norm.normalized_unit_price,
                measure_status=norm.measure_status.value,
                measure_source=s.source.value,
            )
        )


def downgrade() -> None:
    """No-op: the prior values were wrong (a misread price), so there is nothing to restore."""
