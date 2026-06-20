"""Receipt-facing web routes: upload, list/detail, inline edit."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import ValidationError

# Runtime imports: joinedload is called at runtime, and FastAPI resolves
# Annotated[Session, Depends(...)] in this module's namespace.
from sqlalchemy.orm import Session, contains_eager, joinedload, selectinload

from cartlog.categories.service import CategoryService
from cartlog.config import Settings, get_settings
from cartlog.constants import SUPPORTED_SUFFIXES
from cartlog.db.models import LineItem, Product, Receipt, ReceiptStatus
from cartlog.db.sort import SortDir
from cartlog.ingest.queue import enqueue_job
from cartlog.receipts.service import (
    ReparseImageMissingError,
    apply_receipt_edit,
    delete_receipt,
    image_file_available,
    reparse_receipt,
)
from cartlog.web.auth import (  # noqa: TC001  # runtime imports: FastAPI Depends resolves Annotated aliases at startup
    RequireEditor,
    RequireRead,
)
from cartlog.web.dependencies import get_session
from cartlog.web.forms import parse_review_form
from cartlog.web.htmx import wants_partial
from cartlog.web.sort import SORT_COLUMNS, ReceiptSortKey
from cartlog.web.templating import templates
from cartlog.web.units_display import read_unit_system

router = APIRouter()


def _load_receipt(session: Session, receipt_id: int, *, with_category: bool = False) -> Receipt:
    """Load a receipt with the relations its templates iterate, or raise 404.

    Centralizes the eager-load set so every route that renders a receipt stays N+1-safe.
    `with_category` adds the product->category join the read and edit panels render; callers
    that do not display categories can omit it to skip the extra join.
    """
    line_loader = selectinload(Receipt.line_items).joinedload(LineItem.product)
    if with_category:
        line_loader = line_loader.joinedload(Product.category)
    receipt = (
        session.query(Receipt)
        .options(joinedload(Receipt.store), line_loader, selectinload(Receipt.review_reasons))
        .filter(Receipt.id == receipt_id)
        .one_or_none()
    )
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return receipt


def _edit_context(session: Session, receipt: Receipt, *, errors: str | None) -> dict[str, object]:
    """Build the template context for the editable items partial, including picker options."""
    product_names = [
        name for (name,) in session.query(Product.canonical_name).order_by(Product.canonical_name)
    ]
    category_options = CategoryService(session).candidate_pairs(include_system=True)
    return {
        "receipt": receipt,
        "errors": errors,
        "product_names": product_names,
        "category_options": category_options,
        "review_status": ReceiptStatus.NEEDS_REVIEW,
    }


def _items_panel_context(
    receipt: Receipt, settings: Settings, request: Request
) -> dict[str, object]:
    """Build the read-only items panel context, including whether reparse can be offered."""
    return {
        "receipt": receipt,
        "review_status": ReceiptStatus.NEEDS_REVIEW,
        "image_available": image_file_available(
            receipt.image_path, storage_dir=settings.image_storage_dir
        ),
        "unit_system": read_unit_system(request),
    }


def _table_template(request: Request, full_page: str) -> str:
    """Return the table fragment for htmx requests and the full page otherwise."""
    if wants_partial(request):
        return "partials/_receipt_table.html"
    return full_page


async def _store_and_enqueue(
    file: UploadFile,
    *,
    session: Session,
    settings: Settings,
    source: str = "web",
    user_id: int | None = None,
) -> dict[str, object]:
    """Validate, store, and enqueue one uploaded receipt file, returning its accepted record.

    Raise ValueError with a human-readable reason when the file's type is unsupported or its
    size exceeds the cap, so the batch route can report it per-file instead of failing wholesale.

    Args:
        file: The uploaded receipt file.
        session: SQLAlchemy session; enqueue commits on success.
        settings: Runtime settings supplying the size cap and storage directory.
        source: Ingestion source label stored on the job (e.g. 'web', 'shortcut').
        user_id: The id of the authenticated user submitting the file, for uploader attribution.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        msg = (
            f"Unsupported file type: {suffix or '(none)'}. "
            f"Allowed: {', '.join(sorted(SUPPORTED_SUFFIXES))}."
        )
        raise ValueError(msg)

    max_bytes = settings.max_upload_bytes
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        msg = "Upload exceeds the maximum allowed size."
        raise ValueError(msg)

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(data)
        job = enqueue_job(
            session,
            src_path=tmp_path,
            source=source,
            storage_dir=settings.image_storage_dir,
            user_id=user_id,
        )
        return {"filename": file.filename, "job_id": job.id, "status": job.status}
    finally:
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)  # noqa: PTH108


