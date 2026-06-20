"""Tests for the cartlog command-line interface."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

import uvicorn
from sqlalchemy import create_engine
from typer.testing import CliRunner

from cartlog import cli as cli_module
from cartlog.config import Settings
from cartlog.db.base import Base
from cartlog.db.models import IngestionJob, JobStatus
from cartlog.db.session import create_session_factory
from cartlog.ingest.queue import enqueue_job
from cartlog.web.templating import templates

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

runner = CliRunner()


def _temp_db_settings(tmp_path, **overrides) -> Settings:
    """Create the schema in a temp DB and return Settings for a one-shot CLI command.

    Disposes the setup engine so it leaves no open connection; pass test-specific fields
    (e.g. review_confidence_threshold, worker_poll_interval) as keyword overrides.
    """
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return Settings(
        database_url=database_url,
        image_storage_dir=tmp_path / "storage",
        **overrides,
    )


@contextmanager
def _verify_session(database_url: str) -> Iterator[Session]:
    """Yield a fresh session for post-command assertions, disposing the engine on exit."""
    factory = create_session_factory(database_url)
    try:
        with factory() as session:
            yield session
    finally:
        factory.kw["bind"].dispose()


def test_serve_command_runs_workers_and_bootstraps(tmp_path, monkeypatch, fake_parser):
    """Verify serve starts workers that drain the queue while the server runs."""
    # Given a temp DB with schema (real migrations are stubbed out below)
    settings = _temp_db_settings(tmp_path, worker_poll_interval=0.01)
    database_url = settings.database_url
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_parser", lambda _settings, _cats=None: fake_parser)
    monkeypatch.setattr(cli_module, "prepare_runtime", lambda _settings: None)
    # The CSS build shells out to the Node toolchain; stub it so serve tests stay hermetic.
    monkeypatch.setattr("cartlog.web.assets.build_css", lambda **_kwargs: None)

    # And a pending job the worker should process while the server "runs"
    src = tmp_path / "scan.png"
    src.write_bytes(b"\x89PNG fake")
    with _verify_session(database_url) as session:
        enqueue_job(session, src_path=src, source="web", storage_dir=settings.image_storage_dir)

    # And a fake server.run that blocks until the job is processed, like a live server
    def fake_run(_self):
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with _verify_session(database_url) as session:
                if session.query(IngestionJob).one().status == JobStatus.DONE:
                    return
            time.sleep(0.02)

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)

    # When invoking serve
    result = runner.invoke(cli_module.app, ["serve"])

    # Then it exits cleanly and the job was processed by the in-process worker
    assert result.exit_code == 0, result.output
    with _verify_session(database_url) as session:
        assert session.query(IngestionJob).one().status == JobStatus.DONE


def test_serve_dev_mode_enables_template_reload(tmp_path, monkeypatch, fake_parser):
    """Verify `serve --dev` runs the web app with template auto-reload enabled."""
    # Given a temp DB and stubbed bootstrap/parser, with the server returning immediately
    settings = _temp_db_settings(tmp_path, worker_poll_interval=0.01)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_parser", lambda _settings, _cats=None: fake_parser)
    monkeypatch.setattr(cli_module, "prepare_runtime", lambda _settings: None)
    # The CSS build shells out to the Node toolchain; stub it so serve tests stay hermetic.
    monkeypatch.setattr("cartlog.web.assets.build_css", lambda **_kwargs: None)
    monkeypatch.setattr(uvicorn.Server, "run", lambda _self: None)

    # When serving in dev mode
    result = runner.invoke(cli_module.app, ["serve", "--dev"])

    # Then it exits cleanly and the shared templates reload from disk on each render
    assert result.exit_code == 0, result.output
    assert templates.env.auto_reload is True


def test_serve_command_handles_keyboard_interrupt(tmp_path, monkeypatch, fake_parser):
    """Verify serve exits cleanly when the server raises KeyboardInterrupt."""
    # Given a temp DB and stubbed bootstrap/parser
    settings = _temp_db_settings(tmp_path, worker_poll_interval=0.01)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_parser", lambda _settings, _cats=None: fake_parser)
    monkeypatch.setattr(cli_module, "prepare_runtime", lambda _settings: None)
    # The CSS build shells out to the Node toolchain; stub it so serve tests stay hermetic.
    monkeypatch.setattr("cartlog.web.assets.build_css", lambda **_kwargs: None)

    def raise_keyboard_interrupt(_self):
        raise KeyboardInterrupt

    monkeypatch.setattr(uvicorn.Server, "run", raise_keyboard_interrupt)

    # When serving and the server is interrupted
    result = runner.invoke(cli_module.app, ["serve"])

    # Then the command exits cleanly without surfacing the interrupt
    assert result.exit_code == 0, result.output


def test_backup_command_writes_archive(tmp_path, monkeypatch):
    """Verify `backup` writes a tar.gz containing the db and images, and prints its path."""
    settings = _temp_db_settings(tmp_path)
    (settings.image_storage_dir).mkdir(parents=True, exist_ok=True)
    (settings.image_storage_dir / "r.jpg").write_bytes(b"\xff\xd8fake")
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = runner.invoke(cli_module.app, ["backup", "--output", str(out_dir)])

    assert result.exit_code == 0, result.output
    archives = list(out_dir.glob("cartlog-backup-*.tar.gz"))
    assert len(archives) == 1
    assert str(archives[0]) in result.output


def test_backup_command_rejects_non_sqlite_url(tmp_path, monkeypatch):
    """Verify `backup` exits non-zero for an unsupported database backend."""
    settings = Settings(
        database_url="postgresql://localhost/cartlog",
        image_storage_dir=tmp_path / "storage",
    )
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)

    result = runner.invoke(cli_module.app, ["backup", "--output", str(tmp_path / "b.tar.gz")])

    assert result.exit_code == 1
    assert "SQLite" in result.output


def test_backup_command_refuses_to_overwrite(tmp_path, monkeypatch):
    """Verify `backup` exits non-zero rather than clobbering an existing output file."""
    settings = _temp_db_settings(tmp_path)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    existing = tmp_path / "b.tar.gz"
    existing.write_bytes(b"x")

    result = runner.invoke(cli_module.app, ["backup", "--output", str(existing)])

    assert result.exit_code == 1
    assert "Refusing to overwrite" in result.output
