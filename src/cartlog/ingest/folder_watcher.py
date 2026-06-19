"""Watch-folder ingestion: poll a directory and enqueue receipt files dropped into it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cartlog.db.models import FolderIngestConfig

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
