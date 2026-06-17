"""Tests for the JobView display helper and elapsed-time formatting."""

from __future__ import annotations

from datetime import datetime

import pytest

from cartlog.db.models import IngestionJob, JobStatus, JobStep
from cartlog.web.jobs_view import JobView, format_elapsed


def _utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    """Build a naive UTC datetime, matching the app's CURRENT_TIMESTAMP clock used in JobView."""
    # The app stores and compares naive UTC timestamps, so tests construct naive datetimes too.
    return datetime(year, month, day, hour, minute, second)  # noqa: DTZ001


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (5, "5s"),
        (59, "59s"),
        (60, "1m"),
        (125, "2m"),
        (3600, "1h 0m"),
        (3700, "1h 1m"),
        (-5, "0s"),
    ],
)
def test_format_elapsed(seconds, expected):
    """Verify elapsed seconds render as a short human-readable string."""
    assert format_elapsed(seconds) == expected


def test_job_view_pending_is_queued_and_uses_created_at():
    """Verify a pending job shows 'queued' and measures elapsed from created_at."""
    # Given a pending job created 5s before now
    job = IngestionJob(id=1, source="web", image_path="/storage/x.png", status=JobStatus.PENDING)
    job.created_at = _utc(2026, 6, 14, 12, 0, 0)
    job.updated_at = _utc(2026, 6, 14, 12, 0, 0)
    job.retry_count = 0

    # When building a view at 12:00:05
    view = JobView.from_job(job, now=_utc(2026, 6, 14, 12, 0, 5), queue_position=2)

    # Then it is labeled queued, elapsed 5s, with the given queue position
    assert view.state_label == "queued"
    assert view.elapsed == "5s"
    assert view.queue_position == 2


def test_job_view_parsing_uses_step_label_and_updated_at():
    """Verify a parsing job shows its step and measures elapsed from updated_at."""
    # Given a parsing job whose step was set 30s before now
    job = IngestionJob(id=2, source="web", image_path="/storage/x.png", status=JobStatus.PARSING)
    job.step = JobStep.EXTRACTING
    job.created_at = _utc(2026, 6, 14, 12, 0, 0)
    job.updated_at = _utc(2026, 6, 14, 12, 0, 10)
    job.retry_count = 0

    # When building a view at 12:00:40
    view = JobView.from_job(job, now=_utc(2026, 6, 14, 12, 0, 40))

    # Then it shows the step label, elapsed since updated_at, and no queue position
    assert view.state_label == "extracting"
    assert view.elapsed == "30s"
    assert view.queue_position is None


def test_job_view_done_uses_status_label():
    """Verify a finished job's label is its status."""
    # Given a done job linked to a receipt
    job = IngestionJob(id=3, source="web", image_path="/storage/x.png", status=JobStatus.DONE)
    job.created_at = _utc(2026, 6, 14, 12, 0, 0)
    job.updated_at = _utc(2026, 6, 14, 12, 0, 0)
    job.retry_count = 0
    job.receipt_id = 99

    # When building a view
    view = JobView.from_job(job, now=_utc(2026, 6, 14, 12, 0, 0))

    # Then the label is the status and the receipt id is carried through
    assert view.state_label == "done"
    assert view.receipt_id == 99


def test_job_view_failed_carries_error():
    """Verify a failed job shows 'failed' and passes last_error through."""
    # Given a failed job with an error message
    job = IngestionJob(id=4, source="web", image_path="/storage/x.png", status=JobStatus.FAILED)
    job.created_at = _utc(2026, 6, 14, 12, 0, 0)
    job.updated_at = _utc(2026, 6, 14, 12, 0, 0)
    job.retry_count = 2
    job.last_error = "LLM timeout"

    # When building a view
    view = JobView.from_job(job, now=_utc(2026, 6, 14, 12, 0, 0))

    # Then the label is failed and the error text is present
    assert view.state_label == "failed"
    assert view.last_error == "LLM timeout"


def test_job_view_parsing_without_step_falls_back_to_parsing():
    """Verify a parsing job with no step yet is labeled 'parsing'."""
    # Given a parsing job whose step has not been set
    job = IngestionJob(id=5, source="web", image_path="/storage/x.png", status=JobStatus.PARSING)
    job.step = None
    job.created_at = _utc(2026, 6, 14, 12, 0, 0)
    job.updated_at = _utc(2026, 6, 14, 12, 0, 0)
    job.retry_count = 0

    # When building a view
    view = JobView.from_job(job, now=_utc(2026, 6, 14, 12, 0, 0))

    # Then the label falls back to the generic parsing state
    assert view.state_label == "parsing"