@router.post("/receipts")
async def upload_receipts(
    files: Annotated[list[UploadFile], File()],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
    editor: RequireEditor,
    # Cap matches the IngestionJob.source column width; SQLite does not enforce String(50),
    # so without this an over-long label would be stored verbatim instead of rejected.
    source: Annotated[str, Form(max_length=50)] = "web",
) -> dict[str, object]:
    """Accept one or more receipt uploads, enqueuing the valid ones and reporting the rest.

    Returns {accepted, rejected}; the status is 202 when at least one file was enqueued and
    400 when every file was rejected (or none were sent), so a single bad file in a batch does
    not block the rest. The optional `source` field labels jobs by channel (e.g. an Apple
    Shortcut sends 'shortcut'); it defaults to 'web' for the browser uploader.
    """
    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, str]] = []
    for file in files:
        try:
            accepted.append(
                await _store_and_enqueue(
                    file,
                    session=session,
                    settings=settings,
                    source=source,
                    # Record which user uploaded this receipt for attribution on the Receipt row.
                    user_id=editor.id,
                )
            )
        except ValueError as exc:
            rejected.append({"filename": file.filename or "(unnamed)", "reason": str(exc)})
    response.status_code = 202 if accepted else 400
    return {"accepted": accepted, "rejected": rejected}


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, _editor: RequireEditor) -> HTMLResponse:
    """Render the receipt upload form, the entry point for adding receipts via the web UI."""
    return templates.TemplateResponse(request, "upload.html", {})


@router.get("/receipts", response_class=HTMLResponse)
def receipt_list(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _user: RequireRead,
    status: ReceiptStatus | None = None,
    sort: ReceiptSortKey = ReceiptSortKey.DATE,
    direction: SortDir = SortDir.DESC,
) -> HTMLResponse:
    """Render the receipt list, optionally filtered by status and sorted by any column.

    Typing `status`/`sort`/`direction` as enums makes FastAPI reject an unknown value with a
    422 rather than silently rendering an empty or wrongly ordered list.
    """
    query = (
        session.query(Receipt)
        .join(Receipt.store)
        .options(contains_eager(Receipt.store))  # one join serves rendering and store sort
    )
    if status is not None:
        query = query.filter(Receipt.status == status)
    column = SORT_COLUMNS[sort]
    ordered = column.asc() if direction == SortDir.ASC else column.desc()
    # id desc is a stable tiebreaker so equal keys keep a deterministic order.
    receipts = query.order_by(ordered, Receipt.id.desc()).all()
    return templates.TemplateResponse(
        request,
        _table_template(request, "receipt_list.html"),
        {
            "receipts": receipts,
            "status": status,
            "sort": sort,
            "direction": direction,
            "review_status": ReceiptStatus.NEEDS_REVIEW,
            "endpoint": "/receipts",
            "table_id": "receipt-table",
            "show_review": True,
        },
    )


@router.get("/receipts/{receipt_id}", response_class=HTMLResponse)
def receipt_detail(
    receipt_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    _user: RequireRead,
) -> HTMLResponse:
    """Render a single receipt with its line items beside the source image or PDF."""
    receipt = _load_receipt(session, receipt_id, with_category=True)
    # PDFs cannot render in <img>; the template uses an <object> viewer for them instead.
    is_pdf = Path(receipt.image_path).suffix.lower() == ".pdf"
    return templates.TemplateResponse(
        request,
        "receipt_detail.html",
        {**_items_panel_context(receipt, settings, request), "is_pdf": is_pdf},
    )


@router.get("/receipts/{receipt_id}/image")
def receipt_image(
    receipt_id: int,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    _user: RequireRead,
) -> FileResponse:
    """Stream a receipt's stored image, refusing any path outside the storage dir."""
    receipt = session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    if not image_file_available(receipt.image_path, storage_dir=settings.image_storage_dir):
        raise HTTPException(status_code=404, detail="Image not available")
    return FileResponse(Path(receipt.image_path).resolve())


