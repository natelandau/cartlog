"""Database engine and session factory construction."""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

# Milliseconds SQLite waits on a locked database before raising 'database is locked'. Gives a
# brief writer (web upload or worker commit) time to finish when another writer is mid-commit.
_SQLITE_BUSY_TIMEOUT_MS = 5000


def _apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:  # noqa: ANN401
    """Enable WAL journaling and a busy timeout on each new SQLite connection.

    `cartlog serve` runs the web server and ingestion workers against one SQLite file
    concurrently; WAL lets readers and a writer proceed together, and the busy timeout
    absorbs brief write-lock contention instead of failing instantly.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    """Create a Session factory bound to a fresh engine for the given URL.

    For SQLite, tunes each connection for the concurrent reader/writer access that
    `cartlog serve` introduces (WAL journaling plus a busy timeout).
    """
    engine = create_engine(database_url)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _apply_sqlite_pragmas)
    return sessionmaker(bind=engine, expire_on_commit=False)
