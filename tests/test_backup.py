"""Tests for the cartlog backup module."""

from __future__ import annotations

import re
import sqlite3

import pytest

from cartlog.backup import BackupError, _resolve_output_path, _snapshot_database, _source_db_path

_NAME_RE = re.compile(r"^cartlog-backup-\d{8}-\d{6}\.tar\.gz$")


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


def test_resolve_output_path_generates_timestamped_name_in_directory(tmp_path):
    """Generate a timestamped filename in the given directory."""
    target = _resolve_output_path(tmp_path)
    assert target.parent == tmp_path
    assert _NAME_RE.match(target.name)


def test_resolve_output_path_uses_explicit_file_verbatim(tmp_path):
    """Use an explicit file path as the target verbatim."""
    explicit = tmp_path / "my-backup.tar.gz"
    assert _resolve_output_path(explicit) == explicit


def test_resolve_output_path_refuses_existing_file(tmp_path):
    """Refuse to overwrite an existing file."""
    existing = tmp_path / "existing.tar.gz"
    existing.write_bytes(b"x")
    with pytest.raises(BackupError, match="Refusing to overwrite"):
        _resolve_output_path(existing)


def test_snapshot_database_copies_rows_without_sidecars(tmp_path):
    """Copy rows without sidecars using VACUUM INTO."""
    source = tmp_path / "cartlog.db"
    _make_sqlite_db(source, rows=5)
    dest = tmp_path / "snapshot.db"

    _snapshot_database(source, dest)

    # The snapshot is a single file: no -wal / -shm sidecars beside it.
    assert dest.is_file()
    assert not dest.with_name(dest.name + "-wal").exists()
    assert not dest.with_name(dest.name + "-shm").exists()

    # And it holds the same rows as the source.
    conn = sqlite3.connect(dest)
    try:
        count = conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    finally:
        conn.close()
    assert count == 5
