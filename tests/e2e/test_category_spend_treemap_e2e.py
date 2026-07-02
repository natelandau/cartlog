"""Browser end-to-end tests for the Category spend drill-down treemap.

Drives the real Insights page: the "Category spend" analysis renders a Plotly treemap of
categories, a click drills into a category's products (and no deeper, since a product is a leaf), the
breadcrumb stays legible, and the toolbar filters re-render the map. The grouping logic itself is
unit-tested in Python (`tests/analytics/test_category_spend_tree.py`); these tests cover the
rendered behavior. The e2e seed has two categories (dairy: eggs, milk; produce: bananas, apples).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

# True once the treemap has drawn: the graph div carries Plotly's data array with a trace.
_TREEMAP_DRAWN = """
() => {
  const el = document.querySelector('#category-spend-chart');
  return !!el && Array.isArray(el.data) && el.data.length > 0;
}
"""


def _svg_text(page: Page) -> str:
    """Return the concatenated text of every SVG label currently drawn in the chart."""
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('#category-spend-chart text'))"
        ".map(t => t.textContent).join(' ')"
    )


def _load(page: Page, live_server: str) -> None:
    """Load the category-spend view and block until the treemap trace has drawn."""
    page.goto(f"{live_server}/insights/category-spend", wait_until="networkidle")
    expect(page.locator("svg.main-svg").first).to_be_visible(timeout=10000)
    page.wait_for_function(_TREEMAP_DRAWN, timeout=10000)


def _drill_into(page: Page, label: str) -> None:
    """Click a treemap tile by its label (the tile's surface path intercepts a plain click)."""
    # Align the tall chart's top to the viewport so every tile is on-screen and clickable.
    page.evaluate(
        "() => document.querySelector('#category-spend-chart')"
        ".scrollIntoView({block: 'start', behavior: 'instant'})"
    )
    page.locator("#category-spend-chart").get_by_text(label, exact=True).first.click(force=True)


def test_category_spend_draws_a_treemap(live_server: str, page: Page) -> None:
    """Verify the category-spend analysis renders a Plotly treemap with no errors."""
    # Given the view is loaded while tracking responses and JS errors
    statuses: list[tuple[int, str]] = []
    js_errors: list[str] = []
    page.on(
        "response",
        lambda r: statuses.append((r.status, r.url)) if "category-spend" in r.url else None,
    )
    page.on("pageerror", lambda e: js_errors.append(str(e)))
    _load(page, live_server)

    # Then the drawn trace is a treemap
    trace_type = page.evaluate("() => document.querySelector('#category-spend-chart').data[0].type")
    assert trace_type == "treemap", f"expected a treemap trace, got {trace_type!r}"

    # And every category-spend request stayed below 400, with no uncaught JS errors
    assert statuses
    assert not [s for s in statuses if s[0] >= 400], statuses
    assert not js_errors, js_errors


def test_top_level_shows_categories_not_products(live_server: str, page: Page) -> None:
    """Verify the initial view shows category tiles with products hidden until a drill."""
    # Given the treemap is drawn
    _load(page, live_server)

    # Then category names and dollar amounts are labeled directly
    text = _svg_text(page).lower()
    assert "dairy" in text and "produce" in text, text
    assert "$" in text

    # And the products (eggs, bananas, ...) are not shown until a category is clicked
    assert "eggs" not in text and "bananas" not in text, text


def test_clicking_a_category_drills_into_its_products(live_server: str, page: Page) -> None:
    """Verify clicking a category reveals the products that make it up."""
    # Given the treemap is drawn
    _load(page, live_server)

    # When drilling into dairy
    _drill_into(page, "dairy")

    # Then dairy's products (eggs and milk) become visible
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('#category-spend-chart text'))"
        ".some(t => t.textContent.includes('eggs'))",
        timeout=8000,
    )
    text = _svg_text(page).lower()
    assert "eggs" in text and "milk" in text, text


