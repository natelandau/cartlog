"""Tests that Alembic migrations match the ORM models."""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from cartlog.bootstrap import prepare_runtime
from cartlog.config import Settings

_EVENT_COLUMNS = {
    "id",
    "created_at",
    "job_id",
    "parse_input_tokens",
    "parse_output_tokens",
    "classify_input_tokens",
    "classify_output_tokens",
    "parse_model",
    "classify_model",
    "estimated_cost_usd",
}


def test_migration_creates_parse_cost_events(tmp_path, monkeypatch):
    """Verify a migrated database has the parse_cost_events ledger table."""
    # Given settings pointing at a fresh temp database
    db_path = tmp_path / "data" / "cartlog.db"
    settings = Settings(
        database_url=f"sqlite:///{db_path}",
        image_storage_dir=tmp_path / "imgs",
    )
    monkeypatch.setattr("cartlog.config.get_settings", lambda: settings)

    # When the runtime migrates the database to head
    prepare_runtime(settings)

    # Then the ledger table exists with every column
    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    assert "parse_cost_events" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("parse_cost_events")}
    engine.dispose()
    assert columns >= _EVENT_COLUMNS


def test_auth_tables_exist_after_upgrade(tmp_path, monkeypatch):
    """Verify running migrations to head creates the auth tables and user columns."""
    # Given settings pointing at a fresh temp database.
    # We patch get_settings directly because it is lru_cache'd and env-var changes alone
    # would not bust the cache populated by earlier tests in the same process.
    db = tmp_path / "m.db"
    settings = Settings(
        database_url=f"sqlite:///{db}",
        image_storage_dir=tmp_path / "imgs",
        secret_key="x" * 32,
    )
    monkeypatch.setattr("cartlog.config.get_settings", lambda: settings)

    # When migrations are run to head
    command.upgrade(Config("alembic.ini"), "head")

    # Then auth tables and updated user columns exist
    engine = create_engine(f"sqlite:///{db}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    user_cols = {c["name"] for c in inspector.get_columns("users")}
    engine.dispose()
    assert {"users", "sessions", "api_tokens", "app_config"} <= tables
    assert {"username", "role", "is_active", "must_change_password"} <= user_cols
