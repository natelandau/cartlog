"""auth and rbac

Revision ID: 5f9629baa350
Revises: 6f9493d28254
Create Date: 2026-06-19 15:11:18.777764

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5f9629baa350"
down_revision: str | Sequence[str] | None = "6f9493d28254"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Explicit SA Table definition for the users table as it exists after the initial migration.
# We pass this to batch_alter_table so that the unnamed UNIQUE on email is not carried forward
# into the rebuilt table (SQLite cannot drop unnamed constraints any other way).
_users_pre_auth = sa.Table(
    "users",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("email", sa.String(255), nullable=False),
    sa.Column("password_hash", sa.String(255), nullable=False),
    sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
)


def upgrade() -> None:
    """Upgrade schema to add auth, sessions, API tokens, and app config."""
    op.create_table(
        "app_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "allow_anonymous_read",
            sa.Boolean(),
            server_default=sa.text("(true())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("api_tokens", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_api_tokens_token_hash"), ["token_hash"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_api_tokens_user_id"), ["user_id"], unique=False)

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sessions_user_id"), ["user_id"], unique=False)

    # Batch is required for SQLite, which cannot ALTER columns in place; it copies the table.
    # copy_from supplies the pre-auth schema so Alembic knows the starting structure, which
    # allows it to drop the unnamed UNIQUE on email (unreachable by name in SQLite).
    # server_default on username/role satisfies NOT NULL during the table copy even though
    # this is a greenfield deploy with no existing rows; the defaults are only for DDL copy.
    with op.batch_alter_table("users", schema=None, copy_from=_users_pre_auth) as batch_op:
        batch_op.add_column(
            sa.Column("username", sa.String(length=255), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("role", sa.String(length=20), nullable=False, server_default="viewer")
        )
        batch_op.add_column(
            sa.Column(
                "is_active",
                sa.Boolean(),
                # Use '1' not true()/TRUE: SQLite's INSERT doesn't support function-based defaults.
                server_default="1",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                # Use '0' not false()/FALSE for the same reason as is_active above.
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("last_login_at", sa.DateTime(), nullable=True))
        batch_op.alter_column(
            "name",
            existing_type=sa.VARCHAR(length=255),
            nullable=True,
        )
        batch_op.alter_column(
            "email",
            existing_type=sa.VARCHAR(length=255),
            nullable=True,
        )
        # Login is by username, so email unique is no longer needed; named for reliable downgrade.
        batch_op.create_unique_constraint("uq_users_username", ["username"])


def downgrade() -> None:
    """Downgrade schema by removing auth tables and reverting user column changes."""
    # Define what the users table looks like after auth upgrade, so batch can diff correctly.
    _users_post_auth = sa.Table(
        "users",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(255), nullable=False, server_default=""),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    with op.batch_alter_table("users", schema=None, copy_from=_users_post_auth) as batch_op:
        batch_op.drop_constraint("uq_users_username", type_="unique")
        batch_op.alter_column(
            "email",
            existing_type=sa.VARCHAR(length=255),
            nullable=False,
        )
        batch_op.alter_column(
            "name",
            existing_type=sa.VARCHAR(length=255),
            nullable=False,
        )
        batch_op.drop_column("last_login_at")
        batch_op.drop_column("must_change_password")
        batch_op.drop_column("is_active")
        batch_op.drop_column("role")
        batch_op.drop_column("username")

    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sessions_user_id"))

    op.drop_table("sessions")

    with op.batch_alter_table("api_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_tokens_user_id"))
        batch_op.drop_index(batch_op.f("ix_api_tokens_token_hash"))

    op.drop_table("api_tokens")
    op.drop_table("app_config")
