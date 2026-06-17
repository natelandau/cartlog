"""Shared assertion helpers for the web tests.

Small parsers that read values out of rendered HTML in document order, plus lookups for a
seeded row's id, used across the sorting, search, and delete tests.
"""

from __future__ import annotations

import re

from cartlog.db.models import LineItem, Product, Receipt


def totals_in_order(html: str) -> list[float]:
    """Extract the rendered 'USD <n>' total cells in document order."""
    return [float(x) for x in re.findall(r"<td>USD ([0-9.]+)</td>", html)]


def statuses_in_order(html: str) -> list[str]:
    """Extract the rendered status cells (plain or needs-review span) in document order."""
    return re.findall(r"<td>(?:<span[^>]*>)?(pending|parsing|parsed|needs_review|failed)", html)


def unit_prices_in_order(html: str) -> list[float]:
    """Extract the unit-price cell from each row in document order.

    Each row renders two class='num' cells (unit_price then line_total); capture only the
    first of the pair so the assertion reflects the unit-price ordering, not an interleave.
    """
    pattern = r'<td class="num">([0-9.]+)</td>\s*<td class="num">[0-9.]+</td>'
    return [float(x) for x in re.findall(pattern, html)]


def first_receipt_id(app_client) -> int:
    """Return the id of any one seeded receipt."""
    with app_client.app.state.session_factory() as session:
        receipt = session.query(Receipt).first()
        assert receipt is not None
        return receipt.id


def first_line_item_id(app_client) -> int:
    """Return the line_item_id of the first 'eggs' search result."""
    with app_client.app.state.session_factory() as session:
        line = (
            session.query(LineItem)
            .join(Product, LineItem.product_id == Product.id)
            .filter(Product.canonical_name == "eggs")
            .first()
        )
        assert line is not None
        return line.id
