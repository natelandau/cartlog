"""Persist parsed receipts, deduplicating stores, products, and categories."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from cartlog.categories.service import CategoryService
from cartlog.db.models import LineItem, Receipt

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cartlog.db.base import Base
    from cartlog.parsing.schema import ParsedReceipt


def _money(value: float) -> Decimal:
    # Convert via str() so 3.48 -> Decimal("3.48") exactly, avoiding binary float drift.
    return Decimal(str(value))


def _get_or_create[ModelT: Base](
    session: Session,
    model: type[ModelT],
    *,
    defaults: dict[str, Any] | None = None,
    **filters: Any,  # noqa: ANN401  # filters are arbitrary column=value lookups
) -> ModelT:
    """Return the row matching `filters`, creating it (with `defaults`) if absent.

    `filters` are the columns that identify the row (and dedupe on it); `defaults` are
    extra construction values applied only when a new row is created.
    """
    instance = session.query(model).filter_by(**filters).one_or_none()
    if instance is None:
        instance = model(**filters, **(defaults or {}))
        session.add(instance)
    return instance


def persist_receipt(
    session: Session,
    parsed: ParsedReceipt,
    *,
    image_path: str,
    source: str,
    status: str,
    raw_json: str,
) -> tuple[Receipt, list[str]]:
    """Persist a ParsedReceipt and its line items, deduplicating stores and products.

    Categories are resolved against the existing taxonomy via CategoryService; no new
    Category rows are ever created. Unknown categories route to the reserved Uncategorized
    category and are collected in the returned unmapped list.

    The caller owns the transaction (commit/rollback); this function only adds and flushes.

    Args:
        session: The SQLAlchemy session to use for persistence.
        parsed: The structured receipt data returned by a parser.
        image_path: Filesystem path to the original receipt image.
        source: How the receipt was submitted (e.g. 'cli', 'api').
        status: Processing status (a ReceiptStatus value, e.g. 'parsed', 'needs_review').
        raw_json: Verbatim JSON string from the parser, retained for audit and re-processing.

    Returns:
        (receipt, unmapped) where unmapped is the de-duplicated list of category strings
        that did not resolve and were routed to Uncategorized.
    """
    # Local import breaks the stores.service <-> persistence import cycle (the service reuses
    # _get_or_create from this module). Routing through resolve_store makes every ingested
    # receipt honor saved store-merge rules.
    from cartlog.stores.service import resolve_store  # noqa: PLC0415

    store = resolve_store(session, parsed.store_name, parsed.store_location)

    receipt = Receipt(
        store=store,
        purchase_date=parsed.purchase_date,
        total=_money(parsed.total),
        currency=parsed.currency,
        image_path=image_path,
        raw_parser_json=raw_json,
        source=source,
        status=status,
    )
    # Add to session before querying so autoflush during get-or-create lookups sees it as tracked.
    session.add(receipt)

    categories = CategoryService(session)
    unmapped: list[str] = []
    for item in parsed.line_items:
        category, matched = categories.resolve(item.category)
        if not matched and item.category.strip() and item.category not in unmapped:
            unmapped.append(item.category)
        # Local import breaks the products.service <-> persistence import cycle (the service
        # reuses _get_or_create from this module). Routing through resolve_product makes every
        # ingested item honor saved merge rules.
        from cartlog.products.service import resolve_product  # noqa: PLC0415

        product = resolve_product(session, item.canonical_name, defaults={"category": category})
        receipt.line_items.append(
            LineItem(
                product=product,
                raw_description=item.raw_description,
                original_category=item.category,
                quantity=_money(item.quantity),
                unit=item.unit,
                unit_size=item.unit_size,
                unit_price=_money(item.unit_price),
                line_total=_money(item.line_total),
            )
        )

    session.flush()
    return receipt, unmapped
