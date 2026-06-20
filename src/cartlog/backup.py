"""Create a single portable archive of cartlog's database and receipt images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cartlog.clock import naive_utcnow

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
