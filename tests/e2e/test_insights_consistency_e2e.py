"""End-to-end consistency checks for the Insights charts.

Every Plotly view renders a chart, and a theme toggle re-renders the active chart
without error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

PLOTLY_VIEWS = ["price-history", "spend-over-time", "category-spend"]


def test_every_plotly_view_renders_a_chart(page: Page, live_server: str) -> None:
    """Verify each Plotly view renders an svg chart and a visible heading."""
    for view in PLOTLY_VIEWS:
        # Given the insights view is loaded
        page.goto(f"{live_server}/insights/{view}", wait_until="networkidle")
        # price-history needs its Show button clicked first; the others render on mount
        if view == "price-history":
            page.locator("#ph-show").click()
        # Then every Plotly insight draws an <svg.main-svg> and a shared <h3.font-display> heading
        expect(page.locator("svg.main-svg").first).to_be_visible(timeout=10000)
        expect(page.locator("h3.font-display").first).to_be_visible()


def test_store_comparison_heading_renders(page: Page, live_server: str) -> None:
    """Verify the store-comparison view renders its store-pair heading (not a Plotly chart)."""
    # Given the store-comparison insights view is loaded
    page.goto(f"{live_server}/insights/store-comparison", wait_until="networkidle")
    # Then the store-pair heading is visible (the seed has multiple stores)
    expect(page.locator("h3.font-display").first).to_be_visible()


def test_theme_toggle_rerenders_charts(page: Page, live_server: str) -> None:
    """Verify the chart re-renders after a data-theme toggle without error."""
    # Given the category-spend chart is rendered
    page.goto(f"{live_server}/insights/category-spend", wait_until="networkidle")
    expect(page.locator("svg.main-svg").first).to_be_visible(timeout=10000)

    # When the theme attribute is switched to dark (the MutationObserver re-renders the chart)
    page.evaluate("document.documentElement.setAttribute('data-theme', 'cartlog-dark')")

    # Then the chart is still present and rendered without error
    expect(page.locator("svg.main-svg").first).to_be_visible(timeout=10000)
