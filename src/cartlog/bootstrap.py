"""Prepare the runtime environment: storage directories and database migrations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config

from cartlog.db.backfill import normalize_existing_measures
from cartlog.db.seed import seed_app_config, seed_categories
from cartlog.db.session import create_session_factory

if TYPE_CHECKING:
    from cartlog.config import Settings


def prepare_runtime(settings: Settings) -> None:
    """Ensure the storage dir and database schema exist before the app starts.

    Idempotent and safe to call on every startup: it creates the image storage directory,
    pre-creates the SQLite database's parent directory (Alembic creates the file but not
    missing parents), and runs Alembic migrations to head.
    """
    settings.image_storage_dir.mkdir(parents=True, exist_ok=True)

    if settings.database_url.startswith("sqlite:///"):
        db_path = Path(settings.database_url.removeprefix("sqlite:///"))
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # `alembic upgrade head` is idempotent: it builds the schema on a fresh DB and no-ops
    # when already current. env.py reads the URL from settings, so none is set here.
    command.upgrade(Config("alembic.ini"), "head")

    # Seed the default category taxonomy after the schema exists. Seeding is additive and
    # idempotent: a fresh DB gets the full fixture, an existing one gains only new defaults.
    session_factory = create_session_factory(settings.database_url)
    try:
        with session_factory() as session:
            seed_categories(session)
            seed_app_config(session)
            # Recompute normalization for any rows left stale by older logic. Deterministic
            # and idempotent, so it is safe to run on every startup.
            normalize_existing_measures(session)
            session.commit()
    finally:
        session_factory.kw["bind"].dispose()
