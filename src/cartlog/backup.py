"""Create a single portable archive of cartlog's database and receipt images."""

from __future__ import annotations

import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cartlog.clock import naive_utcnow

if TYPE_CHECKING:
    from cartlog.config import Settings

_DB_ARCNAME = "cartlog.db"
_IMAGES_ARCNAME = "receipt_images"


class BackupError(Exception):
    """Raised when a backup cannot be created (bad config, missing source, or clobber)."""


@dataclass(frozen=True)
class BackupResult:
    """Outcome of a successful backup: the archive path and a short content summary."""

    path: Path
    database_bytes: int
    image_count: int


def _source_db_path(database_url: str) -> Path:
    """Return the SQLite file path for `database_url`, validating it is usable.

    Only `sqlite:///...` URLs are supported because the snapshot uses SQLite-specific
    `VACUUM INTO`; any other backend is a clear configuration error.
    """
    if not database_url.startswith("sqlite:///"):
        msg = (
            f"Backups support only SQLite databases, but CARTLOG_DATABASE_URL is '{database_url}'."
        )
        raise BackupError(msg)
    path = Path(database_url.removeprefix("sqlite:///"))
    if not path.is_file():
        msg = f"Database file does not exist: {path}"
        raise BackupError(msg)
    return path


def _resolve_output_path(output: Path | None) -> Path:
    """Resolve the archive's destination, refusing to overwrite an existing file.

    `None` writes a timestamped file into the current directory; an existing directory
    receives a timestamped file inside it; any other value is taken as the exact target.
    """
    name = f"cartlog-backup-{naive_utcnow():%Y%m%d-%H%M%S}.tar.gz"
    if output is None:
        target = Path.cwd() / name
    elif output.is_dir():
        target = output / name
    else:
        target = output
    if target.exists():
        msg = f"Refusing to overwrite existing file: {target}"
        raise BackupError(msg)
    return target


def _snapshot_database(source: Path, dest: Path) -> None:
    """Write a consistent, compacted snapshot of `source` to `dest` via VACUUM INTO.

    VACUUM INTO runs in a read transaction, so it captures committed data (including the
    WAL) into a single file with no -wal/-shm sidecars. This is safe to run while the app
    is serving and writing concurrently.
    """
    conn = sqlite3.connect(source)
    try:
        # The destination is bound as a parameter; VACUUM INTO evaluates it as an expression.
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()


def _add_images(tar: tarfile.TarFile, image_dir: Path) -> int:
    """Add `image_dir` to `tar` under the fixed `receipt_images` arcname.

    Always produces a `receipt_images/` directory entry — even when the source dir is
    missing — so the extracted layout is valid for the restore agent. Returns the number
    of regular files added.
    """
    if image_dir.is_dir():
        tar.add(image_dir, arcname=_IMAGES_ARCNAME)
        return sum(1 for p in image_dir.rglob("*") if p.is_file())

    placeholder = tarfile.TarInfo(name=f"{_IMAGES_ARCNAME}/")
    placeholder.type = tarfile.DIRTYPE
    placeholder.mode = 0o755
    tar.addfile(placeholder)
    return 0


def create_backup(settings: Settings, output: Path | None = None) -> BackupResult:
    """Create a single .tar.gz of the database snapshot and the receipt images.

    Validates configuration, snapshots the database with VACUUM INTO into a temp staging
    dir, then writes a fixed-layout archive (`cartlog.db` + `receipt_images/`). The staging
    dir is always removed; a partially written archive is removed on failure.

    Destination precedence: an explicit `output` wins; otherwise `settings.backup_dir` is used
    when configured; otherwise the archive lands in the current working directory.
    """
    source_db = _source_db_path(settings.database_url)
    target = _resolve_output_path(output if output is not None else settings.backup_dir)

    staging = Path(tempfile.mkdtemp(prefix="cartlog-backup-"))
    try:
        snapshot = staging / _DB_ARCNAME
        _snapshot_database(source_db, snapshot)
        database_bytes = snapshot.stat().st_size
        with tarfile.open(target, "w:gz") as tar:
            tar.add(snapshot, arcname=_DB_ARCNAME)
            image_count = _add_images(tar, settings.image_storage_dir)
    except (OSError, sqlite3.Error) as exc:
        # Translate operational failures (locked db, disk full, unwritable target) into
        # BackupError so callers handle them through the same path as configuration errors,
        # rather than leaking a raw traceback to the CLI or a 500 to the web UI.
        target.unlink(missing_ok=True)
        msg = f"Could not create the backup: {exc}"
        raise BackupError(msg) from exc
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return BackupResult(path=target, database_bytes=database_bytes, image_count=image_count)
