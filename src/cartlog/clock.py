"""Shared clock helper for the naive-UTC timestamps the database stores.

Centralizing this keeps the queue (writer) and web layer (reader) on one definition of
the database's CURRENT_TIMESTAMP clock, so elapsed-time math cannot drift between them.
"""

from __future__ import annotations

from datetime import UTC, datetime


def naive_utcnow() -> datetime:
    """Return a naive UTC timestamp matching the database's CURRENT_TIMESTAMP clock."""
    return datetime.now(UTC).replace(tzinfo=None)
