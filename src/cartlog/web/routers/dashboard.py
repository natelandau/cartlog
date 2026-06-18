# src/cartlog/web/routers/dashboard.py
"""The landing-page dashboard route: a server-rendered, range-filtered overview."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, contains_eager

from cartlog.analytics.ranges import RangePreset, resolve_range
from cartlog.analytics.service import AnalyticsService
from cartlog.db.models import Receipt, ReceiptStatus
from cartlog.db.sort import SortDir
from cartlog.web.dependencies import get_analytics_service, get_session
from cartlog.web.htmx import wants_partial
from cartlog.web.sort import SORT_KEYS, ReceiptSortKey
from cartlog.web.templating import templates
from cartlog.web.viz import build_calendar_heatmap

router = APIRouter()

ServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]


# Maps an htmx swap target's element id to the fragment template that renders it. Adding a
# new independently-swappable region is a one-line entry here, not another branch.
_FRAGMENT_BY_TARGET = {"recent-receipts-table": "partials/_receipt_table.html"}


def _dashboard_template(request: Request) -> str:
    """Pick the fragment to render based on which htmx target asked for it.

    The recent-receipts table sorts by swapping itself; the range chips swap the whole
    dashboard body. A normal (non-htmx) request gets the full page.
    """
    if not wants_partial(request):
        return "dashboard.html"
    target = request.headers.get("HX-Target", "")
    return _FRAGMENT_BY_TARGET.get(target, "partials/_dashboard_body.html")


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    service: ServiceDep,
    range_: Annotated[RangePreset, Query(alias="range")] = RangePreset.LAST_12_MONTHS,
    sort: ReceiptSortKey = ReceiptSortKey.DATE,
    direction: SortDir = SortDir.DESC,
) -> HTMLResponse:
    """Render the data-rich overview for the chosen range, plus a sortable recent table."""
    data = service.dashboard(range_)
    start, end = resolve_range(range_)
    heatmap_grid = build_calendar_heatmap(data.heatmap, start=start, end=end)

    recent = (
        session.query(Receipt)
        .join(Receipt.store)
        .options(contains_eager(Receipt.store))
        .order_by(Receipt.created_at.desc(), Receipt.id.desc())
        .limit(10)
        .all()
    )
    recent = sorted(recent, key=SORT_KEYS[sort], reverse=direction == SortDir.DESC)

    return templates.TemplateResponse(
        request,
        _dashboard_template(request),
        {
            "data": data,
            "heatmap_grid": heatmap_grid,
            "range": range_,
            "ranges": list(RangePreset),
            "receipts": recent,
            "sort": sort,
            "direction": direction,
            "review_status": ReceiptStatus.NEEDS_REVIEW,
            "endpoint": "/",
            "table_id": "recent-receipts-table",
            "show_review": False,
        },
    )
