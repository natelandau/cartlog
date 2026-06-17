"""Tests for the DB-backed ingestion job queue."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cartlog.db.models import IngestionJob, JobStatus, JobStep
from cartlog.ingest.persistence import persist_receipt
from cartlog.ingest.queue import (
    claim_job,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    reap_stale_jobs,
    set_job_step,
)


def _utcnow() -> datetime:
    """Return a naive UTC timestamp matching the queue's clock."""
    return datetime.now(UTC).replace(tzinfo=None)


def _enqueue(session, tmp_path, *, name="scan.png"):
    """Enqueue a job for a freshly written source file and return it."""
    src = tmp_path / name
    src.write_bytes(b"\x89PNG fake " + name.encode())
    return enqueue_job(session, src_path=src, source="cli", storage_dir=tmp_path / "storage")


def test_fail_job_schedules_backoff_next_attempt(session, tmp_path):
    """Verify a re-queued failure schedules next_attempt_at in the future via backoff."""
    # Given a pending job
    job = _enqueue(session, tmp_path)

    # When it fails with a non-zero backoff base and retries remaining
    before = _utcnow()
    terminal = fail_job(session, job, error="boom", max_retries=3, retry_backoff_base_seconds=60)

    # Then it is re-queued with a future next_attempt_at roughly one backoff interval out
    assert terminal is False
    assert job.status == JobStatus.PENDING
    assert job.next_attempt_at is not None
    assert job.next_attempt_at > before + timedelta(seconds=30)


def test_claim_next_job_skips_jobs_not_yet_due(session, tmp_path):
    """Verify a pending job whose backoff has not elapsed is not claimed."""
    # Given a pending job scheduled for a future attempt
    job = _enqueue(session, tmp_path)
    job.next_attempt_at = _utcnow() + timedelta(hours=1)
    session.commit()

    # When claiming the next due job
    claimed = claim_next_job(session)

    # Then nothing is claimed and the job stays pending
    assert claimed is None
    session.refresh(job)
    assert job.status == JobStatus.PENDING


def test_reap_stale_jobs_requeues_stuck_parsing(session, tmp_path):
    """Verify a job stuck in parsing past the timeout is re-queued, counting a retry."""
    # Given a job that was claimed long ago and never finished
    job = _enqueue(session, tmp_path)
    session.query(IngestionJob).filter_by(id=job.id).update(
        {
            IngestionJob.status: JobStatus.PARSING,
            IngestionJob.updated_at: _utcnow() - timedelta(hours=1),
        }
    )
    session.commit()

    # When the reaper runs with a 60s stale timeout and a backoff
    before = _utcnow()
    reaped = reap_stale_jobs(
        session, stale_after_seconds=60, max_retries=3, retry_backoff_base_seconds=60
    )

    # Then the stale job is re-queued, the attempt is counted, and backoff is applied
    assert reaped == 1
    session.refresh(job)
    assert job.status == JobStatus.PENDING
    assert job.retry_count == 1
    assert job.next_attempt_at is not None
    assert job.next_attempt_at > before + timedelta(seconds=30)


def test_reap_stale_jobs_ignores_fresh_parsing(session, tmp_path):
    """Verify a recently-claimed parsing job is left alone by the reaper."""
    # Given a job claimed just now (claim bumps updated_at)
    job = _enqueue(session, tmp_path)
    assert claim_job(session, job) is True

    # When the reaper runs with a generous stale timeout
    reaped = reap_stale_jobs(session, stale_after_seconds=300, max_retries=3)

    # Then the fresh job is not reaped
    assert reaped == 0
    session.refresh(job)
    assert job.status == JobStatus.PARSING


