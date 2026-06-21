"""Insights web routes: the analysis shell and per-analysis HTML fragments.

The page is a shell hosting a growing set of analyses. The canonical URL /insights/{view}
is content-negotiated: htmx requests get the bare fragment to swap into the panel, plain
requests get the full shell with that fragment embedded and the select pre-set.
"""

from __future__ import annotations

# Request is imported at runtime: FastAPI resolves the `request: Request` annotation when
# wiring the route, so it must exist in this module's namespace, not only under TYPE_CHECKING.
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from cartlog.web.guards import require_read
from cartlog.web.htmx import wants_partial
from cartlog.web.insights import DEFAULT_VIEW, INSIGHT_VIEWS, get_view
from cartlog.web.templating import templates

# Insights is read-only, matching the access level of the page it replaces.
router = APIRouter(dependencies=[Depends(require_read)])


@router.get("/charts")
def charts_redirect() -> RedirectResponse:
    """Permanently redirect the legacy /charts path to its Insights replacement."""
    return RedirectResponse("/insights", status_code=301)


@router.get("/insights")
def insights_index() -> RedirectResponse:
    """Land on the default analysis. Temporary redirect since the default may change."""
    return RedirectResponse(f"/insights/{DEFAULT_VIEW.key}", status_code=307)


@router.get("/insights/{view}", response_class=HTMLResponse)
def insights_view(view: str, request: Request) -> HTMLResponse:
    """Render one analysis: the bare fragment for htmx, the full shell otherwise."""
    selected = get_view(view)
    if selected is None:
        raise HTTPException(status_code=404, detail="Unknown analysis")
    if wants_partial(request):
        # Fragments are self-contained (they self-fetch their JSON), so no server context is needed.
        return templates.TemplateResponse(request, selected.template, {})
    return templates.TemplateResponse(
        request,
        "insights.html",
        {"views": INSIGHT_VIEWS, "selected": selected},
    )
