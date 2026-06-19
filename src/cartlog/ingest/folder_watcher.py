"""Watch-folder ingestion: poll a directory and enqueue receipt files dropped into it."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from cartlog.constants import SUPPORTED_SUFFIXES
from cartlog.db.models import FolderIngestConfig
from cartlog.ingest.queue import enqueue_job

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def get_folder_config(session: Session) -> FolderIngestConfig:
    """Return the singleton watch-folder config, creating a default row on first access.

    The settings UI always needs a row to render, so the first read materializes one with
    safe defaults (disabled, no watch dir) and commits it.
    """
    config = session.get(FolderIngestConfig, 1)
    if config is None:
        config = FolderIngestConfig(id=1)
        session.add(config)
        session.commit()
    return config


def _move_to_unique(src: Path, dest_dir: Path) -> Path:
    """Move `src` into `dest_dir`, appending -1, -2, ... if the name is already taken."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    candidate = dest_dir / src.name
    counter = 1
    while candidate.exists():
        candidate = dest_dir / f"{src.stem}-{counter}{src.suffix}"
        counter += 1
    return src.rename(candidate)


def scan_folder_once(
    session: Session,
    config: FolderIngestConfig,
    *,
    storage_dir: Path,
    now: float,
) -> int:
    """Enqueue every settled receipt file in the watch dir once; return the count enqueued.

    A file is "settled" when its modification time is at least `settle_seconds` in the
    past, so partially-synced files are not grabbed mid-write. Enqueued files move to the
    processed subdir; files whose enqueue raises move to the failed subdir so a poison file
    never blocks the folder. `now` is injected (epoch seconds) so the settle check is testable.

    Args:
        session: Session used to enqueue jobs.
        config: The active watch-folder config.
        storage_dir: Directory where enqueue copies the durable file.
        now: Current time in epoch seconds, compared against each file's mtime.
    """
    if config.watch_dir is None:
        return 0
    watch = Path(config.watch_dir)
    if not watch.is_dir():
        return 0
    processed_dir = watch / config.processed_subdir
    failed_dir = watch / config.failed_subdir
    reserved = {config.processed_subdir, config.failed_subdir}

    enqueued = 0
    for entry in sorted(watch.iterdir()):
        if not entry.is_file() or entry.name in reserved:
            continue
        if entry.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        if now - entry.stat().st_mtime < config.settle_seconds:
            continue
        try:
            enqueue_job(session, src_path=entry, source="folder", storage_dir=storage_dir)
        except Exception:
            logger.exception("Failed to enqueue %s; moving to failed", entry.name)
            _move_to_unique(entry, failed_dir)
            continue
        _move_to_unique(entry, processed_dir)
        enqueued += 1
    return enqueued
