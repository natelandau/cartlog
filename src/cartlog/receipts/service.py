"""Mutate receipts from the web edit form and delete receipts, cleaning up unshared images."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from cartlog.db.models import Category, IngestionJob, JobStatus, LineItem, Receipt, Store
from cartlog.ingest.persistence import _get_or_create
from cartlog.products.service import resolve_product

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cartlog.web.forms import ReceiptEdit

logger = logging.getLogger(__name__)


class ReparseImageMissingError(Exception):
    """Raised when a receipt's stored image is missing, so it cannot be reparsed."""


def image_file_available(image_path: str, *, storage_dir: Path) -> bool:
    """Report whether a receipt's stored image exists and lives inside the storage dir.

    Use this before reparsing or when deciding whether to offer reparse: a missing or
    out-of-storage path means the source image is gone and cannot be parsed again.

    Args:
        image_path: The receipt's recorded image path.
        storage_dir: Directory stored image files live under; paths outside it are rejected.

    Returns:
        True only when the path resolves inside storage_dir and points at an existing file.
    """
    resolved = Path(image_path).resolve()
    storage_root = storage_dir.resolve()
    return resolved.is_relative_to(storage_root) and resolved.is_file()


def reparse_receipt(session: Session, receipt_id: int, *, storage_dir: Path) -> IngestionJob | None:
    """Discard a receipt's parsed records and queue its stored image for a fresh parse.

    Use this to re-run the ingestion pipeline on an existing receipt, e.g. to pick up
    model or prompt changes or to retry a bad parse. A new pending job is created for the
    receipt's existing stored image first, so when the old receipt is deleted the shared
    image file is still referenced and left on disk; the worker pool then parses the new
    job like any upload. The new job preserves the original receipt's source.

    Args:
        session: SQLAlchemy session; this function commits.
        receipt_id: Id of the receipt to reparse.
        storage_dir: Directory stored image files live under.

    Returns:
        The new pending IngestionJob, or None if no receipt has that id.

    Raises:
        ReparseImageMissingError: If the receipt's image file is missing or outside storage_dir.
    """
    receipt = session.get(Receipt, receipt_id)
    if receipt is None:
        return None

    image_path = receipt.image_path
    source = receipt.source
    if not image_file_available(image_path, storage_dir=storage_dir):
        msg = f"Image file for receipt {receipt_id} is missing; cannot reparse."
        raise ReparseImageMissingError(msg)

    # Create the new job pointing at the SAME stored file before deleting the receipt, so
    # delete_receipt's reference check keeps the image on disk. Point the job straight at
    # the existing path rather than calling enqueue_job, which would re-hash and re-copy
    # the already-stored file under a new name.
    job = IngestionJob(source=source, image_path=image_path, status=JobStatus.PENDING)
    session.add(job)
    session.commit()

    delete_receipt(session, receipt_id, storage_dir=storage_dir)
    return job


def apply_receipt_edit(session: Session, receipt: Receipt, edit: ReceiptEdit) -> None:
    """Apply an edited header and its full line set to `receipt`, committing once.

    Use this to save any receipt's corrections from the web edit form. The posted lines are
    the complete desired set: a line with a matching id is updated, a line with no id is
    created, and any current line absent from the post is deleted. Each line's product is
    get-or-created from its canonical name, so renaming repoints the line and adopts that
    product's category; a supplied category is written back to the shared product, which by
    design recategorizes every receipt that uses it.

    Args:
        session: SQLAlchemy session; this function commits on success.
        receipt: The receipt to update; its line_items relationship is mutated in place.
        edit: The validated header + lines parsed from the edit form.
    """
    store = _get_or_create(session, Store, chain_name=edit.chain_name, location=edit.location)
    receipt.store = store
    receipt.purchase_date = edit.purchase_date
    receipt.total = edit.total
    receipt.currency = edit.currency

    # Snapshot before the loop so newly created lines are not candidates for the delete pass.
    existing: dict[int, LineItem] = {li.id: li for li in receipt.line_items}
    seen: set[int] = set()
    for line in edit.lines:
        # Skip an id that does not belong to this receipt BEFORE resolving its product, so a
        # tampered/phantom row never get-or-creates an orphan product.
        matched: LineItem | None = None
        if line.line_id is not None:
            matched = existing.get(line.line_id)
            if matched is None:
                continue

        # Resolve the product (and optional category write-back) BEFORE attaching a new line, so
        # the line is never added to the session half-built (no NOT NULL flush failure) and
        # autoflush stays enabled, deduping a product or category that two sibling lines in the
        # same edit both introduce under a brand-new name (otherwise the duplicate rows would
        # violate the unique constraint at commit).
        product = resolve_product(session, line.canonical_name)
        if line.category_id is not None:
            category = session.get(Category, line.category_id)
            if category is None:
                msg = f"Category id {line.category_id} does not exist"
                raise ValueError(msg)
            # Write-back to the shared product recategorizes every receipt using it (by design).
            product.category = category

        if matched is None:
            item = LineItem(product=product)
            session.add(item)
            receipt.line_items.append(item)
        else:
            seen.add(matched.id)
            item = matched
            item.product = product

        item.raw_description = line.raw_description
        item.quantity = line.quantity
        item.unit = line.unit
        item.unit_size = line.unit_size
        item.unit_price = line.unit_price
        item.line_total = line.line_total

    # Lines the operator dropped from the form are deleted (cascades via the relationship).
    for line_id, item in existing.items():
        if line_id not in seen:
            receipt.line_items.remove(item)
            session.delete(item)

    session.commit()


