"""Shared assertion helpers for the web tests.

Small parsers that read values out of rendered HTML in document order, plus lookups for a
seeded row's id, used across the sorting, search, and delete tests.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from cartlog.db.models import LineItem, Product, Receipt

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def get_session_factory(client: TestClient) -> Any:  # noqa: ANN401
    """Return the session_factory stored on the FastAPI app's state.

    TestClient.app is typed as an ASGI callable, not a FastAPI instance, so
    `.state` is invisible to ty. One suppression here avoids scattering it
    across every test that needs a direct DB session.
    """
    return client.app.state.session_factory  # ty: ignore[unresolved-attribute]


def read_json_script(html: str, element_id: str) -> dict:
    """Parse the embedded <script type="application/json" id="..."> chart payload from a fragment.

    The server-rendered insight fragments (spend-over-time, top-products) carry their chart data
    in a JSON script tag the inline renderer reads; tests assert against the same payload.
    """
    match = re.search(
        rf'<script type="application/json" id="{re.escape(element_id)}">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match is not None, f"no JSON script tag with id={element_id!r}"
    return json.loads(match.group(1))


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


def first_receipt_id(app_client: TestClient) -> int:
    """Return the id of any one seeded receipt."""
    with get_session_factory(app_client)() as session:
        receipt = session.query(Receipt).first()
        assert receipt is not None
        return receipt.id


def first_line_item_id(app_client: TestClient) -> int:
    """Return the line_item_id of the first 'eggs' search result."""
    with get_session_factory(app_client)() as session:
        line = (
            session.query(LineItem)
            .join(Product, LineItem.product_id == Product.id)
            .filter(Product.canonical_name == "eggs")
            .first()
        )
        assert line is not None
        return line.id
