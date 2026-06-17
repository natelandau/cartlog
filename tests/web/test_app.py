"""Tests for the receipt-upload and job-status endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

from cartlog.config import Settings, get_settings
from cartlog.db.models import JobStatus
from cartlog.web.app import create_app
from cartlog.web.templating import templates


def test_create_app_dev_mode_reloads_templates():
    """Verify dev mode re-stats templates on render and enables debug tracebacks."""
    # When building the app in development mode
    app = create_app(dev=True)

    # Then templates reload from disk on each render and debug tracebacks are on
    assert templates.env.auto_reload is True
    assert app.debug is True


def test_create_app_production_caches_templates():
    """Verify production reuses the compiled template cache and hides debug tracebacks."""
    # When building the app for production (the default)
    app = create_app()

    # Then templates are not re-stated and debug tracebacks are off
    assert templates.env.auto_reload is False
    assert app.debug is False


def test_lifespan_session_factory_enables_sqlite_wal(tmp_path, monkeypatch):
    """Verify the app lifespan builds its session factory via the shared WAL-enabled helper."""
    # Given settings pointing the app at a file-based sqlite database
    db_path = tmp_path / "web.db"
    settings = Settings(database_url=f"sqlite:///{db_path}", image_storage_dir=tmp_path / "storage")
    monkeypatch.setattr("cartlog.web.app.get_settings", lambda: settings)

    # When the lifespan runs (entering the TestClient context manager triggers startup)
    app = create_app()
    with TestClient(app), app.state.session_factory() as session:
        journal_mode = session.execute(text("PRAGMA journal_mode")).scalar()

    # Then the web app's connections use WAL, matching the worker pool's engine
    assert journal_mode == "wal"


def test_upload_receipt_creates_pending_job(app_client):
    """Verify POST /receipts stores a single upload and returns it as accepted."""
    # Given an uploaded image
    files = {"files": ("scan.png", b"\x89PNG fake", "image/png")}

    # When posting it
    response = app_client.post("/receipts", files=files)

    # Then the API accepts it and reports one pending job
    assert response.status_code == 202
    body = response.json()
    assert body["rejected"] == []
    assert len(body["accepted"]) == 1
    assert body["accepted"][0]["status"] == JobStatus.PENDING
    assert isinstance(body["accepted"][0]["job_id"], int)


def test_job_status_reflects_enqueued_job(app_client):
    """Verify GET /jobs/{id} returns the status of an enqueued job."""
    # Given an uploaded receipt
    files = {"files": ("scan.png", b"\x89PNG fake", "image/png")}
    job_id = app_client.post("/receipts", files=files).json()["accepted"][0]["job_id"]

    # When querying its status
    response = app_client.get(f"/jobs/{job_id}")

    # Then the job is reported pending with no receipt yet
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] == JobStatus.PENDING
    assert body["receipt_id"] is None
    assert body["retry_count"] == 0
    assert body["last_error"] is None


def test_job_status_unknown_id_returns_404(app_client):
    """Verify GET /jobs/{id} returns 404 for an unknown job."""
    # When querying a missing id
    response = app_client.get("/jobs/999999")

    # Then the API reports not found
    assert response.status_code == 404


def test_upload_all_invalid_returns_400(app_client):
    """Verify a request whose only file is unsupported is rejected wholesale."""
    # Given an upload with an unsupported extension
    files = {"files": ("notes.txt", b"hello", "text/plain")}

    # When posting it
    response = app_client.post("/receipts", files=files)

    # Then the API rejects the whole request and reports the file per-file
    assert response.status_code == 400
    body = response.json()
    assert body["accepted"] == []
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["filename"] == "notes.txt"


def test_upload_oversized_file_is_rejected(app_client):
    """Verify an oversized upload is reported as a per-file rejection."""
    # Given a tiny upload cap (override get_settings, keeping the per-test storage dir)
    tiny = Settings(
        database_url="sqlite://",
        image_storage_dir=app_client.app.state.settings.image_storage_dir,
        max_upload_bytes=8,
    )
    app_client.app.dependency_overrides[get_settings] = lambda: tiny

    # When posting a supported file that exceeds the cap
    files = {"files": ("scan.png", b"\x89PNG way too large", "image/png")}
    response = app_client.post("/receipts", files=files)

    # Then the only file is rejected, so the request is a 400 with the reason reported
    assert response.status_code == 400
    body = response.json()
    assert body["accepted"] == []
    assert len(body["rejected"]) == 1


def test_upload_multiple_files_enqueues_each(app_client):
    """Verify a multi-file upload enqueues a job per valid file."""
    # Given two valid images in one request
    files = [
        ("files", ("one.png", b"\x89PNG one", "image/png")),
        ("files", ("two.png", b"\x89PNG two", "image/png")),
    ]

    # When posting them together
    response = app_client.post("/receipts", files=files)

    # Then both are accepted and none rejected
    assert response.status_code == 202
    body = response.json()
    assert len(body["accepted"]) == 2
    assert body["rejected"] == []


def test_upload_partial_invalid_accepts_valid_reports_invalid(app_client):
    """Verify a mixed batch enqueues the valid file and reports the invalid one."""
    # Given one valid image and one unsupported file in the same request
    files = [
        ("files", ("good.png", b"\x89PNG good", "image/png")),
        ("files", ("notes.txt", b"hello", "text/plain")),
    ]

    # When posting them together
    response = app_client.post("/receipts", files=files)

    # Then the valid file is accepted and the invalid file is reported, still a 202
    assert response.status_code == 202
    body = response.json()
    assert len(body["accepted"]) == 1
    assert body["accepted"][0]["filename"] == "good.png"
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["filename"] == "notes.txt"
