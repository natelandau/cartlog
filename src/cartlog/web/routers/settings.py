"""Settings area: configure how cartlog ingests receipts (currently the watch-folder channel)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session  # noqa: TC002  # runtime import for FastAPI Depends

from cartlog.ingest.folder_watcher import get_folder_config
from cartlog.web.dependencies import get_session
from cartlog.web.templating import templates

router = APIRouter()

# poll_interval below this would let the poller busy-loop; settle below zero is meaningless.
_MIN_POLL_INTERVAL = 1.0


def _folder_view(
    session: Session,
    *,
    values: dict[str, object] | None = None,
    errors: dict[str, str] | None = None,
    saved: bool = False,
) -> dict[str, object]:
    """Build the watch-folder panel context.

    `values` holds what the form should display in each field; on a rejected save it carries
    the user's own input so their edits survive, and otherwise it mirrors the stored config.
    `folder` always carries the stored row so the status line reflects reality.
    """
    config = get_folder_config(session)
    if values is None:
        values = {
            "enabled": config.enabled,
            "watch_dir": config.watch_dir or "",
            "poll_interval": config.poll_interval,
            "settle_seconds": config.settle_seconds,
        }
    return {"folder": config, "values": values, "errors": errors or {}, "saved": saved}


@router.get("/admin/settings", response_class=HTMLResponse)
def settings_index(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> HTMLResponse:
    """Render the settings page, the home for configuring how cartlog ingests receipts."""
    return templates.TemplateResponse(request, "settings.html", _folder_view(session))


@router.get("/admin/settings/folder", response_class=HTMLResponse)
def folder_settings(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> HTMLResponse:
    """Render the watch-folder panel fragment (current config and status)."""
    return templates.TemplateResponse(
        request, "partials/_settings_folder.html", _folder_view(session)
    )


@router.post("/admin/settings/folder", response_class=HTMLResponse)
def save_folder_settings(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    watch_dir: Annotated[str, Form()] = "",
    enabled: Annotated[bool, Form()] = False,  # noqa: FBT002 - FastAPI binds this form toggle field
    poll_interval: Annotated[float, Form()] = 10.0,
    settle_seconds: Annotated[float, Form()] = 5.0,
) -> HTMLResponse:
    """Validate and persist the watch-folder config; re-render the panel with field-level errors.

    Each problem is attached to the field that caused it so the form can show the message inline,
    and a non-existent or non-writable directory is rejected without changing the stored config,
    so enabling the channel can never point the poller at an unusable path.
    """
    config = get_folder_config(session)
    cleaned = watch_dir.strip()
    errors: dict[str, str] = {}

    if cleaned:
        path = Path(cleaned)
        if not path.is_dir():
            errors["watch_dir"] = f"No such directory: {cleaned}"
        elif not os.access(path, os.W_OK):
            errors["watch_dir"] = f"cartlog cannot write to {cleaned}"
    elif enabled:
        errors["watch_dir"] = "Set a watch directory before enabling the folder."

    if poll_interval < _MIN_POLL_INTERVAL:
        errors["poll_interval"] = "Must be at least 1 second."
    if settle_seconds < 0:
        errors["settle_seconds"] = "Cannot be negative."

    if errors:
        session.rollback()
        context = _folder_view(
            session,
            values={
                "enabled": enabled,
                "watch_dir": cleaned,
                "poll_interval": poll_interval,
                "settle_seconds": settle_seconds,
            },
            errors=errors,
        )
        return templates.TemplateResponse(
            request, "partials/_settings_folder.html", context, status_code=422
        )

    config.watch_dir = cleaned or None
    config.enabled = enabled
    config.poll_interval = poll_interval
    config.settle_seconds = settle_seconds
    session.commit()
    return templates.TemplateResponse(
        request, "partials/_settings_folder.html", _folder_view(session, saved=True)
    )