def test_clicking_a_product_does_not_drill_deeper(live_server: str, page: Page) -> None:
    """Verify a product is a leaf: clicking it does not zoom in (its siblings stay visible)."""
    # Given the treemap drilled into dairy so both its products are shown
    _load(page, live_server)
    _drill_into(page, "dairy")
    page.wait_for_function(
        "() => { const t = Array.from(document.querySelectorAll('#category-spend-chart text'))"
        ".map(n => n.textContent).join(' '); return t.includes('eggs') && t.includes('milk'); }",
        timeout=8000,
    )

    # When clicking inside the drilled area (a point that lands on a product tile, not the pathbar)
    box = page.locator("#category-spend-chart").bounding_box()
    assert box is not None
    page.mouse.click(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.45)
    page.wait_for_timeout(700)  # allow any (unwanted) zoom animation to settle

    # Then the view has not zoomed into a single product: both siblings remain visible
    text = _svg_text(page).lower()
    assert "eggs" in text and "milk" in text, (
        f"clicking a product drilled deeper and hid a sibling: {text!r}"
    )


def test_breadcrumb_is_visible_after_drilling(live_server: str, page: Page) -> None:
    """Verify the pathbar breadcrumb renders legibly (not the white-on-white default)."""
    # Given the treemap drilled into a category
    _load(page, live_server)
    _drill_into(page, "dairy")
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('#category-spend-chart text'))"
        ".some(t => t.textContent.includes('eggs'))",
        timeout=8000,
    )

    # Then the "All categories" breadcrumb crumb is visible to click back
    crumb = page.locator("#category-spend-chart").get_by_text("All categories", exact=True).first
    expect(crumb).to_be_visible()


def test_hover_shows_readable_detail(live_server: str, page: Page) -> None:
    """Verify hovering a tile reveals a readable detail label with the spend and share."""
    # Given the treemap is drawn
    _load(page, live_server)

    # When hovering over the chart
    page.locator("#category-spend-chart").hover()

    # Then a hover label shows a dollar amount and the itemized-spend share
    hoverlayer = page.locator(".hoverlayer")
    expect(hoverlayer).to_contain_text("$", timeout=5000)
    expect(hoverlayer).to_contain_text("itemized spend")


def test_store_filter_rerenders_the_map(live_server: str, page: Page) -> None:
    """Verify the toolbar's store filter re-renders the treemap and deep-links the selection."""
    # Given the treemap is drawn
    statuses: list[int] = []
    page.on("response", lambda r: statuses.append(r.status) if "category-spend" in r.url else None)
    _load(page, live_server)

    # When choosing a specific store (the first real option after "All stores")
    page.select_option("select[name=store]", index=1)

    # Then the chart re-renders and the selection is deep-linked, with no 4xx/5xx
    page.wait_for_function("() => window.location.search.includes('store=')", timeout=8000)
    page.wait_for_function(_TREEMAP_DRAWN, timeout=10000)
    assert not [s for s in statuses if s >= 400], statuses


def test_treemap_renders_on_mobile(live_server: str, mobile_page: Page) -> None:
    """Verify the treemap draws and labels categories at a phone width (390px)."""
    # Given the view is loaded in a phone-sized viewport
    _load(mobile_page, live_server)

    # Then the treemap is drawn and shows a category label with a dollar value
    trace_type = mobile_page.evaluate(
        "() => document.querySelector('#category-spend-chart').data[0].type"
    )
    assert trace_type == "treemap", f"expected a treemap trace, got {trace_type!r}"
    text = _svg_text(mobile_page).lower()
    assert ("dairy" in text or "produce" in text) and "$" in text, text


def test_spend_ramp_is_a_monotonic_color_ramp(live_server: str, page: Page) -> None:
    """Verify spendRamp returns n distinct colors whose brightness is monotonic (a real ramp)."""
    # Given the helper is loaded
    _load(page, live_server)

    # When building a five-color ramp and reading each color's brightness (Rec. 601 luma)
    lumas = page.evaluate("""
        () => {
          if (typeof window.Insights.spendRamp !== 'function') return null;
          const colors = window.Insights.spendRamp(5);
          if (!Array.isArray(colors) || colors.length !== 5) return null;
          return colors.map(c => {
            const m = c.match(/\\d+/g).map(Number);
            return 0.299 * m[0] + 0.587 * m[1] + 0.114 * m[2];
          });
        }
    """)
    assert lumas is not None, "Insights.spendRamp did not return five colors"

    # Then brightness changes strictly in one direction across the ramp (not random hues)
    deltas = [lumas[i + 1] - lumas[i] for i in range(len(lumas) - 1)]
    assert all(d > 0 for d in deltas) or all(d < 0 for d in deltas), lumas
