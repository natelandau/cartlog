"""add folder_ingest_config

Revision ID: 6f9493d28254
Revises: a1b2c3d4e5f6
Create Date: 2026-06-19 12:05:03.684675

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6f9493d28254"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "folder_ingest_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("watch_dir", sa.String(length=1024), nullable=True),
        sa.Column(
            "processed_subdir",
            sa.String(length=255),
            nullable=False,
            server_default="processed",
        ),
        sa.Column(
            "failed_subdir",
            sa.String(length=255),
            nullable=False,
            server_default="failed",
        ),
        sa.Column("poll_interval", sa.Float(), nullable=False, server_default="10.0"),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("folder_ingest_config")
