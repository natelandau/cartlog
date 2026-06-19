"""add line item normalization columns

Revision ID: a1b2c3d4e5f6
Revises: b72b4a0eb208
Create Date: 2026-06-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "b72b4a0eb208"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("line_items") as batch:
        batch.add_column(sa.Column("measure_quantity", sa.Numeric(12, 4), nullable=True))
        batch.add_column(sa.Column("measure_dimension", sa.String(length=10), nullable=True))
        batch.add_column(sa.Column("normalized_unit_price", sa.Numeric(12, 6), nullable=True))
        batch.add_column(
            sa.Column(
                "measure_status",
                sa.String(length=16),
                nullable=False,
                server_default="not_applicable",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("line_items") as batch:
        batch.drop_column("measure_status")
        batch.drop_column("normalized_unit_price")
        batch.drop_column("measure_dimension")
        batch.drop_column("measure_quantity")