def test_reap_stale_jobs_fails_when_retries_exhausted(session, tmp_path):
    """Verify a stale parsing job with no retries left is marked failed."""
    # Given a stale parsing job that has already used its retry budget
    job = _enqueue(session, tmp_path)
    session.query(IngestionJob).filter_by(id=job.id).update(
        {
            IngestionJob.status: JobStatus.PARSING,
            IngestionJob.retry_count: 3,
            IngestionJob.updated_at: _utcnow() - timedelta(hours=1),
        }
    )
    session.commit()

    # When the reaper runs with max_retries=3
    reaped = reap_stale_jobs(session, stale_after_seconds=60, max_retries=3)

    # Then the job is permanently failed with no pending next attempt
    assert reaped == 1
    session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.next_attempt_at is None


def test_claim_job_wins_then_loses(session, tmp_path):
    """Verify claiming a pending job wins once, then a second claim of it loses."""
    # Given a pending job
    src = tmp_path / "scan.png"
    src.write_bytes(b"\x89PNG fake")
    job = enqueue_job(session, src_path=src, source="cli", storage_dir=tmp_path / "storage")

    # When claiming it the first time
    won = claim_job(session, job)

    # Then the claim succeeds and the job is now parsing
    assert won is True
    assert job.status == JobStatus.PARSING

    # When a second actor tries to claim the same (no longer pending) job
    won_again = claim_job(session, job)

    # Then the second claim loses
    assert won_again is False


def test_enqueue_job_stores_file_and_creates_pending_row(session, tmp_path):
    """Verify enqueue copies the file into storage and creates a pending job."""
    # Given a source image and an empty storage directory
    src = tmp_path / "scan.png"
    src.write_bytes(b"\x89PNG fake")
    storage = tmp_path / "storage"

    # When enqueuing the file
    job = enqueue_job(session, src_path=src, source="cli", storage_dir=storage)

    # Then the file is copied into storage and a pending job points at the copy
    assert Path(job.image_path).exists()
    assert Path(job.image_path).parent == storage
    assert job.status == JobStatus.PENDING
    assert job.source == "cli"
    assert session.query(IngestionJob).count() == 1


def test_claim_next_job_transitions_oldest_to_parsing(session, tmp_path):
    """Verify claiming returns the oldest pending job and marks it parsing."""
    # Given two enqueued jobs
    storage = tmp_path / "storage"
    for name in ("a.png", "b.png"):
        src = tmp_path / name
        src.write_bytes(b"\x89PNG fake " + name.encode())
        enqueue_job(session, src_path=src, source="cli", storage_dir=storage)

    # When claiming the next job
    claimed = claim_next_job(session)

    # Then the first-enqueued job is returned and marked parsing
    assert claimed is not None
    assert claimed.status == JobStatus.PARSING
    assert Path(claimed.image_path).name.startswith("a-")

    # And the other job is left untouched, still pending
    pending = session.query(IngestionJob).filter_by(status=JobStatus.PENDING).all()
    assert len(pending) == 1
    assert Path(pending[0].image_path).name.startswith("b-")


def test_claim_next_job_returns_none_when_queue_empty(session):
    """Verify claiming an empty queue returns None."""
    # Given no jobs
    # When claiming
    claimed = claim_next_job(session)

    # Then nothing is returned
    assert claimed is None


def test_complete_job_marks_done_and_links_receipt(session, tmp_path, sample_parsed_receipt):
    """Verify completing a job marks it done and links the created receipt."""
    # Given a claimed job and a persisted receipt
    src = tmp_path / "scan.png"
    src.write_bytes(b"\x89PNG fake")
    job = enqueue_job(session, src_path=src, source="cli", storage_dir=tmp_path / "storage")
    receipt, _ = persist_receipt(
        session,
        sample_parsed_receipt,
        image_path=job.image_path,
        source="cli",
        status="parsed",
        raw_json="{}",
    )
    session.flush()

    # When completing the job
    complete_job(session, job, receipt=receipt)

    # Then it is done and points at the receipt
    assert job.status == JobStatus.DONE
    assert job.receipt_id == receipt.id
    assert job.last_error is None


