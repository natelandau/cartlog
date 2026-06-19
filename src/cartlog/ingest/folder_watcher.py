"""Watch-folder ingestion: poll a directory and enqueue receipt files dropped into it."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from cartlog.clock import naive_utcnow
from cartlog.constants import SUPPORTED_SUFFIXES
from cartlog.db.models import FolderIngestConfig
from cartlog.ingest.queue import enqueue_job

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.orm import Session, sessionmaker

    from cartlog.config import Settings

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
    """Move `src` into `dest_dir`, appending -1, -2, ... if the name is already taken.

    Uses shutil.move rather than Path.rename so the move succeeds even when the destination
    is on a different filesystem than the source, which happens for synced watch folders
    (cloud mounts, network shares) where a plain rename raises OSError(EXDEV).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    candidate = dest_dir / src.name
    counter = 1
    while candidate.exists():
        candidate = dest_dir / f"{src.stem}-{counter}{src.suffix}"
        counter += 1
    shutil.move(src, candidate)
    return candidate


def _is_settled_receipt(
    entry: Path, *, reserved: set[str], settle_seconds: float, now: float
) -> bool:
    """Return whether `entry` is a settled receipt file ready to ingest.

    Returns False (and never raises) for the bookkeeping subdirs, unsupported suffixes, files
    still inside the settle window, and entries that vanished between the listing and the stat.
    """
    try:
        if not entry.is_file() or entry.name in reserved:
            return False
        if entry.suffix.lower() not in SUPPORTED_SUFFIXES:
            return False
        return now - entry.stat().st_mtime >= settle_seconds
    except OSError:
        # A sync client can move or delete a file between the listing and the stat; treat it
        # as not-ready this pass rather than letting the error abort the whole scan.
        logger.warning("Skipping %s: not accessible this pass", entry.name)
        return False


def _claim_and_enqueue(
    session: Session, entry: Path, *, processed_dir: Path, failed_dir: Path, storage_dir: Path
) -> bool:
    """Claim one file by moving it to processed/, enqueue it, and return whether it was enqueued.

    The file is moved out of the scan path before enqueuing, so an enqueue or move failure can
    never leave it where the next pass would re-enqueue (and duplicate) it. A file whose enqueue
    raises is moved on to failed/ so a poison file never blocks the folder.
    """
    try:
        claimed = _move_to_unique(entry, processed_dir)
    except OSError:
        logger.exception("Could not claim %s; leaving it for the next pass", entry.name)
        return False
    try:
        enqueue_job(session, src_path=claimed, source="folder", storage_dir=storage_dir)
    except Exception:
        logger.exception("Failed to enqueue %s; moving to failed", claimed.name)
        try:
            _move_to_unique(claimed, failed_dir)
        except OSError:
            logger.exception("Could not move %s to failed", claimed.name)
        return False
    return True


def scan_folder_once(
    session: Session,
    config: FolderIngestConfig,
    *,
    storage_dir: Path,
    now: float,
) -> int:
    """Enqueue every settled receipt file in the watch dir once; return the count enqueued.

    A file is "settled" when its modification time is at least `settle_seconds` in the
    past, so partially-synced files are not grabbed mid-write. Each settled file is first
    moved out of the scan path into the processed subdir (claiming it so a later failure can
    never leave it to be re-enqueued and duplicated), then enqueued; a file whose enqueue
    raises is moved on to the failed subdir so a poison file never blocks the folder. `now`
    is injected (epoch seconds) so the settle check is testable.

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
        if not _is_settled_receipt(
            entry, reserved=reserved, settle_seconds=config.settle_seconds, now=now
        ):
            continue
        if _claim_and_enqueue(
            session,
            entry,
            processed_dir=processed_dir,
            failed_dir=failed_dir,
            storage_dir=storage_dir,
        ):
            enqueued += 1
    return enqueued


def run_folder_watcher(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Continuously scan the watch folder, re-reading config each pass so UI edits apply live.

    Each pass opens its own session, loads the current config, and (when enabled) scans once.
    Errors are recorded on the config row and logged, never raised, so one bad pass cannot
    kill the thread. Sleeps `poll_interval` between passes, but checks `stop` first so a
    shutdown is not delayed by a full interval.

    Args:
        session_factory: Factory yielding sessions bound to the application database.
        settings: Runtime settings supplying the image storage directory.
        stop: Optional predicate checked each iteration; the loop exits when it returns True.
    """
    while stop is None or not stop():
        interval = 10.0
        with session_factory() as session:
            config = get_folder_config(session)
            interval = config.poll_interval
            if config.enabled and config.watch_dir:
                # Record the attempt time so the "Last polled" status is honest even when the
                # scan fails, not just on success.
                config.last_run_at = naive_utcnow()
                try:
                    scan_folder_once(
                        session,
                        config,
                        storage_dir=settings.image_storage_dir,
                        now=time.time(),
                    )
                    config.last_error = None
                except Exception as exc:
                    logger.exception("Watch-folder poll failed")
                    config.last_error = str(exc)
                session.commit()
        # Re-check stop before sleeping so shutdown is not delayed by a full poll interval.
        if stop is not None and stop():
            break
        # Guard against a misconfigured non-positive interval that would busy-loop (sleep 0)
        # or crash the thread (negative raises ValueError); the UI rejects these, but a row
        # edited out-of-band must not be able to take the poller down.
        time.sleep(interval if interval > 0 else 10.0)


@contextmanager
def folder_watcher(
    session_factory: sessionmaker[Session], settings: Settings
) -> Iterator[threading.Thread]:
    """Run the watch-folder poller in a daemon thread for the duration of the block.

    Always started by `serve`; the channel's enabled state lives in the database, so an
    unconfigured folder is simply a no-op poll. The thread is signaled to stop and joined
    on exit, mirroring the worker pool's lifecycle.
    """
    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_folder_watcher,
        kwargs={
            "session_factory": session_factory,
            "settings": settings,
            "stop": stop_event.is_set,
        },
        name="cartlog-folder-watcher",
        daemon=True,
    )
    thread.start()
    try:
        yield thread
    finally:
        stop_event.set()
        thread.join(timeout=5.0)
