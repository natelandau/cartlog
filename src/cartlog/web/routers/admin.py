"""Admin area: product mapping (merge) and transformation-rule management."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum  # noqa: TC003  # used at runtime in helper annotations
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session  # noqa: TC002  # runtime import for FastAPI Depends

from cartlog.analytics.service import AnalyticsService  # noqa: TC001  # FastAPI Depends at runtime
from cartlog.db.models import Category, LineItem, Product, ProductMerge, Receipt, Store, StoreMerge
from cartlog.db.query_helpers import text_filter
from cartlog.db.sort import SortDir, apply_sort
from cartlog.exceptions import ProductMergeError, StoreMergeError
from cartlog.products.service import merge_products
from cartlog.stores.service import merge_stores
from cartlog.web.auth import require_admin
from cartlog.web.dependencies import get_analytics_service, get_session
from cartlog.web.htmx import wants_partial
from cartlog.web.sort import (
    OCCURRENCE_COUNT,
    PRODUCT_SORT_COLUMNS,
    STORE_MERGE_SORT_COLUMNS,
    STORE_SORT_COLUMNS,
    TRANSFORMATION_SORT_COLUMNS,
    VISIT_COUNT,
    ProductSortKey,
    StoreMergeSortKey,
    StoreSortKey,
    TransformationSortKey,
)
from cartlog.web.templating import templates

if TYPE_CHECKING:
    from collections.abc import Callable

    from cartlog.db.base import Base

# All admin routes require an Admin user; the dependency is declared once at the router level
# so individual handlers stay focused on business logic rather than auth boilerplate.
router = APIRouter(dependencies=[Depends(require_admin)])


@dataclass(frozen=True)
class _TableView:
    """Static config for one admin table: its templates, row query, and full-page-only extras.

    `rows(session, *, q, sort, direction)` returns the table rows; `extra_context(session, *,
    partial)` supplies any context the surrounding page needs beyond the swappable table fragment.
    """

    full_template: str
    partial_template: str
    rows: Callable[..., list]
    extra_context: Callable[..., dict] | None = None


def _render_table(
    request: Request,
    session: Session,
    view: _TableView,
    *,
    q: str | None,
    sort: StrEnum,
    direction: SortDir,
) -> HTMLResponse:
    """Render an admin table: a full page on direct navigation, a fragment for htmx swaps."""
    partial = wants_partial(request)
    context: dict[str, object] = {
        "rows": view.rows(session, q=q, sort=sort, direction=direction),
        "q": q or "",
        "sort": sort,
        "direction": direction,
    }
    if view.extra_context is not None:
        context.update(view.extra_context(session, partial=partial))
    template = view.partial_template if partial else view.full_template
    return templates.TemplateResponse(request, template, context)


def _delete_and_refresh(  # noqa: PLR0913 - explicit per-table delete config
    request: Request,
    session: Session,
    *,
    model: type[Base],
    rule_id: int,
    not_found: str,
    view: _TableView,
    q: str | None,
    sort: StrEnum,
    direction: SortDir,
) -> HTMLResponse:
    """Delete a rule by id (404 if absent), commit, then re-render the refreshed table fragment."""
    rule = session.get(model, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=not_found)
    session.delete(rule)
    session.commit()
    return _render_table(request, session, view, q=q, sort=sort, direction=direction)


@router.get("/admin", response_class=HTMLResponse)
def admin_index(
    request: Request,
    service: Annotated[AnalyticsService, Depends(get_analytics_service)],
) -> HTMLResponse:
    """Render the admin landing page with the mapping tools and the LLM parsing-cost figures."""
    return templates.TemplateResponse(
        request,
        "admin/index.html",
        {"parsing_cost": service.parsing_cost_overview()},
    )


# --- Products --------------------------------------------------------------------------------


def _all_product_names(session: Session) -> list[str]:
    """Return every product name, ordered, for the merge-target datalist."""
    return [
        name
        for (name,) in session.query(Product.canonical_name).order_by(
            func.lower(Product.canonical_name)
        )
    ]


def _product_rows(
    session: Session, *, q: str | None, sort: ProductSortKey, direction: SortDir
) -> list[tuple[Product, int, str | None]]:
    """Return (product, occurrence_count, category_name) rows, filtered and sorted."""
    query = (
        session.query(Product, OCCURRENCE_COUNT, Category.name)
        .outerjoin(Category, Category.id == Product.category_id)
        .outerjoin(Product.line_items)
        .group_by(Product.id)
    )
    if q:
        query = query.filter(text_filter(q, Product.canonical_name))
    query = apply_sort(query, PRODUCT_SORT_COLUMNS[sort], direction)
    return [(product, count, category) for product, count, category in query]


def _product_extra(session: Session, *, partial: bool) -> dict:
    # The merge-target datalist lives only on the full page (outside the swapped fragment), so
    # skip the extra all-names query on htmx sort/filter swaps.
    return {"product_names": [] if partial else _all_product_names(session)}


PRODUCTS_VIEW = _TableView(
    full_template="admin/products.html",
    partial_template="partials/_admin_products_table.html",
    rows=_product_rows,
    extra_context=_product_extra,
)


@router.get("/admin/products", response_class=HTMLResponse)
def admin_products(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Query()] = None,
    sort: ProductSortKey = ProductSortKey.NAME,
    direction: SortDir = SortDir.ASC,
) -> HTMLResponse:
    """Render the sortable/filterable products table with a merge control per row."""
    return _render_table(request, session, PRODUCTS_VIEW, q=q, sort=sort, direction=direction)


@router.get("/admin/products/{product_id}/merge/confirm", response_class=HTMLResponse)
def admin_merge_confirm(
    product_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    target: Annotated[str, Query()],
) -> HTMLResponse:
    """Render a confirmation dialog naming the source and target products."""
    source = session.get(Product, product_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Product not found")
    target_product = (
        session.query(Product).filter(Product.canonical_name == target.strip()).one_or_none()
    )
    error: str | None = None
    if target_product is None:
        error = f'No product named "{target.strip()}".'
    elif target_product.id == source.id:
        error = "Pick a different product to merge into."
    # The count is only shown in the no-error branch of the dialog, so skip the query otherwise.
    occurrences = 0
    if error is None:
        occurrences = (
            session.query(func.count(LineItem.id)).filter(LineItem.product_id == source.id).scalar()
            or 0
        )
    return templates.TemplateResponse(
        request,
        "partials/_merge_confirm.html",
        {
            "source": source,
            "target": target_product,
            "occurrences": occurrences,
            "error": error,
        },
    )


@router.post("/admin/products/{product_id}/merge", response_class=HTMLResponse)
def admin_merge(  # noqa: PLR0913 - form fields HTMX includes to preserve sort/filter state
    product_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    target_id: Annotated[int, Form()],
    q: Annotated[str | None, Form()] = None,
    sort: Annotated[ProductSortKey, Form()] = ProductSortKey.NAME,
    direction: Annotated[SortDir, Form()] = SortDir.ASC,
) -> HTMLResponse:
    """Merge the product into the target, returning the refreshed products fragment."""
    try:
        merge_products(session, source_id=product_id, target_id=target_id)
        session.commit()
    except ProductMergeError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _render_table(request, session, PRODUCTS_VIEW, q=q, sort=sort, direction=direction)


# --- Stores ----------------------------------------------------------------------------------


def _all_stores(session: Session) -> list[Store]:
    """Return every store ordered by chain then location, for the merge-target select."""
    return list(
        session.query(Store).order_by(func.lower(Store.chain_name), func.lower(Store.location))
    )


def _store_rows(
    session: Session, *, q: str | None, sort: StoreSortKey, direction: SortDir
) -> list[tuple[Store, int]]:
    """Return (store, visit_count) rows, filtered by chain/location and sorted."""
    query = session.query(Store, VISIT_COUNT).outerjoin(Store.receipts).group_by(Store.id)
    if q:
        query = query.filter(text_filter(q, Store.chain_name, Store.location))
    query = apply_sort(query, STORE_SORT_COLUMNS[sort], direction)
    return [(store, count) for store, count in query]


def _store_extra(session: Session, *, partial: bool) -> dict:  # noqa: ARG001 - select shown on both
    return {"stores": _all_stores(session)}


STORES_VIEW = _TableView(
    full_template="admin/stores.html",
    partial_template="partials/_admin_stores_table.html",
    rows=_store_rows,
    extra_context=_store_extra,
)


@router.get("/admin/stores", response_class=HTMLResponse)
def admin_stores(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Query()] = None,
    sort: StoreSortKey = StoreSortKey.CHAIN,
    direction: SortDir = SortDir.ASC,
) -> HTMLResponse:
    """Render the sortable/filterable stores table with a merge control per row."""
    return _render_table(request, session, STORES_VIEW, q=q, sort=sort, direction=direction)


@router.get("/admin/stores/{store_id}/merge/confirm", response_class=HTMLResponse)
def admin_store_merge_confirm(
    store_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    target_id: Annotated[int, Query()],
) -> HTMLResponse:
    """Render a confirmation dialog naming the source and target stores."""
    source = session.get(Store, store_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Store not found")
    target_store = session.get(Store, target_id)
    error: str | None = None
    if target_store is None:
        error = "Pick a store to merge into."
    elif target_store.id == source.id:
        error = "Pick a different store to merge into."
    # The count is only shown in the no-error branch of the dialog, so skip the query otherwise.
    visits = 0
    if error is None:
        visits = (
            session.query(func.count(Receipt.id)).filter(Receipt.store_id == source.id).scalar()
            or 0
        )
    return templates.TemplateResponse(
        request,
        "partials/_store_merge_confirm.html",
        {"source": source, "target": target_store, "visits": visits, "error": error},
    )


@router.post("/admin/stores/{store_id}/merge", response_class=HTMLResponse)
def admin_store_merge(  # noqa: PLR0913 - form fields HTMX includes to preserve sort/filter state
    store_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    target_id: Annotated[int, Form()],
    q: Annotated[str | None, Form()] = None,
    sort: Annotated[StoreSortKey, Form()] = StoreSortKey.CHAIN,
    direction: Annotated[SortDir, Form()] = SortDir.ASC,
) -> HTMLResponse:
    """Merge the store into the target, returning the refreshed stores fragment."""
    try:
        merge_stores(session, source_id=store_id, target_id=target_id)
        session.commit()
    except StoreMergeError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _render_table(request, session, STORES_VIEW, q=q, sort=sort, direction=direction)


# --- Store-merge rules -----------------------------------------------------------------------


def _store_merge_rows(
    session: Session, *, q: str | None, sort: StoreMergeSortKey, direction: SortDir
) -> list[tuple[StoreMerge, Store]]:
    """Return (rule, target_store) rows for the store-merges table, filtered and sorted."""
    query = session.query(StoreMerge, Store).join(Store, Store.id == StoreMerge.target_store_id)
    if q:
        query = query.filter(
            text_filter(
                q, StoreMerge.source_chain_name, StoreMerge.source_location, Store.chain_name
            )
        )
    query = apply_sort(query, STORE_MERGE_SORT_COLUMNS[sort], direction)
    return [(rule, store) for rule, store in query]


STORE_MERGES_VIEW = _TableView(
    full_template="admin/store_merges.html",
    partial_template="partials/_admin_store_merges_table.html",
    rows=_store_merge_rows,
)


@router.get("/admin/store-merges", response_class=HTMLResponse)
def admin_store_merges(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Query()] = None,
    sort: StoreMergeSortKey = StoreMergeSortKey.DATE,
    direction: SortDir = SortDir.DESC,
) -> HTMLResponse:
    """Render the sortable/filterable list of saved store-merge rules."""
    return _render_table(request, session, STORE_MERGES_VIEW, q=q, sort=sort, direction=direction)


@router.get("/admin/store-merges/{rule_id}/delete/confirm", response_class=HTMLResponse)
def admin_store_merge_delete_confirm(
    rule_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Render a confirmation dialog for deleting one store-merge rule."""
    rule = session.get(StoreMerge, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Store merge not found")
    return templates.TemplateResponse(
        request,
        "partials/_store_merge_delete_confirm.html",
        {"rule": rule, "target_name": rule.target_store.chain_name},
    )


@router.post("/admin/store-merges/{rule_id}/delete", response_class=HTMLResponse)
def admin_store_merge_delete(
    rule_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Form()] = None,
    sort: Annotated[StoreMergeSortKey, Form()] = StoreMergeSortKey.DATE,
    direction: Annotated[SortDir, Form()] = SortDir.DESC,
) -> HTMLResponse:
    """Delete a store-merge rule (no back-dating), returning the refreshed fragment."""
    return _delete_and_refresh(
        request,
        session,
        model=StoreMerge,
        rule_id=rule_id,
        not_found="Store merge not found",
        view=STORE_MERGES_VIEW,
        q=q,
        sort=sort,
        direction=direction,
    )


