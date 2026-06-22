"""add structured measure columns

Revision ID: 9dfb068b8256
Revises: e7f93bc5409b
Create Date: 2026-06-22 08:57:05.378956

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9dfb068b8256"
down_revision: str | Sequence[str] | None = "e7f93bc5409b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add structured measure columns to line_items for the sold_by/measure_unit/size_amount/size_unit schema."""
    with op.batch_alter_table("line_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("sold_by", sa.String(length=8), nullable=False, server_default="item")
        )
        batch_op.add_column(sa.Column("measure_unit", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("size_amount", sa.Numeric(12, 4), nullable=True))
        batch_op.add_column(sa.Column("size_unit", sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Drop the structured measure columns from line_items."""
    with op.batch_alter_table("line_items", schema=None) as batch_op:
        batch_op.drop_column("size_unit")
        batch_op.drop_column("size_amount")
        batch_op.drop_column("measure_unit")
        batch_op.drop_column("sold_by")
