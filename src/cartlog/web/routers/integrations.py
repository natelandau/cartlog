"""Integrations area: configure how receipts reach cartlog from outside the app (Apple Shortcut)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from cartlog.constants import SHORTCUT_URL
from cartlog.web.guards import require_admin
from cartlog.web.templating import templates

# The integrations area is admin-only; guard declared at router level.
router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/admin/integrations", response_class=HTMLResponse)
def integrations_index(request: Request) -> HTMLResponse:
    """Render the integrations page, the entry point for configuring how receipts reach cartlog."""
    # Resolve by route name so the shown URL tracks the upload route's actual path and any
    # root_path prefix, and stays correct behind whatever host/port the user runs cartlog on.
    upload_url = str(request.url_for("upload_receipts"))
    return templates.TemplateResponse(
        request,
        "integrations.html",
        {"upload_url": upload_url, "shortcut_url": SHORTCUT_URL},
    )
