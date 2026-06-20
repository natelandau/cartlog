"""ingestion job uploader

Revision ID: fc67dfa1aa8c
Revises: 5f9629baa350
Create Date: 2026-06-19 17:55:18.895337

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fc67dfa1aa8c"
down_revision: str | Sequence[str] | None = "5f9629baa350"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add user_id FK to ingestion_jobs to record which user submitted the job."""
    with op.batch_alter_table("ingestion_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_ingestion_jobs_user_id", "users", ["user_id"], ["id"]
        )


def downgrade() -> None:
    """Remove user_id FK from ingestion_jobs."""
    with op.batch_alter_table("ingestion_jobs", schema=None) as batch_op:
        batch_op.drop_constraint("fk_ingestion_jobs_user_id", type_="foreignkey")
        batch_op.drop_column("user_id")
