"""Sort enums and column maps for every sortable web table.

One module for the receipt list, the dashboard recent table, and the admin product/store/
transformation tables, so a router never reaches across into another router for a sort key.
`SortDir` and the asc/desc helper live in `cartlog.db.sort`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import func

from cartlog.db.models import Category, LineItem, Product, ProductMerge, Receipt, Store, StoreMerge

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any


# --- Receipts (list view + dashboard recent table) -------------------------------------------


class ReceiptSortKey(StrEnum):
    """Sortable columns shared by the receipt list and the dashboard recent table."""

    ID = "id"
    STORE = "store"
    DATE = "date"
    TOTAL = "total"
    STATUS = "status"


# DB columns for list-level (whole-query) ordering.
SORT_COLUMNS = {
    ReceiptSortKey.ID: Receipt.id,
    # Store sorts case-insensitively to match the dashboard's Python `.lower()` key, so the
    # DB-ordered receipt list and the Python-ordered dashboard table order names identically.
    ReceiptSortKey.STORE: func.lower(Store.chain_name),
    ReceiptSortKey.DATE: Receipt.purchase_date,
    ReceiptSortKey.TOTAL: Receipt.total,
    ReceiptSortKey.STATUS: Receipt.status,
}

# Python key functions for reordering an already-selected recent set.
SORT_KEYS: dict[ReceiptSortKey, Callable[[Receipt], Any]] = {
    ReceiptSortKey.ID: lambda r: r.id,
    ReceiptSortKey.STORE: lambda r: r.store.chain_name.lower(),
    ReceiptSortKey.DATE: lambda r: r.purchase_date,
    ReceiptSortKey.TOTAL: lambda r: r.total,
    ReceiptSortKey.STATUS: lambda r: r.status,
}


# --- Admin: products & transformations -------------------------------------------------------


class ProductSortKey(StrEnum):
    """Sortable columns for the admin products table."""

    NAME = "name"
    OCCURRENCES = "occurrences"
    CATEGORY = "category"


class TransformationSortKey(StrEnum):
    """Sortable columns for the admin transformations table."""

    SOURCE = "source"
    TARGET = "target"
    DATE = "date"


# Aggregate reused by both the SELECT and the ORDER BY of the products query.
OCCURRENCE_COUNT = func.count(LineItem.id)

PRODUCT_SORT_COLUMNS = {
    ProductSortKey.NAME: func.lower(Product.canonical_name),
    ProductSortKey.OCCURRENCES: OCCURRENCE_COUNT,
    ProductSortKey.CATEGORY: func.lower(Category.name),
}

TRANSFORMATION_SORT_COLUMNS = {
    TransformationSortKey.SOURCE: func.lower(ProductMerge.source_name),
    TransformationSortKey.TARGET: func.lower(Product.canonical_name),
    TransformationSortKey.DATE: ProductMerge.created_at,
}


# --- Admin: stores & store-merges ------------------------------------------------------------


class StoreSortKey(StrEnum):
    """Sortable columns for the admin stores table."""

    CHAIN = "chain"
    LOCATION = "location"
    VISITS = "visits"


class StoreMergeSortKey(StrEnum):
    """Sortable columns for the admin store-merges table."""

    SOURCE = "source"
    TARGET = "target"
    DATE = "date"


# Aggregate reused by both the SELECT and the ORDER BY of the stores query.
VISIT_COUNT = func.count(Receipt.id)

STORE_SORT_COLUMNS = {
    StoreSortKey.CHAIN: func.lower(Store.chain_name),
    StoreSortKey.LOCATION: func.lower(Store.location),
    StoreSortKey.VISITS: VISIT_COUNT,
}

STORE_MERGE_SORT_COLUMNS = {
    StoreMergeSortKey.SOURCE: func.lower(StoreMerge.source_chain_name),
    StoreMergeSortKey.TARGET: func.lower(Store.chain_name),
    StoreMergeSortKey.DATE: StoreMerge.created_at,
}
