"""Create a single portable archive of cartlog's database and receipt images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
            f"Backups support only sqlite databases, but CARTLOG_DATABASE_URL is '{database_url}'."
        )
        raise BackupError(msg)
    path = Path(database_url.removeprefix("sqlite:///"))
    if not path.is_file():
        msg = f"Database file does not exist: {path}"
        raise BackupError(msg)
    return path
