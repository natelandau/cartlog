"""Tests that Alembic migrations match the ORM models."""

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
