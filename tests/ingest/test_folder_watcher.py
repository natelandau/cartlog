"""Tests for the watch-folder ingestion poller."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from cartlog.db.models import FolderIngestConfig, IngestionJob, JobStatus
from cartlog.ingest.folder_watcher import (
    _move_to_unique,
    get_folder_config,
    scan_folder_once,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_get_folder_config_creates_singleton_with_defaults(session):
    """Verify the accessor creates a single default-valued config row."""
    # When fetching the config for the first time
    config = get_folder_config(session)

    # Then a default row exists with id 1 and the expected defaults
    assert config.id == 1
    assert config.enabled is False
    assert config.processed_subdir == "processed"
    assert config.failed_subdir == "failed"
    assert config.poll_interval == 10.0
    assert config.settle_seconds == 5.0


def test_get_folder_config_returns_same_row(session):
    """Verify repeated calls return the one singleton row, not new rows."""
    # Given an existing config with a watch dir set
    first = get_folder_config(session)
    first.watch_dir = "/data/inbox"
    session.commit()

    # When fetching again
    second = get_folder_config(session)

    # Then it is the same row
    assert second.id == 1
    assert second.watch_dir == "/data/inbox"


def _make_watch_dir(tmp_path: Path) -> tuple[Path, FolderIngestConfig]:
    watch = tmp_path / "inbox"
    watch.mkdir()
    config = FolderIngestConfig(id=1, enabled=True, watch_dir=str(watch), settle_seconds=5.0)
    return watch, config


def _drop(watch: Path, name: str, *, age_seconds: float) -> Path:
    path = watch / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    old = time.time() - age_seconds
    os.utime(path, (old, old))
    return path


def test_scan_enqueues_settled_file_and_moves_to_processed(session, tmp_path):
    """Verify a settled receipt file is enqueued and moved into processed/."""
    # Given a watch dir with a settled png
    watch, config = _make_watch_dir(tmp_path)
    session.add(config)
    session.commit()
    _drop(watch, "receipt.png", age_seconds=60)

    # When scanning once
    count = scan_folder_once(session, config, storage_dir=tmp_path / "storage", now=time.time())

    # Then a folder-sourced job exists and the file moved to processed/
    assert count == 1
    jobs = session.query(IngestionJob).all()
    assert [j.source for j in jobs] == ["folder"]
    assert jobs[0].status == JobStatus.PENDING
    assert not (watch / "receipt.png").exists()
    assert (watch / "processed" / "receipt.png").exists()


def test_scan_skips_files_within_settle_window(session, tmp_path):
    """Verify a freshly written file inside the settle window is left untouched."""
    # Given a watch dir with a just-written png
    watch, config = _make_watch_dir(tmp_path)
    session.add(config)
    session.commit()
    _drop(watch, "fresh.png", age_seconds=1)

    # When scanning once
    count = scan_folder_once(session, config, storage_dir=tmp_path / "storage", now=time.time())

    # Then nothing is enqueued and the file stays put
    assert count == 0
    assert (watch / "fresh.png").exists()
    assert session.query(IngestionJob).count() == 0


def test_scan_moves_failed_enqueue_to_failed(session, tmp_path, monkeypatch):
    """Verify a file whose enqueue raises is moved into failed/."""
    # Given a settled file and an enqueue that raises
    watch, config = _make_watch_dir(tmp_path)
    session.add(config)
    session.commit()
    _drop(watch, "bad.png", age_seconds=60)

    def _boom(*args: object, **kwargs: object) -> None:
        msg = "storage offline"
        raise RuntimeError(msg)

    monkeypatch.setattr("cartlog.ingest.folder_watcher.enqueue_job", _boom)

    # When scanning once
    count = scan_folder_once(session, config, storage_dir=tmp_path / "storage", now=time.time())

    # Then it is moved to failed/ and not counted
    assert count == 0
    assert (watch / "failed" / "bad.png").exists()


def test_scan_ignores_unsupported_suffix(session, tmp_path):
    """Verify files with unsupported suffixes are ignored, not moved."""
    # Given a settled .txt file
    watch, config = _make_watch_dir(tmp_path)
    session.add(config)
    session.commit()
    _drop(watch, "note.txt", age_seconds=60)

    # When scanning once
    count = scan_folder_once(session, config, storage_dir=tmp_path / "storage", now=time.time())

    # Then it is left in place
    assert count == 0
    assert (watch / "note.txt").exists()


def test_move_to_unique_suffixes_on_collision(tmp_path):
    """Verify a name collision in the destination yields a suffixed file name."""
    # Given a destination that already holds receipt.png
    dest = tmp_path / "processed"
    dest.mkdir()
    (dest / "receipt.png").write_bytes(b"existing")
    src = tmp_path / "receipt.png"
    src.write_bytes(b"new")

    # When moving the new file in
    moved = _move_to_unique(src, dest)

    # Then it lands under a non-colliding name
    assert moved == dest / "receipt-1.png"
    assert (dest / "receipt.png").read_bytes() == b"existing"
    assert moved.read_bytes() == b"new"
