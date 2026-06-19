"""Tests for the cartlog command-line interface."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
import typer
import uvicorn
from sqlalchemy import create_engine
from typer.testing import CliRunner

from cartlog import cli as cli_module
from cartlog.config import Settings
from cartlog.db.base import Base
from cartlog.db.models import IngestionJob, JobStatus, Receipt
from cartlog.db.session import create_session_factory
from cartlog.ingest.queue import enqueue_job
from cartlog.ingest.worker import run_worker as real_run_worker
from cartlog.web.templating import templates
from tests.factories import seed_temp_db

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

runner = CliRunner()


def _make_bounded_run_worker(*, max_iterations: int):
    """Wrap the real run_worker with a stop callback so the CLI test terminates."""

    def bounded(session_factory, **kwargs):
        calls = {"n": 0}

        def stop() -> bool:
            calls["n"] += 1
            return calls["n"] > max_iterations

        kwargs.pop("stop", None)
        return real_run_worker(session_factory, stop=stop, **kwargs)

    return bounded


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


def test_ingest_command_batch_persists_all_receipts(
    tmp_path, monkeypatch, fake_parser, sample_parsed_receipt
):
    """Verify ingesting several files in one invocation persists a receipt for each."""
    # Given temp settings, the fake parser, and two image files
    settings = _temp_db_settings(tmp_path, review_confidence_threshold=0.7)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_parser", lambda _settings, _cats=None: fake_parser)
    first = tmp_path / "one.png"
    second = tmp_path / "two.png"
    first.write_bytes(b"\x89PNG one")
    second.write_bytes(b"\x89PNG two")

    # When ingesting both in one call
    result = runner.invoke(cli_module.app, ["ingest", str(first), str(second)])

    # Then both are ingested, the summary reports two, and the exit is clean
    assert result.exit_code == 0, result.output
    assert "2 ingested" in result.output
    with _verify_session(settings.database_url) as session:
        assert session.query(Receipt).count() == 2


def test_ingest_command_batch_continues_past_failure(tmp_path, monkeypatch, fake_parser):
    """Verify a failed file does not abort the batch and the command exits non-zero."""
    # Given two files where the second file's parse fails permanently
    settings = _temp_db_settings(tmp_path, review_confidence_threshold=0.7)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_parser", lambda _settings, _cats=None: fake_parser)
    first = tmp_path / "one.png"
    second = tmp_path / "two.png"
    first.write_bytes(b"\x89PNG one")
    second.write_bytes(b"\x89PNG two")

    real_process = cli_module.process_job
    calls = {"n": 0}

    def flaky_process(session, job, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            # Simulate a permanent parse failure for the second job.
            job.status = JobStatus.FAILED
            job.last_error = "boom"
            session.commit()
            return None
        return real_process(session, job, **kwargs)

    monkeypatch.setattr(cli_module, "process_job", flaky_process)

    # When ingesting both
    result = runner.invoke(cli_module.app, ["ingest", str(first), str(second)])

    # Then the good file is ingested, the batch reports one failure, and exit is non-zero
    assert result.exit_code == 1, result.output
    assert "1 ingested" in result.output
    assert "1 failed" in result.output
    with _verify_session(settings.database_url) as session:
        assert session.query(Receipt).count() == 1


def test_ingest_command_batch_no_wait_enqueues_all(tmp_path, monkeypatch, fake_parser):
    """Verify --no-wait enqueues every file without parsing any of them."""
    # Given temp settings and two files
    settings = _temp_db_settings(tmp_path, review_confidence_threshold=0.7)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_parser", lambda _settings, _cats=None: fake_parser)
    first = tmp_path / "one.png"
    second = tmp_path / "two.png"
    first.write_bytes(b"\x89PNG one")
    second.write_bytes(b"\x89PNG two")

    # When enqueuing both with --no-wait
    result = runner.invoke(cli_module.app, ["ingest", str(first), str(second), "--no-wait"])

    # Then two pending jobs exist and no receipts were produced
    assert result.exit_code == 0, result.output
    with _verify_session(settings.database_url) as session:
        assert session.query(IngestionJob).count() == 2
        assert session.query(Receipt).count() == 0


def test_ingest_command_persists_receipt(tmp_path, monkeypatch, fake_parser, sample_parsed_receipt):
    """Verify the ingest command parses a receipt and persists it to the database."""
    # Build settings pointing at a fresh temp DB and storage dir.
    settings = _temp_db_settings(tmp_path, review_confidence_threshold=0.7)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    # Use the fake parser instead of constructing a real anthropic-backed one.
    monkeypatch.setattr(cli_module, "build_parser", lambda _settings, _cats=None: fake_parser)

    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG fake")

    result = runner.invoke(cli_module.app, ["ingest", str(image)])

    assert result.exit_code == 0, result.output
    assert "Safeway" in result.output

    with _verify_session(settings.database_url) as session:
        assert session.query(Receipt).count() == 1


def test_ingest_command_defers_when_worker_claims_job(tmp_path, monkeypatch, fake_parser):
    """Verify ingest skips processing and persists no receipt when the claim is lost."""
    settings = _temp_db_settings(tmp_path, review_confidence_threshold=0.7)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_parser", lambda _settings, _cats=None: fake_parser)
    # Simulate a concurrent worker winning the job: the CLI's claim loses.
    monkeypatch.setattr(cli_module, "claim_job", lambda _session, _job: False)

    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG fake")

    result = runner.invoke(cli_module.app, ["ingest", str(image)])

    # Then the CLI reports the job is handled elsewhere and writes no receipt
    assert result.exit_code == 0, result.output
    assert "already being processed" in result.output

    with _verify_session(settings.database_url) as session:
        assert session.query(Receipt).count() == 0


def test_worker_command_processes_queued_job(tmp_path, monkeypatch, fake_parser):
    """Verify the worker command drains a pending job from the queue."""
    settings = _temp_db_settings(tmp_path, review_confidence_threshold=0.7)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "build_parser", lambda _settings, _cats=None: fake_parser)
    monkeypatch.setattr(cli_module, "run_worker", _make_bounded_run_worker(max_iterations=3))

    # Given a job enqueued directly into the temp DB
    src = tmp_path / "scan.png"
    src.write_bytes(b"\x89PNG fake")
    with _verify_session(settings.database_url) as session:
        enqueue_job(session, src_path=src, source="web", storage_dir=settings.image_storage_dir)

    # When running the worker command
    result = runner.invoke(cli_module.app, ["worker"])

    # Then it exits cleanly and the job is done
    assert result.exit_code == 0, result.output
    with _verify_session(settings.database_url) as session:
        job = session.query(IngestionJob).one()
        assert job.status == JobStatus.DONE


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


def test_build_model_returns_model_for_valid_id():
    """Verify the factory builds a model when the provider key is present."""
    # Given the autouse dummy key fixture has set ANTHROPIC_API_KEY
    # When building a model from a provider-prefixed id
    model = cli_module._build_model("anthropic:claude-opus-4-8")

    # Then a model object is returned
    assert model is not None


def test_build_model_raises_friendly_error_without_key(monkeypatch):
    """Verify a missing provider key surfaces as a typer.BadParameter, not a raw error."""
    # Given no Anthropic key in the environment
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # When building a model that needs that key, then a friendly CLI error is raised
    with pytest.raises(typer.BadParameter):
        cli_module._build_model("anthropic:claude-opus-4-8")


def test_ingest_command_errors_without_provider_key(tmp_path, monkeypatch):
    """Verify ingest fails fast with a friendly error when the provider key is unset."""
    # Given settings and no provider key in the environment
    settings = _temp_db_settings(tmp_path)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    receipt = tmp_path / "receipt.png"
    receipt.write_bytes(b"\x89PNG fake bytes")

    # When invoking ingest without --no-wait
    result = runner.invoke(cli_module.app, ["ingest", str(receipt)])

    # Then the command exits non-zero rather than parsing
    assert result.exit_code != 0
    # And no ingestion jobs were persisted, confirming the guard fires before any DB write
    with _verify_session(settings.database_url) as session:
        assert session.query(IngestionJob).count() == 0


def test_export_command_writes_csv(tmp_path, monkeypatch):
    """Verify `cartlog export` writes a CSV file covering every line item."""
    # Given a seeded temp database and settings pointing at it
    db_url = seed_temp_db(tmp_path, "export.db")
    monkeypatch.setattr(cli_module, "get_settings", lambda: Settings(database_url=db_url))
    out = tmp_path / "out.csv"

    # When exporting to CSV
    result = runner.invoke(cli_module.app, ["export", "-o", str(out), "-f", "csv"])

    # Then the command succeeds and the file has a header plus 7 data rows
    assert result.exit_code == 0, result.output
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("purchase_date,")
    assert len(lines) == 8  # header + 7 line items (incl. the failed receipt)
    assert "7" in result.output  # row-count confirmation


def test_export_command_writes_json_with_filters(tmp_path, monkeypatch):
    """Verify `cartlog export --format json` honors the store filter."""
    # Given a seeded temp database
    db_url = seed_temp_db(tmp_path, "export.db")
    monkeypatch.setattr(cli_module, "get_settings", lambda: Settings(database_url=db_url))
    out = tmp_path / "out.json"

    # When exporting Safeway rows as JSON
    result = runner.invoke(
        cli_module.app, ["export", "-o", str(out), "-f", "json", "--store", "safeway"]
    )

    # Then only Safeway's 5 line items are written
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload) == 5
    assert {row["store_chain"] for row in payload} == {"Safeway"}


def test_export_command_requires_output():
    """Verify omitting --output is a usage error."""
    # When invoking export with no --output
    result = runner.invoke(cli_module.app, ["export"])

    # Then it exits non-zero with a missing-option error
    assert result.exit_code != 0
