"""Browser end-to-end test for the spend-over-time insights page.

Drives the real user flow: land on Insights, pick Spend over time from the analysis dropdown
(an htmx.ajax swap), then change a measure (a toolbar form swap), confirming the Plotly chart
renders both times. A regression guard for the swap-event wiring, since both swaps settle on the
panel container rather than the inserted fragment node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

# True once the toolbar shows the given measure AND the chart container holds a rendered Plotly
# node. Gating on both avoids matching the previous view's stale chart before the swap completes.
_RENDERED = """
(measure) => {
  const select = document.querySelector('select[name=series]');
  const chart = document.querySelector('#spend-over-time-chart');
  return !!select && select.value === measure && !!chart && chart.childElementCount > 0;
}
"""


def test_spend_over_time_renders_via_dropdown_and_toggle(live_server: str, page: Page) -> None:
    """Verify the chart renders via the dropdown (with the right URL) and re-renders on a toggle."""
    # Given the Insights page landed on its default analysis, with responses and JS errors tracked
    statuses: list[tuple[int, str]] = []
    js_errors: list[str] = []
    page.on(
        "response",
        lambda r: (
            statuses.append((r.status, r.url)) if "/insights/spend-over-time" in r.url else None
        ),
    )
    page.on("pageerror", lambda e: js_errors.append(str(e)))
    page.goto(f"{live_server}/insights", wait_until="networkidle")

    # When choosing Spend over time from the analysis dropdown (fires an htmx.ajax swap)
    page.select_option("#insight-select", "spend-over-time")

    # Then the Plotly chart actually renders into the panel (default measure is total spend)
    page.wait_for_function(_RENDERED, arg="total", timeout=15000)

    # And the dropdown pushed the canonical deep-link URL, not the literal "/insights/true"
    assert page.url.endswith("/insights/spend-over-time")

    # When switching the measure to the stacked by-category view (a toolbar form swap)
    page.select_option("select[name=series]", "category")

    # Then the chart re-renders for the new measure
    page.wait_for_function(_RENDERED, arg="category", timeout=15000)

    # And every spend-over-time request stayed below 400, with no uncaught JS errors anywhere
    assert statuses
    bad = [(status, url) for status, url in statuses if status >= 400]
    assert not bad, f"unexpected non-2xx responses: {bad}"
    assert not js_errors, f"unexpected JS errors: {js_errors}"
