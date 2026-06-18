"""Analytics web routes: read-only JSON endpoints, search page, and charts shell."""

from __future__ import annotations

from datetime import date  # noqa: TC003  # used at runtime by FastAPI for query-param coercion
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session  # noqa: TC002  # runtime import for FastAPI Depends resolution

from cartlog.analytics.results import (
    CategorySpend,
    PriceHistory,
    SearchResult,
    StoreComparison,
)
from cartlog.analytics.search_sort import SearchSortKey

# Runtime import: FastAPI resolves Annotated[AnalyticsService, Depends(...)] in this
# module's namespace, so AnalyticsService must be importable at runtime.
from cartlog.analytics.service import AnalyticsService
from cartlog.categories.service import CategoryService
from cartlog.db.models import LineItem
from cartlog.db.sort import SortDir
from cartlog.receipts.service import apply_line_item_edit
from cartlog.web.dependencies import get_analytics_service, get_session
from cartlog.web.templating import templates
from cartlog.web.units_display import read_unit_system

router = APIRouter()

ServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]


@router.get("/api/analytics/price-history", response_model=PriceHistory)
def price_history_api(
    service: ServiceDep,
    product: Annotated[str, Query(min_length=1)],
    store: Annotated[str | None, Query()] = None,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
) -> PriceHistory:
    """Return a product's price history as JSON."""
    # An explicitly empty ?store= means "no filter", not "match the empty store name".
    return service.price_history(product, store=store or None, start=from_, end=to)


@router.get("/api/analytics/store-comparison", response_model=StoreComparison)
def store_comparison_api(
    service: ServiceDep,
    product: Annotated[str, Query(min_length=1)],
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
) -> StoreComparison:
    """Return a product's per-store price comparison as JSON."""
    return service.store_comparison(product, start=from_, end=to)


@router.get("/api/analytics/category-spend", response_model=CategorySpend)
def category_spend_api(
    service: ServiceDep,
    category: Annotated[str | None, Query()] = None,
    store: Annotated[str | None, Query()] = None,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
) -> CategorySpend:
    """Return category spend (single category or full breakdown) as JSON."""
    # Empty ?category=/?store= mean "no filter"; otherwise an empty category would
    # collapse the full breakdown to an all-zero result.
    return service.category_spend(category or None, store=store or None, start=from_, end=to)


@router.get("/api/analytics/search", response_model=list[SearchResult])
def search_api(
    service: ServiceDep,
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[SearchResult]:
    """Return free-text search results as JSON."""
    return service.search(q, limit=limit)


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request) -> HTMLResponse:
    """Render the search page with an HTMX-driven results area."""
    return templates.TemplateResponse(request, "search.html", {})


@router.get("/search/results", response_class=HTMLResponse)
def search_results(
    request: Request,
    service: ServiceDep,
    q: Annotated[str, Query(min_length=1)],
    sort: SearchSortKey = SearchSortKey.DATE,
    direction: SortDir = SortDir.DESC,
) -> HTMLResponse:
    """Render search results as an HTML fragment for HTMX swapping, ordered by `sort`.

    Typing `sort`/`direction` as enums makes FastAPI reject an unknown value with a 422
    rather than silently rendering a wrongly ordered list.
    """
    results = service.search(q, sort=sort, descending=direction == SortDir.DESC)
    return templates.TemplateResponse(
        request,
        "partials/_search_results.html",
        {
            "results": results,
            "q": q,
            "sort": sort,
            "direction": direction,
            "product_names": service.product_names(),
            "unit_system": read_unit_system(request),
        },
    )


@router.get("/search/items/{line_item_id}", response_class=HTMLResponse)
def search_item_row(
    line_item_id: int,
    request: Request,
    service: ServiceDep,
) -> HTMLResponse:
    """Render the read-only row for one line (used by the inline editor's Cancel)."""
    row = service.line_item_row(line_item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Line item not found")
    return templates.TemplateResponse(
        request, "partials/_search_row.html", {"r": row, "unit_system": read_unit_system(request)}
    )


@router.get("/search/items/{line_item_id}/edit", response_class=HTMLResponse)
def search_item_edit(
    line_item_id: int,
    request: Request,
    service: ServiceDep,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Render the editable row for one line: product datalist input + category picker."""
    row = service.line_item_row(line_item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Line item not found")
    category_options = CategoryService(session).candidate_pairs(include_system=True)
    return templates.TemplateResponse(
        request,
        "partials/_search_row_edit.html",
        {"r": row, "category_options": category_options},
    )


@router.post("/search/items/{line_item_id}", response_class=HTMLResponse)
def search_item_save(
    line_item_id: int,
    request: Request,
    service: ServiceDep,
    session: Annotated[Session, Depends(get_session)],
    canonical_name: Annotated[str, Form()],
    category_id: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Reassign a line's product (and optionally its category), returning the updated row.

    A blank canonical name or a bad category id re-renders the edit row with a 422 and an
    error message; base.html opts 422 responses into an htmx swap for search rows. An empty
    category_id means 'leave the category unchanged' (the picker cannot clear a category to
    null), matching the receipt editor.
    """
    # 404 when the line is missing OR not something search would surface (failed receipt):
    # line_item_row applies search()'s counted-status filter, so it guards both at once. The
    # cheap session.get runs first and narrows line_item to non-None for the edit below.
    line_item = session.get(LineItem, line_item_id)
    if line_item is None or service.line_item_row(line_item_id) is None:
        raise HTTPException(status_code=404, detail="Line item not found")

    name = canonical_name.strip()
    error: str | None = None
    cat_id: int | None = None
    # Parse the picker value defensively: a non-numeric category_id reaches us only via a
    # tampered POST, but it must surface as an inline 422 rather than an unhandled 500.
    try:
        cat_id = int(category_id) if category_id.strip() else None
    except ValueError:
        error = "Invalid category selection."

    if error is None and not name:
        error = "Product name is required."

    if error is None:
        try:
            apply_line_item_edit(session, line_item, canonical_name=name, category_id=cat_id)
        except ValueError as exc:
            session.rollback()
            error = str(exc)

    # Re-fetch for the response. A concurrent change that moved the line out of counted
    # status between the entry gate and here yields None; 404 rather than deref it in a template.
    row = service.line_item_row(line_item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Line item not found")

    if error is not None:
        category_options = CategoryService(session).candidate_pairs(include_system=True)
        return templates.TemplateResponse(
            request,
            "partials/_search_row_edit.html",
            {"r": row, "category_options": category_options, "errors": error},
            status_code=422,
        )

    return templates.TemplateResponse(
        request, "partials/_search_row.html", {"r": row, "unit_system": read_unit_system(request)}
    )


@router.get("/charts", response_class=HTMLResponse)
def charts_page(request: Request) -> HTMLResponse:
    """Render the analytics charts shell; Plotly.js fetches the JSON endpoints client-side."""
    return templates.TemplateResponse(request, "charts.html", {})
