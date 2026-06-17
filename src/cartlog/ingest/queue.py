"""DB-backed ingestion job queue: store receipt files and claim them for processing."""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import or_

from cartlog.clock import naive_utcnow
from cartlog.db.models import IngestionJob, JobStatus, JobStep

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cartlog.db.models import Receipt

# Streaming chunk size for hashing and copying stored files (1 MiB).
_STORE_CHUNK_SIZE = 1024 * 1024


def _utcnow() -> datetime:
    """Return a naive UTC timestamp matching the database's CURRENT_TIMESTAMP clock."""
    return naive_utcnow()


# Cap backoff so a large max_retries cannot overflow timedelta or schedule absurd delays.
_MAX_BACKOFF_SECONDS = 3600.0
_MAX_BACKOFF_EXPONENT = 20


def _backoff_delay(retry_count: int, base_seconds: float) -> timedelta:
    """Compute the (capped) exponential backoff delay for the given retry attempt (1-based)."""
    exponent = min(max(0, retry_count - 1), _MAX_BACKOFF_EXPONENT)
    seconds = min(base_seconds * 2**exponent, _MAX_BACKOFF_SECONDS)
    return timedelta(seconds=seconds)


def _requeue_or_fail(
    job: IngestionJob, *, error: str, max_retries: int, retry_backoff_base_seconds: float
) -> bool:
    """Apply a failure outcome to a job in memory (no commit). Returns True if terminal.

    Re-queues the job with a backoff-delayed next attempt while retries remain, otherwise
    marks it permanently failed. Shared by transient parse failures and the stale-job reaper.
    """
    job.last_error = error
    job.step = None  # a re-queued or failed job is no longer mid-parse
    if job.retry_count < max_retries:
        job.retry_count += 1
        job.status = JobStatus.PENDING
        job.next_attempt_at = _utcnow() + _backoff_delay(
            job.retry_count, retry_backoff_base_seconds
        )
        return False
    job.status = JobStatus.FAILED
    job.next_attempt_at = None
    return True


def _store_file(storage_dir: Path, src_path: Path) -> Path:
    """Copy the source file into the storage directory and return the new path.

    The stored filename embeds a content hash so that two different source files sharing
    a basename never overwrite each other, while re-storing identical bytes is idempotent.
    The original suffix is preserved so downstream parsing can tell images from PDFs.

    Args:
        storage_dir: Destination directory for stored files. Created if absent.
        src_path: Path to the original source file (image or PDF).

    Returns:
        Path to the copied file within the storage directory.
    """
    storage_dir.mkdir(parents=True, exist_ok=True)
    # Hash and copy in one streaming pass (one read + one write). The destination name
    # embeds the digest, so bytes land in a temp file first and are renamed once the hash
    # is known; the rename is atomic because the temp file lives in storage_dir.
    hasher = hashlib.sha256()
    tmp = tempfile.NamedTemporaryFile(dir=storage_dir, delete=False)  # noqa: SIM115
    tmp_path = Path(tmp.name)
    try:
        with tmp, src_path.open("rb") as src:
            while chunk := src.read(_STORE_CHUNK_SIZE):
                hasher.update(chunk)
                tmp.write(chunk)
        dest = storage_dir / f"{src_path.stem}-{hasher.hexdigest()[:12]}{src_path.suffix}"
        tmp_path.replace(dest)
    except Exception:
        # Never leave a half-written temp file behind if the stream fails.
        tmp_path.unlink(missing_ok=True)
        raise
    return dest


def enqueue_job(
    session: Session, *, src_path: Path, source: str, storage_dir: Path
) -> IngestionJob:
    """Store a receipt file and create a pending job for it. Commits on success.

    The stored file is the durable artifact a worker later parses, so it is committed
    together with the job row. If the commit fails, the orphaned copy is removed.

    Args:
        session: SQLAlchemy session; this function commits on success.
        src_path: Path to the source file to ingest (image or PDF).
        source: How the receipt was submitted (e.g. 'cli', 'web').
        storage_dir: Directory where the file copy will be stored.

    Returns:
        The newly created, committed pending IngestionJob.
    """
    stored_path = _store_file(storage_dir, src_path)
    try:
        job = IngestionJob(source=source, image_path=str(stored_path), status=JobStatus.PENDING)
        session.add(job)
        session.commit()
    except Exception:
        # The file has no referencing row, so remove it rather than leak an orphan.
        session.rollback()
        stored_path.unlink(missing_ok=True)
        raise
    return job


def claim_job(session: Session, job: IngestionJob) -> bool:
    """Transition one specific pending job to parsing. Commits.

    The transition is a guarded update that only succeeds while the row is still pending,
    so two actors racing for the same job (e.g. the CLI and a background worker) can never
    both win it.

    Args:
        session: SQLAlchemy session; this function commits the claim.
        job: The job to claim.

    Returns:
        True if this caller won the claim (job is now 'parsing'), False if it was already
        taken (or no longer pending).
    """
    # Bump updated_at on the claim so the stale-job reaper measures elapsed parsing time
    # from when the job was claimed, not from when it was created.
    updated = (
        session.query(IngestionJob)
        .filter(IngestionJob.id == job.id, IngestionJob.status == JobStatus.PENDING)
        .update({IngestionJob.status: JobStatus.PARSING, IngestionJob.updated_at: _utcnow()})
    )
    session.commit()
    if updated == 1:
        session.refresh(job)
        return True
    return False


