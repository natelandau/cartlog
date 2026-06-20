"""Seed the category taxonomy from the bundled fixture file (additive and idempotent)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from cartlog.categories.service import CategoryService
from cartlog.db.models import Category
from cartlog.normalization import normalize_text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# The fixture lives beside this module so it loads from the source tree in development,
# the same way the web templates are resolved by path rather than as packaged resources.
_FIXTURE_PATH = Path(__file__).parent / "categories.yaml"


def load_seed_categories(path: Path = _FIXTURE_PATH) -> list[str]:
    """Read category names from the seed fixture, dropping blanks and case-insensitive dupes.

    Each fixture entry is a bare category name. First occurrence wins so the file reads
    top-to-bottom.

    Args:
        path: The fixture file to read; defaults to the bundled categories.yaml.

    Returns:
        list[str]: The de-duplicated category names in file order.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    names: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        name = str(entry).strip()
        key = normalize_text(name)
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def seed_categories(session: Session, *, path: Path = _FIXTURE_PATH) -> int:
    """Add any fixture categories missing from the database; never remove or rename.

    Ensures the reserved Uncategorized system category exists, then inserts each fixture
    name that is not already present (case-insensitive). The caller owns the transaction.

    Args:
        session: The SQLAlchemy session to seed into.
        path: The fixture file to read; defaults to the bundled categories.yaml.

    Returns:
        int: The number of new categories inserted.
    """
    CategoryService(session).ensure_uncategorized()
    existing = {normalize_text(name) for (name,) in session.query(Category.name)}
    added = 0
    for name in load_seed_categories(path):
        key = normalize_text(name)
        if key not in existing:
            session.add(Category(name=name))
            existing.add(key)
            added += 1
    session.flush()
    return added


def seed_app_config(session: Session) -> None:
    """Ensure the singleton app_config row exists (open read access by default)."""
    from cartlog.db.models import AppConfig  # noqa: PLC0415

    if session.get(AppConfig, 1) is None:
        session.add(AppConfig(id=1))
