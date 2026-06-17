"""Tests for the Jobs tab page, list partial, and nav badge."""

from __future__ import annotations

from cartlog.db.models import IngestionJob, JobStatus, JobStep, Receipt


def _upload(app_client) -> int:
    """Upload a receipt and return its job id."""
    files = {"files": ("scan.png", b"\x89PNG fake", "image/png")}
    return app_client.post("/receipts", files=files).json()["accepted"][0]["job_id"]


def test_jobs_page_renders(app_client):
    """Verify GET /jobs renders the Jobs tab shell."""
    # When loading the Jobs tab
    response = app_client.get("/jobs")

    # Then it renders and wires up polling of the list partial
    assert response.status_code == 200
    assert "Jobs" in response.text
    assert "/jobs/list/partial" in response.text


def test_jobs_list_partial_shows_active_job(app_client):
    """Verify the list partial lists a freshly uploaded job as queued."""
    # Given an uploaded receipt
    job_id = _upload(app_client)

    # When loading the list partial
    response = app_client.get("/jobs/list/partial")

    # Then the new job appears as queued
    assert response.status_code == 200
    assert f"#{job_id}" in response.text
    assert "queued" in response.text


def test_jobs_list_partial_shows_parsing_step(app_client):
    """Verify a parsing job renders its current step in the active list."""
    # Given a job already parsing with an extracting step
    with app_client.app.state.session_factory() as session:
        job = IngestionJob(
            source="web",
            image_path="/storage/x.png",
            status=JobStatus.PARSING,
            step=JobStep.EXTRACTING,
        )
        session.add(job)
        session.commit()

    # When loading the list partial
    response = app_client.get("/jobs/list/partial")

    # Then the active row shows the extracting step
    assert response.status_code == 200
    assert "extracting" in response.text


def test_jobs_badge_partial_counts_active(app_client):
    """Verify the badge partial shows the active job count after an upload."""
    # Given an uploaded receipt
    _upload(app_client)

    # When loading the badge partial
    response = app_client.get("/jobs/badge/partial")

    # Then the badge shows a count of one
    assert response.status_code == 200
    assert "1" in response.text
    assert "badge-warning" in response.text


def test_jobs_badge_partial_empty_when_no_active(app_client):
    """Verify the badge renders nothing when no jobs are active."""
    # Given the seeded app with no ingestion jobs
    # When loading the badge partial
    response = app_client.get("/jobs/badge/partial")

    # Then no badge markup is emitted
    assert response.status_code == 200
    assert "badge-warning" not in response.text


def test_jobs_list_partial_shows_done_job_receipt_link(app_client):
    """Verify a finished job links to its receipt in the Recent section."""
    # Given a done job linked to an existing seeded receipt
    with app_client.app.state.session_factory() as session:
        receipt_id = session.query(Receipt.id).order_by(Receipt.id).first()[0]
        job = IngestionJob(
            source="web",
            image_path="/storage/done.png",
            status=JobStatus.DONE,
            receipt_id=receipt_id,
        )
        session.add(job)
        session.commit()

    # When loading the list partial
    response = app_client.get("/jobs/list/partial")

    # Then the Recent section links to the receipt
    assert response.status_code == 200
    assert f"View receipt #{receipt_id}" in response.text
    assert f"/receipts/{receipt_id}" in response.text