# --- Transformation rules --------------------------------------------------------------------


def _transformation_rows(
    session: Session, *, q: str | None, sort: TransformationSortKey, direction: SortDir
) -> list[tuple[ProductMerge, str]]:
    """Return (rule, target_name) rows for the transformations table, filtered and sorted."""
    query = session.query(ProductMerge, Product.canonical_name).join(
        Product, Product.id == ProductMerge.target_product_id
    )
    if q:
        query = query.filter(text_filter(q, ProductMerge.source_name, Product.canonical_name))
    query = apply_sort(query, TRANSFORMATION_SORT_COLUMNS[sort], direction)
    return [(rule, name) for rule, name in query]


TRANSFORMATIONS_VIEW = _TableView(
    full_template="admin/transformations.html",
    partial_template="partials/_admin_transformations_table.html",
    rows=_transformation_rows,
)


@router.get("/admin/transformations", response_class=HTMLResponse)
def admin_transformations(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Query()] = None,
    sort: TransformationSortKey = TransformationSortKey.DATE,
    direction: SortDir = SortDir.DESC,
) -> HTMLResponse:
    """Render the sortable/filterable list of saved transformation rules."""
    return _render_table(
        request, session, TRANSFORMATIONS_VIEW, q=q, sort=sort, direction=direction
    )


@router.get("/admin/transformations/{rule_id}/delete/confirm", response_class=HTMLResponse)
def admin_transformation_delete_confirm(
    rule_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """Render a confirmation dialog for deleting one transformation rule."""
    rule = session.get(ProductMerge, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Transformation not found")
    return templates.TemplateResponse(
        request,
        "partials/_transformation_delete_confirm.html",
        {"rule": rule, "target_name": rule.target_product.canonical_name},
    )


@router.post("/admin/transformations/{rule_id}/delete", response_class=HTMLResponse)
def admin_transformation_delete(
    rule_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Form()] = None,
    sort: Annotated[TransformationSortKey, Form()] = TransformationSortKey.DATE,
    direction: Annotated[SortDir, Form()] = SortDir.DESC,
) -> HTMLResponse:
    """Delete a transformation rule (no back-dating), returning the refreshed fragment."""
    return _delete_and_refresh(
        request,
        session,
        model=ProductMerge,
        rule_id=rule_id,
        not_found="Transformation not found",
        view=TRANSFORMATIONS_VIEW,
        q=q,
        sort=sort,
        direction=direction,
    )
