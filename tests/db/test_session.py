"""Tests for the session factory construction."""

from sqlalchemy import text

from cartlog.db.session import create_session_factory


def test_create_session_factory_connects():
    """Verify the session factory yields a session that can execute a query."""
    session_factory = create_session_factory("sqlite:///:memory:")

    with session_factory() as session:
        result = session.execute(text("SELECT 1")).scalar_one()

    # Close the in-memory connection so it does not trigger a ResourceWarning at GC time.
    session_factory.kw["bind"].dispose()

    assert result == 1


def test_create_session_factory_enables_sqlite_concurrency(tmp_path):
    """Verify file-backed SQLite engines use WAL journaling and a busy timeout."""
    # Given a file-based sqlite database (WAL is a no-op on :memory: connections)
    db_path = tmp_path / "pragmas.db"
    session_factory = create_session_factory(f"sqlite:///{db_path}")

    # When inspecting the connection pragmas
    with session_factory() as session:
        journal_mode = session.execute(text("PRAGMA journal_mode")).scalar()
        busy_timeout = session.execute(text("PRAGMA busy_timeout")).scalar()

    session_factory.kw["bind"].dispose()

    # Then WAL lets readers and writers proceed concurrently and the busy timeout
    # absorbs brief write-lock contention instead of failing with 'database is locked'.
    assert journal_mode == "wal"
    assert busy_timeout == 5000
