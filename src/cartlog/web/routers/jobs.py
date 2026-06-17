"""Job-facing web routes: the Jobs tab, its polling fragments, and per-job status."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

# Runtime import: FastAPI resolves Annotated[Session, Depends(...)] in this module's namespace.
from sqlalchemy.orm import Session  # noqa: TC002

from cartlog.clock import naive_utcnow
from cartlog.db.models import IngestionJob, JobStatus
from cartlog.web.dependencies import get_session
from cartlog.web.jobs_view import JobView
from cartlog.web.templating import templates

router = APIRouter()

# Statuses considered "in flight" for the active list and the nav badge.
_ACTIVE_STATUSES = (JobStatus.PENDING, JobStatus.PARSING)
# Statuses shown in the "recent" section, newest first.
_FINISHED_STATUSES = (JobStatus.DONE, JobStatus.FAILED)
_RECENT_LIMIT = 20


def _list_context(session: Session) -> dict[str, object]:
    """Build the active and recent JobView lists for the list partial.

    Args:
        session: SQLAlchemy session used to read job rows.

    Returns:
        A template context with the active list, recent list, and active count.
    """
    now = naive_utcnow()
    active_jobs = (
        session.query(IngestionJob)
        .filter(IngestionJob.status.in_(_ACTIVE_STATUSES))
        .order_by(IngestionJob.created_at, IngestionJob.id)
        .all()
    )
    active: list[JobView] = []
    pending_seen = 0
    for job in active_jobs:
        position: int | None = None
        if job.status == JobStatus.PENDING:
            pending_seen += 1
            position = pending_seen
        active.append(JobView.from_job(job, now=now, queue_position=position))

    recent_jobs = (
        session.query(IngestionJob)
        .filter(IngestionJob.status.in_(_FINISHED_STATUSES))
        .order_by(IngestionJob.updated_at.desc(), IngestionJob.id.desc())
        .limit(_RECENT_LIMIT)
        .all()
    )
    recent = [JobView.from_job(job, now=now) for job in recent_jobs]
    return {"active": active, "recent": recent, "active_count": len(active)}


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request) -> HTMLResponse:
    """Render the Jobs tab shell; its body polls the list partial."""
    return templates.TemplateResponse(request, "jobs.html", {})


@router.get("/jobs/list/partial", response_class=HTMLResponse)
def jobs_list_partial(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> HTMLResponse:
    """Render the active and recent job list as an HTML fragment the Jobs tab polls."""
    return templates.TemplateResponse(request, "partials/_jobs_list.html", _list_context(session))


@router.get("/jobs/badge/partial", response_class=HTMLResponse)
def jobs_badge_partial(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> HTMLResponse:
    """Render the nav badge fragment with the count of in-flight jobs."""
    active_count = (
        session.query(IngestionJob).filter(IngestionJob.status.in_(_ACTIVE_STATUSES)).count()
    )
    return templates.TemplateResponse(
        request, "partials/_nav_jobs_badge.html", {"active_count": active_count}
    )


@router.get("/jobs/{job_id}/partial", response_class=HTMLResponse)
def job_status_partial(
    job_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Render a single job's status as an HTML fragment so the upload page can poll it."""
    job = session.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(request, "partials/_job_status.html", {"job": job})


@router.get("/jobs/{job_id}")
def job_status(job_id: int, session: Annotated[Session, Depends(get_session)]) -> dict[str, object]:
    """Return the current status of an ingestion job for polling."""
    job = session.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.id,
        "status": job.status,
        "step": job.step,
        "receipt_id": job.receipt_id,
        "retry_count": job.retry_count,
        "last_error": job.last_error,
    }