def set_job_step(session: Session, job: IngestionJob, step: JobStep) -> None:
    """Record the sub-step a parsing job is on and commit so web pollers can observe it.

    Each transition is its own committed write because process_job otherwise runs the whole
    parse in one transaction, hiding progress from the separate session a web request uses.

    Args:
        session: SQLAlchemy session; this function commits.
        job: The job to update; expected to be in 'parsing' status.
        step: The sub-step to record.
    """
    job.step = step
    session.commit()


def claim_next_job(session: Session) -> IngestionJob | None:
    """Claim the oldest pending job, transitioning it pending -> parsing. Commits.

    Args:
        session: SQLAlchemy session; this function commits the claim.

    Returns:
        The claimed job now in 'parsing', or None when the queue is empty.
    """
    while True:
        # Recompute each iteration so a job whose backoff elapses mid-loop becomes eligible.
        now = _utcnow()
        # Skip jobs whose backoff delay has not yet elapsed (next_attempt_at in the future).
        job = (
            session.query(IngestionJob)
            .filter(
                IngestionJob.status == JobStatus.PENDING,
                or_(
                    IngestionJob.next_attempt_at.is_(None),
                    IngestionJob.next_attempt_at <= now,
                ),
            )
            .order_by(IngestionJob.created_at, IngestionJob.id)
            .first()
        )
        if job is None:
            return None
        # If a concurrent worker claimed this row first, try the next pending job.
        if claim_job(session, job):
            return job


def complete_job(session: Session, job: IngestionJob, *, receipt: Receipt) -> None:
    """Mark a job done and link the receipt produced from it. Commits."""
    job.status = JobStatus.DONE
    job.receipt_id = receipt.id
    job.last_error = None
    job.step = None
    session.commit()


def fail_job(
    session: Session,
    job: IngestionJob,
    *,
    error: str,
    max_retries: int,
    retry_backoff_base_seconds: float = 0.0,
) -> bool:
    """Record a job failure, re-queuing with backoff if retries remain. Commits.

    Args:
        session: SQLAlchemy session; this function commits.
        job: The job that failed.
        error: Message describing the failure, stored on the job.
        max_retries: Retry budget; the job fails permanently once it is reached.
        retry_backoff_base_seconds: Base delay for exponential backoff before the next attempt.

    Returns:
        True if the failure was terminal (job marked 'failed'), False if re-queued.
    """
    terminal = _requeue_or_fail(
        job,
        error=error,
        max_retries=max_retries,
        retry_backoff_base_seconds=retry_backoff_base_seconds,
    )
    session.commit()
    return terminal


def reap_stale_jobs(
    session: Session,
    *,
    stale_after_seconds: float,
    max_retries: int,
    retry_backoff_base_seconds: float = 0.0,
) -> int:
    """Re-queue (or fail) jobs stuck in 'parsing' past the stale timeout. Commits.

    A worker that crashes mid-parse leaves its job in 'parsing' forever, since claiming only
    selects 'pending' rows. This recovers those jobs by treating an over-long parse as a
    failure: it re-queues with backoff while retries remain, counting the attempt so a job
    that repeatedly crashes the worker eventually fails instead of being reaped forever.

    Args:
        session: SQLAlchemy session; this function commits.
        stale_after_seconds: A job parsing longer than this (since it was claimed) is reaped.
        max_retries: Retry budget shared with normal failures.
        retry_backoff_base_seconds: Base delay for exponential backoff before the next attempt.

    Returns:
        The number of stale jobs actually reaped.
    """
    threshold = _utcnow() - timedelta(seconds=stale_after_seconds)
    stale_jobs = (
        session.query(IngestionJob)
        .filter(IngestionJob.status == JobStatus.PARSING, IngestionJob.updated_at < threshold)
        .all()
    )
    error = "worker stopped before finishing the job (reaped)"
    reaped = 0
    for job in stale_jobs:
        # Guard on 'parsing' so a worker that just finished this job (e.g. a parse slower than
        # the stale timeout) is not clobbered back into the queue, which would duplicate its
        # receipt. If the row already moved on, the update matches nothing and is skipped.
        guarded = session.query(IngestionJob).filter(
            IngestionJob.id == job.id, IngestionJob.status == JobStatus.PARSING
        )
        if job.retry_count < max_retries:
            reaped += guarded.update(
                {
                    IngestionJob.status: JobStatus.PENDING,
                    IngestionJob.step: None,
                    IngestionJob.retry_count: job.retry_count + 1,
                    IngestionJob.last_error: error,
                    IngestionJob.next_attempt_at: _utcnow()
                    + _backoff_delay(job.retry_count + 1, retry_backoff_base_seconds),
                    IngestionJob.updated_at: _utcnow(),
                }
            )
        else:
            reaped += guarded.update(
                {
                    IngestionJob.status: JobStatus.FAILED,
                    IngestionJob.step: None,
                    IngestionJob.last_error: error,
                    IngestionJob.next_attempt_at: None,
                    IngestionJob.updated_at: _utcnow(),
                }
            )
    session.commit()
    return reaped