def apply_line_item_edit(
    session: Session,
    line_item: LineItem,
    *,
    canonical_name: str,
    category_id: int | None,
) -> None:
    """Normalize one line: reassign its product and optionally recategorize that product.

    Use this from the search view to fix a single line in isolation. The line is repointed to
    the product named `canonical_name` (get-or-created, so a new name creates a product), which
    affects only this line. A supplied `category_id` is written back to that shared product,
    recategorizing every line that resolves to it. Commits on success.

    Args:
        session: SQLAlchemy session; this function commits on success.
        line_item: The line to reassign; mutated in place.
        canonical_name: The product name to point the line at, created if it does not exist.
        category_id: Taxonomy category to write back to the product, or None to leave it.

    Raises:
        ValueError: If `category_id` is given but no such category exists.
    """
    product = resolve_product(session, canonical_name)
    if category_id is not None:
        category = session.get(Category, category_id)
        if category is None:
            msg = f"Category id {category_id} does not exist"
            raise ValueError(msg)
        # Write-back to the shared product recategorizes every line using it (by design).
        product.category = category
    line_item.product = product
    session.commit()


def delete_receipt(session: Session, receipt_id: int, *, storage_dir: Path) -> bool:
    """Delete a receipt, its line items, its ingestion job, and its image file if unshared.

    Line items cascade via the ``Receipt.line_items`` relationship. The shared, normalized
    Store and Product rows are deliberately left intact. The stored image file is removed
    only when no other receipt or ingestion job still references it, because duplicate
    uploads share one content-hashed file on disk.

    Args:
        session: SQLAlchemy session; this function commits on success.
        receipt_id: Id of the receipt to delete.
        storage_dir: Directory stored image files live under; file removal is confined to it.

    Returns:
        True if a receipt was deleted, False if no receipt had that id.
    """
    receipt = session.get(Receipt, receipt_id)
    if receipt is None:
        return False

    image_path = receipt.image_path

    # Remove the job that produced this receipt first so its receipt_id FK never dangles.
    session.query(IngestionJob).filter(IngestionJob.receipt_id == receipt_id).delete()
    session.delete(receipt)  # line items cascade via Receipt.line_items
    session.commit()

    # Check references AFTER the commit so the just-deleted rows are excluded from the count.
    _delete_image_if_unreferenced(session, image_path, storage_dir=storage_dir)
    return True


def _delete_image_if_unreferenced(session: Session, image_path: str, *, storage_dir: Path) -> None:
    """Unlink the stored image file unless another receipt or job still references its path.

    Confined to ``storage_dir`` so a malformed or hand-edited image_path can never unlink a
    file outside the configured storage area.
    """
    still_referenced = (
        session.query(Receipt).filter(Receipt.image_path == image_path).first() is not None
        or session.query(IngestionJob).filter(IngestionJob.image_path == image_path).first()
        is not None
    )
    if still_referenced:
        return

    resolved = Path(image_path).resolve()
    storage_root = storage_dir.resolve()
    if resolved.is_relative_to(storage_root):
        # Best-effort: the receipt is already committed as deleted, so a failure to remove
        # the now-orphaned file must not propagate and report the whole deletion as failed.
        # An orphaned file is harmless; surface it as a warning rather than crashing.
        try:
            resolved.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove orphaned receipt image %s", resolved, exc_info=True)
