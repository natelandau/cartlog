"""Fixtures for receipt CLI tests: a temp-file database seeded with sample receipts."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.factories import seed_temp_db

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def seeded_db_url(tmp_path: Path) -> str:
    """Create a temp-file SQLite DB seeded with sample receipts and return its URL."""
    return seed_temp_db(tmp_path, "receipts.db")
