"""backfill and drop legacy measure columns

Revision ID: 79cdbf39248c
Revises: 9dfb068b8256
Create Date: 2026-06-22 09:01:58.497256

"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Union

import sqlalchemy as sa
from alembic import op

from cartlog.parsing.structuring import structure_line
from cartlog.units import MeasureSource, compute_measure

# revision identifiers, used by Alembic.
revision: str = "79cdbf39248c"
down_revision: Union[str, Sequence[str], None] = "9dfb068b8256"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LINES = sa.table(
    "line_items",
    sa.column("id", sa.Integer),
    sa.column("quantity", sa.Numeric),
    sa.column("unit", sa.String),
    sa.column("unit_size", sa.String),
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


def upgrade() -> None:
    """Populate structured measure columns from legacy unit/unit_size, then drop those columns."""
    bind = op.get_bind()
    rows = bind.execute(sa.select(LINES)).mappings().all()
    for row in rows:
        # Defensive Decimal conversion: SQLite may return floats for Numeric columns.
        s = structure_line(
            quantity=Decimal(str(row["quantity"])),
            unit=row["unit"],
            unit_size=row["unit_size"],
            raw_description=row["raw_description"],
            canonical_name=None,
        )
        norm = compute_measure(
            sold_by=s.sold_by,
            quantity=Decimal(str(row["quantity"])),
            measure_unit=s.measure_unit,
            size_amount=s.size_amount,
            size_unit=s.size_unit,
            line_total=Decimal(str(row["line_total"])),
        )
        # Preserve a human-pinned provenance; recompute its measure from migrated structure.
        source = (
            row["measure_source"]
            if row["measure_source"] == MeasureSource.MANUAL.value
            else s.source.value
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
                measure_source=source,
            )
        )
    # batch_alter_table is required for SQLite, which cannot drop columns in-place.
    with op.batch_alter_table("line_items") as batch:
        batch.drop_column("unit")
        batch.drop_column("unit_size")


def downgrade() -> None:
    """Restore legacy unit and unit_size columns (data is not recovered)."""
    with op.batch_alter_table("line_items") as batch:
        batch.add_column(sa.Column("unit", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("unit_size", sa.String(length=50), nullable=True))
