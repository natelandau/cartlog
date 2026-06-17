"""Tests for runtime bootstrap."""

from sqlalchemy import create_engine, inspect

from cartlog.bootstrap import prepare_runtime
from cartlog.config import Settings


def test_prepare_runtime_creates_storage_and_schema(tmp_path, monkeypatch):
    """Verify prepare_runtime creates the storage dir and migrates a fresh database."""
    # Given settings pointing at a not-yet-existing db parent dir and storage dir
    db_path = tmp_path / "data" / "cartlog.db"
    storage_dir = tmp_path / "imgs"
    settings = Settings(
        database_url=f"sqlite:///{db_path}",
        image_storage_dir=storage_dir,
    )
    # alembic/env.py calls get_settings() itself; point it at the same temp settings
    monkeypatch.setattr("cartlog.config.get_settings", lambda: settings)

    # When preparing the runtime
    prepare_runtime(settings)

    # Then the storage dir exists and the database has the migrated schema
    assert storage_dir.is_dir()
    assert db_path.is_file()
    engine = create_engine(f"sqlite:///{db_path}")
    assert "ingestion_jobs" in inspect(engine).get_table_names()
    # And the default category taxonomy has been seeded (Uncategorized plus fixture entries)
    with engine.connect() as conn:
        count = conn.exec_driver_sql("SELECT COUNT(*) FROM categories").scalar()
    assert count is not None
    assert count > 1
    engine.dispose()
