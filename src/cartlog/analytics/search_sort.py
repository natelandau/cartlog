"""Sortable columns for the line-item search results table.

Lives in the analytics package, next to the search query that consumes the column map, so
the web router depends on analytics (not the reverse).
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import func

from cartlog.db.models import Category, LineItem, Product, Receipt, Store


class SearchSortKey(StrEnum):
    """Columns the search results table can be ordered by."""

    DESCRIPTION = "description"
    PRODUCT = "product"
    CATEGORY = "category"
    STORE = "store"
    DATE = "date"
    UNIT_PRICE = "unit_price"
    LINE_TOTAL = "line_total"


# Text columns sort case-insensitively (func.lower) to match the receipt-list convention.
SEARCH_SORT_COLUMNS = {
    SearchSortKey.DESCRIPTION: func.lower(LineItem.raw_description),
    SearchSortKey.PRODUCT: func.lower(Product.canonical_name),
    SearchSortKey.CATEGORY: func.lower(Category.name),
    SearchSortKey.STORE: func.lower(Store.chain_name),
    SearchSortKey.DATE: Receipt.purchase_date,
    SearchSortKey.UNIT_PRICE: LineItem.unit_price,
    SearchSortKey.LINE_TOTAL: LineItem.line_total,
}
