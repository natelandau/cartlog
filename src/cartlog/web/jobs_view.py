"""Server-side display values for ingestion jobs, kept out of templates.

Templates avoid time math, so the router builds a JobView per job: a frozen snapshot with a
human-readable elapsed string, a state label, and the receipt/error fields the Jobs tab shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cartlog.db.models import JobStatus

if TYPE_CHECKING:
    from datetime import datetime

    from cartlog.db.models import IngestionJob

_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60


def format_elapsed(seconds: float) -> str:
    """Format an elapsed duration as a short human string (e.g. '5s', '2m', '1h 1m').

    Negative inputs (from clock skew between the worker and web clocks) clamp to '0s'.

    Args:
        seconds: Elapsed seconds to format.

    Returns:
        A short human-readable duration string.
    """
    secs = max(0, int(seconds))
    if secs < _SECONDS_PER_MINUTE:
        return f"{secs}s"
    minutes, secs = divmod(secs, _SECONDS_PER_MINUTE)
    if minutes < _MINUTES_PER_HOUR:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, _MINUTES_PER_HOUR)
    return f"{hours}h {minutes}m"


@dataclass(frozen=True)
class JobView:
    """A read-only snapshot of an ingestion job shaped for the Jobs templates."""

    id: int
    source: str
    status: str
    state_label: str
    elapsed: str
    queue_position: int | None
    retry_count: int
    last_error: str | None
    receipt_id: int | None

    @classmethod
    def from_job(
        cls, job: IngestionJob, *, now: datetime, queue_position: int | None = None
    ) -> JobView:
        """Build a JobView from a job, measuring elapsed time against `now`.

        Pending jobs measure from created_at (time queued); parsing jobs measure from
        updated_at (time on the current step, which set_job_step bumps); finished jobs
        measure from updated_at (time since completion).

        Args:
            job: The ingestion job to snapshot.
            now: Current naive-UTC time, matching the queue's CURRENT_TIMESTAMP clock.
            queue_position: 1-based position among pending jobs, or None when not pending.

        Returns:
            A frozen JobView ready to render.
        """
        if job.status == JobStatus.PENDING:
            state_label = "queued"
            reference = job.created_at
        elif job.status == JobStatus.PARSING:
            state_label = job.step or "parsing"
            reference = job.updated_at
        else:
            state_label = job.status  # "done" or "failed" as the plain status value
            reference = job.updated_at
        return cls(
            id=job.id,
            source=job.source,
            status=job.status,
            state_label=state_label,
            elapsed=format_elapsed((now - reference).total_seconds()),
            queue_position=queue_position,
            retry_count=job.retry_count,
            last_error=job.last_error,
            receipt_id=job.receipt_id,
        )
