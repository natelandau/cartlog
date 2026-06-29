"""Browser end-to-end tests for the store-comparison insights page.

Guards the form fixes against regression: the toolbar must reload without a 4xx (the empty
date inputs once triggered a 422), the store selects must not overflow into each other, and
the product multi-select must add and remove removable pills.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page, Response

pytestmark = pytest.mark.e2e


def test_store_comparison_form_reloads_and_pills_work(live_server: str, page: Page) -> None:
    """Verify the toolbar reloads without a 4xx and product pills can be added and removed."""
    # Given the store-comparison page with its fragment responses tracked
    statuses: list[tuple[int, str]] = []

    def _track(response: Response) -> None:
        if "/insights/store-comparison" in response.url:
            statuses.append((response.status, response.url))

    page.on("response", _track)
    page.goto(f"{live_server}/insights/store-comparison", wait_until="networkidle")

    # Then the two store selects render side by side without overlapping
    box_a = page.locator("select[name=store_a]").bounding_box()
    box_b = page.locator("select[name=store_b]").bounding_box()
    assert box_a is not None
    assert box_b is not None
    assert box_a["x"] + box_a["width"] <= box_b["x"] + 1

    # And the explicit submit button is present
    assert page.get_by_role("button", name="Update comparison").count() == 1

    # When a product is selected from the multi-select
    page.get_by_role("button", name="Select Products").click()
    checkbox = page.locator("input[name=product]").first
    value = checkbox.get_attribute("value")
    assert value is not None
    checkbox.check()

    # Then a removable pill appears for it (the form auto-reloaded with no 4xx)
    page.locator("span.badge", has_text=value).first.wait_for(timeout=5000)

    # The check triggers an htmx reload that swaps the whole panel, including the form itself. htmx
    # re-wires the new form's change trigger during settle, so wait for the round-trip to settle
    # before clicking the fresh remove control. Otherwise the remove click unchecks the box and
    # fires change into a not-yet-wired form, htmx sends no request, and the pill never detaches.
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(250)

    # And clicking its remove control takes it away
    page.locator(f'label[aria-label="Remove {value}"]').first.click()
    page.locator("span.badge", has_text=value).wait_for(state="detached", timeout=5000)

    # And every store-comparison request stayed below 400 (no empty-date 422)
    assert statuses
    bad = [(status, url) for status, url in statuses if status >= 400]
    assert not bad, f"unexpected non-2xx responses: {bad}"