@router.get("/receipts/{receipt_id}/items", response_class=HTMLResponse)
def receipt_items(
    receipt_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    _user: RequireRead,
) -> HTMLResponse:
    """Render the read-only items panel fragment (used by Cancel to restore the view)."""
    receipt = _load_receipt(session, receipt_id, with_category=True)
    return templates.TemplateResponse(
        request, "partials/_receipt_items.html", _items_panel_context(receipt, settings, request)
    )


@router.get("/receipts/{receipt_id}/edit", response_class=HTMLResponse)
def edit_items(
    receipt_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _editor: RequireEditor,
) -> HTMLResponse:
    """Render the editable items panel fragment for any receipt, regardless of status."""
    receipt = _load_receipt(session, receipt_id, with_category=True)
    return templates.TemplateResponse(
        request, "partials/_receipt_items_edit.html", _edit_context(session, receipt, errors=None)
    )


@router.get("/receipts/{receipt_id}/review")
def review_redirect(receipt_id: int, _user: RequireRead) -> RedirectResponse:
    """Redirect the retired review URL to the unified detail page."""
    return RedirectResponse(url=f"/receipts/{receipt_id}", status_code=307)


@router.post("/receipts/{receipt_id}", response_class=HTMLResponse)
async def review_save(
    receipt_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    _editor: RequireEditor,
) -> HTMLResponse:
    """Apply the full edited line set + header in one transaction, then return the read panel.

    Saving corrects the receipt's data but deliberately leaves `status` untouched: the
    operator confirms a receipt separately via the mark-reviewed action.
    """
    receipt = _load_receipt(session, receipt_id, with_category=True)

    raw = await request.form()
    # Coerce every field to str: the edit form posts only text inputs, but getlist may also
    # yield uploaded-file objects, which the parser does not accept.
    form: dict[str, list[str]] = {key: [str(value) for value in raw.getlist(key)] for key in raw}
    try:
        edit = parse_review_form(form)
        apply_receipt_edit(session, receipt, edit)
    except (ValueError, ValidationError) as exc:
        # apply_receipt_edit may have left pending mutations when it raised (e.g. a bad
        # category_id), so roll back before re-rendering the form.
        session.rollback()
        # 422 re-render of the edit form; base.html opts these into an htmx swap.
        return templates.TemplateResponse(
            request,
            "partials/_receipt_items_edit.html",
            _edit_context(session, receipt, errors=str(exc)),
            status_code=422,
        )

    # Reload with the category join so the read partial renders category names post-commit.
    receipt = _load_receipt(session, receipt_id, with_category=True)
    return templates.TemplateResponse(
        request, "partials/_receipt_items.html", _items_panel_context(receipt, settings, request)
    )


@router.delete("/receipts/{receipt_id}")
def delete_receipt_route(
    receipt_id: int,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    _editor: RequireEditor,
) -> Response:
    """Delete a receipt and its entries, then send htmx to the receipt list."""
    deleted = delete_receipt(session, receipt_id, storage_dir=settings.image_storage_dir)
    if not deleted:
        raise HTTPException(status_code=404, detail="Receipt not found")
    # htmx swaps on a 2xx body; HX-Redirect instead navigates the whole page to the list.
    return Response(status_code=200, headers={"HX-Redirect": "/receipts"})


@router.post("/receipts/{receipt_id}/reparse")
def reparse_receipt_route(
    receipt_id: int,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    _editor: RequireEditor,
) -> Response:
    """Discard a receipt's parsed data, requeue its image, and send htmx to the list."""
    try:
        job = reparse_receipt(session, receipt_id, storage_dir=settings.image_storage_dir)
    except ReparseImageMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    # htmx swaps on a 2xx body; HX-Redirect instead navigates the whole page to the list.
    return Response(status_code=200, headers={"HX-Redirect": "/receipts"})


@router.post("/receipts/{receipt_id}/mark-reviewed", response_class=HTMLResponse)
def mark_reviewed(
    receipt_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    _editor: RequireEditor,
) -> HTMLResponse:
    """Flip a needs_review receipt to parsed without editing any fields."""
    receipt = _load_receipt(session, receipt_id, with_category=True)
    receipt.status = ReceiptStatus.PARSED
    receipt.review_reasons.clear()
    session.commit()
    return templates.TemplateResponse(
        request, "partials/_receipt_items.html", _items_panel_context(receipt, settings, request)
    )
