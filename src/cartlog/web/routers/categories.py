"""Category taxonomy management: tree view, create, rename, merge, delete."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session  # noqa: TC002

from cartlog.categories.service import CategoryService
from cartlog.db.models import Category
from cartlog.exceptions import CategoryError
from cartlog.web.auth import (  # noqa: TC001  # runtime imports: FastAPI Depends resolves Annotated aliases at startup
    RequireEditor,
    RequireRead,
)
from cartlog.web.dependencies import get_session
from cartlog.web.templating import templates

router = APIRouter()


def _optional_id(value: str) -> int | None:
    """Parse an optional id form field: blank -> None, non-integer -> clean 422."""
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid category id") from exc


def _panel_response(
    request: Request, session: Session, *, error: str | None = None, status: int = 200
) -> HTMLResponse:
    """Render the category panel partial, optionally with an error banner and status."""
    return templates.TemplateResponse(
        request,
        "partials/_category_panel.html",
        {"tree": CategoryService(session).tree(), "error": error},
        status_code=status,
    )


@router.get("/categories", response_class=HTMLResponse)
def categories_page(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _user: RequireRead,
) -> HTMLResponse:
    """Render the taxonomy management page (flat category list with product counts)."""
    tree = CategoryService(session).tree()
    return templates.TemplateResponse(request, "categories.html", {"tree": tree})


@router.post("/categories", response_class=HTMLResponse)
def create_category(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _editor: RequireEditor,
    name: Annotated[str, Form()],
) -> HTMLResponse:
    """Create a category; return the refreshed panel."""
    svc = CategoryService(session)
    try:
        svc.create_category(name=name)
        session.commit()
    except CategoryError as exc:
        session.rollback()
        return _panel_response(request, session, error=str(exc), status=422)
    return _panel_response(request, session)


@router.get("/categories/new-inline", response_class=HTMLResponse)
def inline_create_form(request: Request, _user: RequireRead) -> HTMLResponse:
    """Return a small inline form (name only) for creating a category mid-edit."""
    return templates.TemplateResponse(request, "partials/_category_inline_form.html", {})


@router.post("/categories/inline", response_class=HTMLResponse)
def inline_create(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _editor: RequireEditor,
    name: Annotated[str, Form()],
) -> HTMLResponse:
    """Create a category and return a category picker with the new category pre-selected."""
    svc = CategoryService(session)
    try:
        created = svc.create_category(name=name)
        session.commit()
    except CategoryError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "partials/_category_picker.html",
        {"options": svc.candidate_pairs(include_system=True), "current_id": created.id},
    )


@router.get("/categories/{category_id}/rename", response_class=HTMLResponse)
def rename_form(
    request: Request,
    category_id: int,
    session: Annotated[Session, Depends(get_session)],
    _user: RequireRead,
) -> HTMLResponse:
    """Render the rename form partial for the given category."""
    category = session.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return templates.TemplateResponse(
        request,
        "partials/_category_form.html",
        {"mode": "rename", "category": category},
    )


@router.post("/categories/{category_id}/rename", response_class=HTMLResponse)
def rename_category(
    request: Request,
    category_id: int,
    session: Annotated[Session, Depends(get_session)],
    _editor: RequireEditor,
    new_name: Annotated[str, Form()],
) -> HTMLResponse:
    """Rename a category; return the refreshed panel."""
    svc = CategoryService(session)
    try:
        svc.rename_category(category_id, new_name=new_name)
        session.commit()
    except CategoryError as exc:
        session.rollback()
        return _panel_response(request, session, error=str(exc), status=422)
    return _panel_response(request, session)


@router.get("/categories/{category_id}/merge", response_class=HTMLResponse)
def merge_form(
    request: Request,
    category_id: int,
    session: Annotated[Session, Depends(get_session)],
    _user: RequireRead,
) -> HTMLResponse:
    """Render the merge form partial with a list of candidate target categories."""
    svc = CategoryService(session)
    category = session.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    candidates = svc.candidate_pairs(exclude_id=category_id)
    return templates.TemplateResponse(
        request,
        "partials/_category_form.html",
        {"mode": "merge", "category": category, "candidates": candidates},
    )


@router.post("/categories/{category_id}/merge", response_class=HTMLResponse)
def merge_category(
    request: Request,
    category_id: int,
    session: Annotated[Session, Depends(get_session)],
    _editor: RequireEditor,
    target_id: Annotated[int, Form()],
) -> HTMLResponse:
    """Merge a category into the target; return the refreshed panel."""
    svc = CategoryService(session)
    try:
        svc.merge_categories(source_id=category_id, target_id=target_id)
        session.commit()
    except CategoryError as exc:
        session.rollback()
        return _panel_response(request, session, error=str(exc), status=422)
    return _panel_response(request, session)


@router.get("/categories/{category_id}/delete", response_class=HTMLResponse)
def delete_form(
    request: Request,
    category_id: int,
    session: Annotated[Session, Depends(get_session)],
    _user: RequireRead,
) -> HTMLResponse:
    """Render the delete form partial with optional reassignment target candidates."""
    svc = CategoryService(session)
    category = session.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    candidates = svc.candidate_pairs(exclude_id=category_id, include_system=True)
    return templates.TemplateResponse(
        request,
        "partials/_category_form.html",
        {"mode": "delete", "category": category, "candidates": candidates},
    )


@router.post("/categories/{category_id}/delete", response_class=HTMLResponse)
def delete_category(
    request: Request,
    category_id: int,
    session: Annotated[Session, Depends(get_session)],
    _editor: RequireEditor,
    reassign_to_id: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Delete a category (reassigning dependents when a target is given); return the panel."""
    svc = CategoryService(session)
    try:
        svc.delete_category(category_id, reassign_to_id=_optional_id(reassign_to_id))
        session.commit()
    except CategoryError as exc:
        session.rollback()
        return _panel_response(request, session, error=str(exc), status=422)
    return _panel_response(request, session)
