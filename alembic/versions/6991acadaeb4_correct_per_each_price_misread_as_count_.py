"""correct per-each price misread as count size

Revision ID: 6991acadaeb4
Revises: 79cdbf39248c
Create Date: 2026-06-27 13:25:23.147075

Earlier ingests read a per-each *price* in a line's text ("$1.39 each") as a per-item
*size* of "1.39 each", because the deterministic size scanner accepted "each"/"ea" as a
count unit. compute_measure then multiplied that price into the base measure, so the
normalized unit price collapsed to a meaningless $1.00/ea (line_total / line_total).

This one-time data fix re-derives the affected rows with the corrected logic. It is scoped
to the exact bug signature (`size_unit` in each/ea, non-manual, non-inferred): the fix only
changed each/ea handling, so real ct/weight/volume sizes are untouched, and an LLM-recovered
"package size of N each" is nonsensical, so every each/ea size is the deterministic misread.
A fresh database ingested with the fix in place has no such rows, so this no-ops there.
"""

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from cartlog.parsing.structuring import structure_line
from cartlog.units import compute_measure

# revision identifiers, used by Alembic.
revision: str = "6991acadaeb4"
down_revision: str | Sequence[str] | None = "79cdbf39248c"
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
    """Re-derive rows whose each/ea size was a misread per-each price (deterministic, no LLM)."""
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
