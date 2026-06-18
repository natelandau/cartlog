"""add parse_cost_events ledger

Revision ID: b72b4a0eb208
Revises: 39363b6358bf
Create Date: 2026-06-18 15:55:18.646150

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b72b4a0eb208"
down_revision: str | Sequence[str] | None = "39363b6358bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "parse_cost_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("parse_input_tokens", sa.Integer(), nullable=True),
        sa.Column("parse_output_tokens", sa.Integer(), nullable=True),
        sa.Column("classify_input_tokens", sa.Integer(), nullable=True),
        sa.Column("classify_output_tokens", sa.Integer(), nullable=True),
        sa.Column("parse_model", sa.String(length=255), nullable=True),
        sa.Column("classify_model", sa.String(length=255), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("parse_cost_events")
