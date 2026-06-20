"""Tests for the cartlog backup module."""

from __future__ import annotations

import sqlite3

import pytest

from cartlog.backup import BackupError, _source_db_path


def _make_sqlite_db(path, rows: int = 3) -> None:
    """Create a small WAL-mode SQLite db with `rows` rows in a `receipts` table."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE receipts (id INTEGER PRIMARY KEY, total REAL)")
        conn.executemany(
            "INSERT INTO receipts (total) VALUES (?)", [(float(i),) for i in range(rows)]
        )
        conn.commit()
    finally:
        conn.close()


def test_source_db_path_returns_existing_sqlite_file(tmp_path):
    """Return the filesystem path for a valid sqlite URL."""
    db = tmp_path / "cartlog.db"
    _make_sqlite_db(db)
    assert _source_db_path(f"sqlite:///{db}") == db


def test_source_db_path_rejects_non_sqlite_url(tmp_path):
    """Reject non-sqlite database URLs."""
    with pytest.raises(BackupError, match="SQLite"):
        _source_db_path("postgresql://localhost/cartlog")


def test_source_db_path_rejects_missing_file(tmp_path):
    """Reject sqlite URLs where the database file doesn't exist."""
    missing = tmp_path / "nope.db"
    with pytest.raises(BackupError, match="does not exist"):
        _source_db_path(f"sqlite:///{missing}")
