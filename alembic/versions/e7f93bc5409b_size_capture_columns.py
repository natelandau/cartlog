"""size capture columns

Revision ID: e7f93bc5409b
Revises: fc67dfa1aa8c
Create Date: 2026-06-21 14:44:37.787438

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f93bc5409b"
down_revision: str | Sequence[str] | None = "fc67dfa1aa8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add provenance/attempt columns for size capture, the product typical size, and the
    size-extract cost columns.
    """
    with op.batch_alter_table("line_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("measure_source", sa.String(length=16), server_default="none", nullable=False)
        )
        batch_op.add_column(
            sa.Column("size_extract_attempts", sa.Integer(), server_default="0", nullable=False)
        )
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(sa.Column("typical_measure_value", sa.Numeric(12, 4), nullable=True))
        batch_op.add_column(
            sa.Column("typical_measure_dimension", sa.String(length=10), nullable=True)
        )
    with op.batch_alter_table("parse_cost_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("size_extract_input_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("size_extract_output_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("size_extract_model", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Drop the size-capture columns."""
    with op.batch_alter_table("parse_cost_events", schema=None) as batch_op:
        batch_op.drop_column("size_extract_model")
        batch_op.drop_column("size_extract_output_tokens")
        batch_op.drop_column("size_extract_input_tokens")
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_column("typical_measure_dimension")
        batch_op.drop_column("typical_measure_value")
    with op.batch_alter_table("line_items", schema=None) as batch_op:
        batch_op.drop_column("size_extract_attempts")
        batch_op.drop_column("measure_source")
