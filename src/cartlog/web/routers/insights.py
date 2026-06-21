"""Insights web routes: the analysis shell and per-analysis HTML fragments.

The page is a shell hosting a growing set of analyses. The canonical URL /insights/{view}
is content-negotiated: htmx requests get the bare fragment to swap into the panel, plain
requests get the full shell with that fragment embedded and the select pre-set.
"""

from __future__ import annotations

from datetime import date  # runtime use by FastAPI for query coercion
from typing import Annotated

# Request is imported at runtime: FastAPI resolves the `request: Request` annotation when
# wiring the route, so it must exist in this module's namespace, not only under TYPE_CHECKING.
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BeforeValidator

from cartlog.analytics.results import PriceBasis, ScaleMode, StorePairSort
from cartlog.analytics.service import AnalyticsService
from cartlog.web.dependencies import get_analytics_service
from cartlog.web.guards import require_read
from cartlog.web.htmx import wants_partial
from cartlog.web.insights import DEFAULT_VIEW, INSIGHT_VIEWS, get_view
from cartlog.web.templating import templates
from cartlog.web.units_display import read_unit_system

# Insights is read-only, matching the access level of the page it replaces.
router = APIRouter(dependencies=[Depends(require_read)])

ServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]

# A comparison requires exactly two stores: one for each side of the table.
_MIN_STORES_FOR_COMPARISON = 2


def _blank_to_none(value: str | None) -> str | None:
    """Coerce an empty query value to None.

    The toolbar form always submits its date inputs (`from`/`to`), so an unfilled date
    arrives as an empty string. Without this, FastAPI cannot parse "" as a date and rejects
    the whole request with a 422, which silently breaks the form's auto-reload.
    """
    return value or None


# Optional date query params that tolerate an empty string (unfilled date input) as "absent".
OptionalDate = Annotated[date | None, BeforeValidator(_blank_to_none)]


@router.get("/charts")
def charts_redirect() -> RedirectResponse:
    """Permanently redirect the legacy /charts path to its Insights replacement."""
    return RedirectResponse("/insights", status_code=301)


@router.get("/insights")
def insights_index() -> RedirectResponse:
    """Land on the default analysis. Temporary redirect since the default may change."""
    return RedirectResponse(f"/insights/{DEFAULT_VIEW.key}", status_code=307)


@router.get("/insights/{view}", response_class=HTMLResponse)
def insights_view(  # noqa: PLR0913 - toolbar query params map 1:1 to filter controls
    view: str,
    request: Request,
    service: ServiceDep,
    store_a: Annotated[int | None, Query()] = None,
    store_b: Annotated[int | None, Query()] = None,
    product: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[int] | None, Query()] = None,
    from_: Annotated[OptionalDate, Query(alias="from")] = None,
    to: Annotated[OptionalDate, Query()] = None,
    scale: ScaleMode = ScaleMode.PERCENT,
    basis: PriceBasis = PriceBasis.TYPICAL,
    sort: StorePairSort = StorePairSort.ALPHABETICAL,
) -> HTMLResponse:
    """Render one analysis: the bare fragment for htmx, the full shell otherwise.

    The store-comparison view reads the toolbar's query params and is server-rendered;
    the other analyses ignore them and self-fetch their JSON as before.
    """
    selected = get_view(view)
    if selected is None:
        raise HTTPException(status_code=404, detail="Unknown analysis")
    context: dict[str, object] = {}
    if selected.key == "store-comparison":
        context = _store_comparison_context(
            service,
            request,
            store_a=store_a,
            store_b=store_b,
            products=product,
            categories=category,
            start=from_,
            end=to,
            scale=scale,
            basis=basis,
            sort=sort,
        )
    if wants_partial(request):
        return templates.TemplateResponse(request, selected.template, context)
    return templates.TemplateResponse(
        request,
        "insights.html",
        {"views": INSIGHT_VIEWS, "selected": selected, **context},
    )


def _store_comparison_context(  # noqa: PLR0913 - each kwarg is a distinct filter forwarded to the service
    service: AnalyticsService,
    request: Request,
    *,
    store_a: int | None,
    store_b: int | None,
    products: list[str] | None,
    categories: list[int] | None,
    start: date | None,
    end: date | None,
    scale: ScaleMode,
    basis: PriceBasis,
    sort: StorePairSort,
) -> dict[str, object]:
    """Assemble the store-comparison template context.

    Auto-selects the two most-shopped stores when the user has not chosen a pair, and
    returns sc=None (an empty-state flag for the template) when fewer than two stores exist.
    The unit system is derived from the incoming request via read_unit_system.
    """
    stores = service.stores_by_frequency()
    base = {
        "store_options": stores,
        "unit_system": read_unit_system(request),
        "selected_products": products or [],
        "selected_categories": categories or [],
        "date_from": start,
        "date_to": end,
    }
    if len(stores) < _MIN_STORES_FOR_COMPARISON:
        return {"sc": None, **base}
    valid_ids = {s.id for s in stores}
    a_id = store_a if store_a in valid_ids else stores[0].id
    b_id = store_b if store_b in valid_ids else stores[1].id
    # Prevent a same-store comparison by advancing b to the first store that differs from a.
    if a_id == b_id:
        b_id = next(s.id for s in stores if s.id != a_id)
    sc = service.store_pair_comparison(
        a_id,
        b_id,
        product_names=products or None,
        category_ids=categories or None,
        start=start,
        end=end,
        basis=basis,
        scale=scale,
        sort=sort,
    )
    return {"sc": sc, **base}