def test_fail_job_requeues_until_retries_exhausted(session, tmp_path):
    """Verify failing re-queues while retries remain, then marks failed."""
    # Given a job and a max of 1 retry
    src = tmp_path / "scan.png"
    src.write_bytes(b"\x89PNG fake")
    job = enqueue_job(session, src_path=src, source="cli", storage_dir=tmp_path / "storage")

    # When it fails the first time (retry_count 0 < 1)
    terminal_first = fail_job(session, job, error="boom", max_retries=1)

    # Then it is re-queued, not terminal
    assert terminal_first is False
    assert job.status == JobStatus.PENDING
    assert job.retry_count == 1
    assert job.last_error == "boom"

    # When it fails again (retry_count 1 == max)
    terminal_second = fail_job(session, job, error="boom again", max_retries=1)

    # Then it is permanently failed
    assert terminal_second is True
    assert job.status == JobStatus.FAILED
    assert job.last_error == "boom again"


def test_set_job_step_records_and_commits(session, tmp_path):
    """Verify set_job_step persists the sub-step so other sessions can observe it."""
    # Given a claimed (parsing) job
    job = _enqueue(session, tmp_path)
    assert claim_job(session, job) is True

    # When recording a sub-step
    set_job_step(session, job, JobStep.EXTRACTING)

    # Then the committed value is readable after expiring the in-memory state
    session.expire(job)
    assert job.step == JobStep.EXTRACTING


def test_complete_job_clears_step(session, tmp_path, sample_parsed_receipt):
    """Verify completing a job clears its sub-step so a done job carries no stale step."""
    # Given a job mid-parse with a step set
    job = _enqueue(session, tmp_path)
    set_job_step(session, job, JobStep.SAVING)
    receipt, _ = persist_receipt(
        session,
        sample_parsed_receipt,
        image_path=job.image_path,
        source="cli",
        status="parsed",
        raw_json="{}",
    )

    # When the job completes
    complete_job(session, job, receipt=receipt)

    # Then the step is cleared
    assert job.step is None


def test_fail_job_clears_step(session, tmp_path):
    """Verify failing a job clears its sub-step."""
    # Given a job mid-parse with a step set
    job = _enqueue(session, tmp_path)
    set_job_step(session, job, JobStep.EXTRACTING)

    # When it fails
    fail_job(session, job, error="boom", max_retries=3)

    # Then the step is cleared
    assert job.step is None


def test_reap_stale_jobs_clears_step(session, tmp_path):
    """Verify reaping a stuck parsing job clears its sub-step."""
    # Given a job stuck in parsing with a step set
    job = _enqueue(session, tmp_path)
    session.query(IngestionJob).filter_by(id=job.id).update(
        {
            IngestionJob.status: JobStatus.PARSING,
            IngestionJob.step: JobStep.EXTRACTING,
            IngestionJob.updated_at: _utcnow() - timedelta(hours=1),
        }
    )
    session.commit()

    # When reaping stale jobs
    reap_stale_jobs(session, stale_after_seconds=1, max_retries=3)

    # Then the re-queued job no longer carries a step
    session.expire_all()
    reaped = session.get(IngestionJob, job.id)
    assert reaped.step is None


def test_reap_stale_jobs_bumps_updated_at_on_fail(session, tmp_path):
    """Verify reaping a retry-exhausted stuck job refreshes updated_at to now."""
    # Given a job stuck in parsing with no retries left and a stale updated_at
    job = _enqueue(session, tmp_path)
    stale = _utcnow() - timedelta(hours=2)
    session.query(IngestionJob).filter_by(id=job.id).update(
        {
            IngestionJob.status: JobStatus.PARSING,
            IngestionJob.retry_count: 3,
            IngestionJob.updated_at: stale,
        }
    )
    session.commit()

    # When reaping with the retry budget already exhausted
    reaped = reap_stale_jobs(session, stale_after_seconds=1, max_retries=3)

    # Then the job is failed and its updated_at moved forward from the stale value
    assert reaped == 1
    session.expire_all()
    failed = session.get(IngestionJob, job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.updated_at > stale
