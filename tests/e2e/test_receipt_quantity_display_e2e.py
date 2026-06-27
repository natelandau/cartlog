"""End-to-end coverage for the receipt detail Qty column trimming trailing zeros.

A size-less count line (e.g. the seeded "BANANAS", quantity 2) has no per-item measure, so
the Qty cell falls back to the plain quantity. The Numeric(10,3) column stores it as 2.000;
the page must render "2". Anonymous read is on in the harness, so no login is needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e


def _find_receipt_with_bananas(page: Page, base_url: str) -> None:
    """Navigate to the receipt detail page that contains the seeded BANANAS line."""
    page.goto(f"{base_url}/receipts")
    page.wait_for_load_state("networkidle")
    hrefs = page.locator("a[href^='/receipts/']").evaluate_all(
        "els => els.map(e => e.getAttribute('href'))"
    )
    for href in dict.fromkeys(h for h in hrefs if h and h.split("/")[-1].isdigit()):
        page.goto(f"{base_url}{href}")
        page.wait_for_load_state("networkidle")
        if page.get_by_text("BANANAS", exact=False).count():
            return
    pytest.fail("No receipt detail page contained the seeded BANANAS line")


def test_receipt_detail_trims_whole_quantity(page: Page, live_server: str) -> None:
    """Verify a whole count quantity renders as "2", not the stored "2.000"."""
    errors: list[tuple[int, str]] = []
    # Seeded test receipts have no image file on disk, so /image always 404s; not a bug.
    page.on(
        "response",
        lambda r: (
            errors.append((r.status, r.url)) if r.status >= 400 and "/image" not in r.url else None
        ),
    )

    # Given the receipt detail page that lists the size-less BANANAS count line
    _find_receipt_with_bananas(page, live_server)

    # Then that row's Qty cell shows the trimmed integer, never the stored fixed-decimal
    qty_cell = page.locator("tr", has=page.get_by_text("BANANAS", exact=False)).locator(
        "td[data-label='Qty']"
    )
    expect(qty_cell).to_have_text("2")

    assert not errors, f"Unexpected 4xx during receipt detail render: {errors}"
