"""Fixtures for analytics tests.

The seed datasets and builders live in `tests.factories`; this module only exposes them as
pytest fixtures bound to in-memory databases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.factories import seed_dashboard_dataset, seed_receipts

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture
def analytics_session(session: Session) -> Session:
    """Yield the in-memory session pre-seeded with the analytics dataset."""
    seed_receipts(session)
    return session


@pytest.fixture
def dashboard_session(session: Session) -> Session:
    """Yield the in-memory session pre-seeded with the dashboard dataset."""
    seed_dashboard_dataset(session)
    return session
