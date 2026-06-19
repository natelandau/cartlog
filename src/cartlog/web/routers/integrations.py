"""Integrations area: configure how receipts reach cartlog (Apple Shortcut, and later watch folder and email)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session  # noqa: TC002  # runtime import for FastAPI Depends

from cartlog.constants import SHORTCUT_URL
from cartlog.ingest.folder_watcher import get_folder_config
from cartlog.web.dependencies import get_session
from cartlog.web.templating import templates

router = APIRouter()


def _folder_context(session: Session) -> dict[str, object]:
    """Build the template context for the watch-folder panel."""
    return {"folder": get_folder_config(session)}


@router.get("/admin/integrations", response_class=HTMLResponse)
def integrations_index(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> HTMLResponse:
    """Render the integrations page, the entry point for configuring how receipts reach cartlog."""
    # Resolve by route name so the shown URL tracks the upload route's actual path and any
    # root_path prefix, and stays correct behind whatever host/port the user runs cartlog on.
    upload_url = str(request.url_for("upload_receipts"))
    return templates.TemplateResponse(
        request,
        "integrations.html",
        {"upload_url": upload_url, "shortcut_url": SHORTCUT_URL, **_folder_context(session)},
    )


@router.get("/admin/integrations/folder", response_class=HTMLResponse)
def folder_settings(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> HTMLResponse:
    """Render the watch-folder panel fragment (current config and status)."""
    return templates.TemplateResponse(
        request,
        "partials/_integrations_folder.html",
        {**_folder_context(session), "error": None},
    )


@router.post("/admin/integrations/folder", response_class=HTMLResponse)
def save_folder_settings(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    watch_dir: Annotated[str, Form()] = "",
    enabled: Annotated[bool, Form()] = False,  # noqa: FBT002 - FastAPI binds this form checkbox field
    poll_interval: Annotated[float, Form()] = 10.0,
    settle_seconds: Annotated[float, Form()] = 5.0,
) -> HTMLResponse:
    """Validate and persist the watch-folder config; re-render the panel with the outcome.

    A non-existent or non-writable directory is rejected without changing the stored config,
    so enabling the channel can never point the poller at an unusable path.
    """
    config = get_folder_config(session)
    cleaned = watch_dir.strip()
    error: str | None = None
    if cleaned:
        path = Path(cleaned)
        if not path.is_dir():
            error = f"Directory does not exist: {cleaned}"
        elif not os.access(path, os.W_OK):
            error = f"Directory is not a writable directory: {cleaned}"
    elif enabled:
        error = "Set a watch directory before enabling the folder channel."

    if error is None:
        config.watch_dir = cleaned or None
        config.enabled = enabled
        config.poll_interval = poll_interval
        config.settle_seconds = settle_seconds
        session.commit()
    else:
        session.rollback()

    return templates.TemplateResponse(
        request,
        "partials/_integrations_folder.html",
        {**_folder_context(session), "error": error},
    )
