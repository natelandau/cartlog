"""Tests for the /healthz probe (DB reachable, migrations at head, workers alive)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cartlog.config import get_settings
from cartlog.web.app import create_app
from cartlog.web.routers.health import (
    _check_database,
    _check_migrations,
    _check_worker,
)

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient as TestClientType


@pytest.fixture
def migrated_db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Return the URL of a temp SQLite DB migrated to the current Alembic head.

    Runs the real migrations (env.py reads the URL from settings), so the resulting DB has a
    populated alembic_version table, unlike the create_all schema the other test fixtures use.
    """
    db_path = tmp_path / "migrated.db"
    monkeypatch.setenv("CARTLOG_DATABASE_URL", str(db_path))
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    get_settings.cache_clear()
    return f"sqlite:///{db_path}"


def _app_on(session_factory: sessionmaker) -> TestClientType:
    """Build a plain (no-lifespan) TestClient whose app talks to the given session factory."""
    app = create_app()
    # Not used as a context manager, so lifespan never runs and this factory is authoritative.
    app.state.session_factory = session_factory
    return TestClient(app)


# --- /healthz endpoint ------------------------------------------------------


def test_healthz_ok_when_healthy(migrated_db_url: str) -> None:
    """Verify /healthz returns 200 with every check green against a migrated DB."""
    # Given an app pointed at a fully migrated database
    engine = create_engine(migrated_db_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    client = _app_on(factory)

    # When the health probe is hit
    response = client.get("/healthz")

    # Then every check passes
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["migrations"]["ok"] is True
    assert body["checks"]["worker"]["ok"] is True

    engine.dispose()


def test_healthz_answers_directly_without_users(anon_client: TestClient) -> None:
    """Verify /healthz returns a real status code, not a 303 to /setup, when no user exists."""
    # Given the anon client's DB has no users (the setup gate would redirect a normal page) and
    # a create_all schema with no alembic_version, a direct 503 proves both the bypass and the
    # migration check.

    # When the health probe is hit without following redirects
    response = anon_client.get("/healthz", follow_redirects=False)

    # Then it is answered directly (not a redirect) and reports the unmigrated schema
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["migrations"]["ok"] is False


def test_healthz_unhealthy_when_workers_dead(migrated_db_url: str) -> None:
    """Verify /healthz returns 503 when the registered worker pool has no live threads."""
    # Given a migrated DB and a worker pool whose only thread has already exited
    engine = create_engine(migrated_db_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()

    app = create_app()
    app.state.session_factory = factory
    app.state.worker_threads = [dead]
    client = TestClient(app)

    # When the health probe is hit
    response = client.get("/healthz")

    # Then the worker check fails and the instance is unhealthy
    assert response.status_code == 503
    assert response.json()["checks"]["worker"]["ok"] is False

    engine.dispose()


# --- check helpers ----------------------------------------------------------


def test_check_database_ok(session_factory: sessionmaker) -> None:
    """Verify the database check passes against a live session factory."""
    # When the database check runs
    result = _check_database(session_factory)

    # Then it reports reachable
    assert result.ok is True


def test_check_database_reports_failure() -> None:
    """Verify the database check raises when the database cannot answer."""
    # The health handler wraps checks (via _run) so this raise surfaces as a 503, not a crash.
    # Given a session factory bound to an engine pointed at an unwritable path
    engine = create_engine("sqlite:////nonexistent-dir/does-not-exist.db")
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    # When the database check runs, Then it raises rather than reporting ok
    with pytest.raises(Exception):  # noqa: B017, PT011 - any DB error proves unreachability
        _check_database(factory)

    engine.dispose()


def test_check_migrations_ok_when_at_head(migrated_db_url: str) -> None:
    """Verify the migration check passes when the DB is stamped at head."""
    # Given a migrated database
    engine = create_engine(migrated_db_url)

    # When the migration check runs
    result = _check_migrations(engine)

    # Then it reports the schema is at head
    assert result.ok is True

    engine.dispose()


def test_check_migrations_not_ok_when_unmigrated(session_factory: sessionmaker) -> None:
    """Verify the migration check fails when the schema was built without Alembic."""
    # Given a create_all schema with no alembic_version table
    engine = session_factory.kw["bind"]

    # When the migration check runs
    result = _check_migrations(engine)

    # Then it reports the schema is not at head
    assert result.ok is False


def test_check_worker_not_monitored_when_unregistered() -> None:
    """Verify the worker check passes when no pool is registered (web-only / test context)."""
    # Given an app with no worker pool registered
    app = create_app()

    # When the worker check runs
    result = _check_worker(app)

    # Then it passes and reports it is not monitoring workers
    assert result.ok is True
    assert "not monitored" in result.detail


def test_check_worker_ok_when_thread_alive() -> None:
    """Verify the worker check passes while at least one worker thread is alive."""
    # Given an app with one live worker thread
    stop = threading.Event()
    alive = threading.Thread(target=stop.wait)
    alive.start()
    try:
        app = create_app()
        app.state.worker_threads = [alive]

        # When the worker check runs
        result = _check_worker(app)

        # Then it passes and counts the live thread
        assert result.ok is True
        assert result.detail == "1/1 alive"
    finally:
        stop.set()
        alive.join()


def test_check_worker_fails_when_all_dead() -> None:
    """Verify the worker check fails once every worker thread has exited."""
    # Given an app whose only worker thread has finished
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    app = create_app()
    app.state.worker_threads = [dead]

    # When the worker check runs
    result = _check_worker(app)

    # Then it fails
    assert result.ok is False
