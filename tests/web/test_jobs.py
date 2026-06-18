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


def test_jobs_progress_partial_summarizes_batch(app_client):
    """Verify the upload progress card counts done and failed jobs and keeps polling."""
    # Given three jobs from one upload: one done, one failed, one still pending
    with app_client.app.state.session_factory() as session:
        receipt_id = session.query(Receipt.id).order_by(Receipt.id).first()[0]
        done = IngestionJob(
            source="web", image_path="/s/a.png", status=JobStatus.DONE, receipt_id=receipt_id
        )
        failed = IngestionJob(
            source="web", image_path="/s/b.png", status=JobStatus.FAILED, last_error="boom"
        )
        pending = IngestionJob(source="web", image_path="/s/c.png", status=JobStatus.PENDING)
        session.add_all([done, failed, pending])
        session.commit()
        ids = f"{done.id},{failed.id},{pending.id}"

    # When loading the progress card for that batch
    response = app_client.get(f"/jobs/progress/partial?ids={ids}")

    # Then it reports 1 of 3 done, flags the failure, and still polls
    assert response.status_code == 200
    assert "1 of 3" in response.text
    assert "1 failed" in response.text
    assert 'hx-trigger="every 2s"' in response.text


def test_jobs_progress_partial_stops_polling_when_all_terminal(app_client):
    """Verify the progress card drops polling attributes once every tracked job is terminal."""
    # Given two finished jobs from one upload
    with app_client.app.state.session_factory() as session:
        first = IngestionJob(source="web", image_path="/s/a.png", status=JobStatus.DONE)
        second = IngestionJob(source="web", image_path="/s/b.png", status=JobStatus.DONE)
        session.add_all([first, second])
        session.commit()
        ids = f"{first.id},{second.id}"

    # When loading the progress card
    response = app_client.get(f"/jobs/progress/partial?ids={ids}")

    # Then it shows completion and no longer polls
    assert response.status_code == 200
    assert "2 of 2" in response.text
    assert "Upload complete" in response.text
    assert "hx-trigger" not in response.text


def test_jobs_progress_partial_tolerates_non_numeric_ids(app_client):
    """Verify the progress route resolves (not captured as /jobs/{id}) and ignores junk ids."""
    # Given a single real pending job
    job_id = _upload(app_client)

    # When requesting progress with a junk token mixed into the ids
    response = app_client.get(f"/jobs/progress/partial?ids=abc,{job_id}")

    # Then the route resolves and counts only the one real job
    assert response.status_code == 200
    assert "0 of 1" in response.text


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
