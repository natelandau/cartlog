"""Settings area: configure ingestion channels (iOS share sheet, and later watch folder and email)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cartlog.web.templating import templates

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_index(request: Request) -> HTMLResponse:
    """Render the settings page, the entry point for configuring how receipts reach cartlog."""
    # Resolve by route name so the shown URL tracks the upload route's actual path and any
    # root_path prefix, and stays correct behind whatever host/port the user runs cartlog on.
    upload_url = str(request.url_for("upload_receipts"))
    return templates.TemplateResponse(request, "settings.html", {"upload_url": upload_url})
