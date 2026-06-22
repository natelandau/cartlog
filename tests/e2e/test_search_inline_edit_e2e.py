"""End-to-end coverage for the search page inline edit panel (desktop + mobile).

The Edit button is only rendered for authenticated Editor+ users, so every flow
begins with a login step. The live_server seeds an admin user (e2e-admin /
"violet pantry koala") and a milk line item (raw_description="2% MILK") that has
no existing unit/size, making it a clean target for size edits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

# A term known to match exactly one seeded line item: raw_description="2% MILK",
# product "milk", with no pre-existing unit/size (clean for size edits).
SEARCH_TERM = "milk"


def _login(page: Page, base_url: str) -> None:
    """Log in as the seeded admin user so authenticated routes render correctly."""
    page.goto(f"{base_url}/login")
    page.locator("input[name='username']").fill("e2e-admin")
    page.locator("input[name='password']").fill("violet pantry koala")
    page.locator("button[type='submit']").click()
    # Wait for the post-login redirect (303 → /) to complete
    page.wait_for_url(f"{base_url}/")


def _open_first_panel(page: Page, base_url: str) -> str:
    """Log in, search for milk, open the first row's edit panel, and return its line-item id."""
    _login(page, base_url)
    page.goto(f"{base_url}/search?q={SEARCH_TERM}")
    first_row = page.locator("tr[id^='search-row-']").first
    expect(first_row).to_be_visible()
    # The `q` deep link auto-runs a search on load (hx-trigger includes `load`). Let that
    # request settle before clicking Edit, otherwise the late results swap can replace the
    # row and wipe the panel we just opened, racing the test.
    page.wait_for_load_state("networkidle")
    row_id = first_row.get_attribute("id")
    assert row_id is not None
    line_id = row_id.removeprefix("search-row-")
    first_row.get_by_role("button", name="Edit").click()
    expect(page.locator(f"#search-edit-{line_id}")).to_be_visible()
    # The panel arrives via an htmx outerHTML swap; htmx wires the new content's hx-get
    # buttons (Save/Cancel) when it processes the swapped nodes, which can land a beat after
    # the nodes become visible under a loaded headless suite. Wait for the edit request to go
    # idle, then give htmx a short settle window, so a Cancel/Escape immediately after open is
    # not dropped by an as-yet-unwired button.
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(250)
    return line_id


def test_search_panel_edits_size_and_saves_desktop(page: Page, live_server: str) -> None:
    """Verify editing size in the panel saves and refreshes the row on desktop."""
    # Track 4xx responses during this flow to surface route bugs immediately
    errors: list[tuple[int, str]] = []
    page.on("response", lambda r: errors.append((r.status, r.url)) if r.status >= 400 else None)

    # Given an open edit panel
    line_id = _open_first_panel(page, live_server)
    panel = page.locator(f"#search-edit-{line_id}")

    # When the editor sets a new size and saves
    panel.get_by_role("textbox", name="Package size").fill("2L")
    panel.get_by_role("button", name="Save").click()

    # Then the panel closes and the read row is back with its Edit button
    expect(page.locator(f"#search-edit-{line_id}")).to_have_count(0)
    row = page.locator(f"#search-row-{line_id}")
    expect(row).to_be_visible()
    expect(row.get_by_role("button", name="Edit")).to_be_visible()

    # And no request errored during the flow
    assert not errors, f"Unexpected 4xx during panel save flow: {errors}"


def test_search_panel_edits_receipt_text_desktop(page: Page, live_server: str) -> None:
    """Verify editing the receipt text in the panel updates the row's Description on save."""
    errors: list[tuple[int, str]] = []
    page.on("response", lambda r: errors.append((r.status, r.url)) if r.status >= 400 else None)

    # Given an open edit panel
    line_id = _open_first_panel(page, live_server)
    panel = page.locator(f"#search-edit-{line_id}")

    # When the editor rewrites the receipt text and saves
    panel.get_by_role("textbox", name="Receipt text").fill("ORGANIC WHOLE MILK")
    panel.get_by_role("button", name="Save").click()

    # Then the panel closes and the read row's Description cell shows the new text
    expect(page.locator(f"#search-edit-{line_id}")).to_have_count(0)
    row = page.locator(f"#search-row-{line_id}")
    expect(row).to_contain_text("ORGANIC WHOLE MILK")

    assert not errors, f"Unexpected 4xx during receipt-text edit flow: {errors}"


def test_search_panel_cancel_discards(page: Page, live_server: str) -> None:
    """Verify Cancel closes the panel without mutating the row."""
    errors: list[tuple[int, str]] = []
    page.on("response", lambda r: errors.append((r.status, r.url)) if r.status >= 400 else None)

    # Given an open edit panel
    line_id = _open_first_panel(page, live_server)

    # When the editor clicks Cancel
    page.locator(f"#search-edit-{line_id}").get_by_role("button", name="Cancel").click()

    # Then the panel is gone and the read row is still visible
    expect(page.locator(f"#search-edit-{line_id}")).to_have_count(0)
    expect(page.locator(f"#search-row-{line_id}")).to_be_visible()

    assert not errors, f"Unexpected 4xx during cancel flow: {errors}"


def test_search_panel_escape_closes(page: Page, live_server: str) -> None:
    """Verify an Escape keydown in the panel closes it via the delegated cancel listener."""
    errors: list[tuple[int, str]] = []
    page.on("response", lambda r: errors.append((r.status, r.url)) if r.status >= 400 else None)

    # Given an open edit panel
    line_id = _open_first_panel(page, live_server)
    size_field = page.locator(f"#search-edit-{line_id}").get_by_role("textbox", name="Package size")

    # When an Escape keydown is raised inside the panel. We dispatch a real bubbling
    # KeyboardEvent rather than page.keyboard.press(): headless Chromium does not reliably
    # deliver OS-level keystrokes across a sequential suite's context churn. The production
    # handler is a delegated `keydown` listener on document.body that fires htmx.ajax() for
    # the cancel route, so a bubbling synthetic event exercises exactly the code under test.
    size_field.evaluate(
        "el => { el.focus();"
        " el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); }"
    )

    # Then the panel closes and the read row remains
    expect(page.locator(f"#search-edit-{line_id}")).to_have_count(0)
    expect(page.locator(f"#search-row-{line_id}")).to_be_visible()

    assert not errors, f"Unexpected 4xx during Escape-cancel flow: {errors}"


def test_search_panel_usable_on_mobile(mobile_page: Page, live_server: str) -> None:
    """Verify the panel renders as a usable stacked form on a phone viewport."""
    errors: list[tuple[int, str]] = []
    mobile_page.on(
        "response", lambda r: errors.append((r.status, r.url)) if r.status >= 400 else None
    )

    # Given a phone-sized viewport with the panel open
    line_id = _open_first_panel(mobile_page, live_server)
    panel = mobile_page.locator(f"#search-edit-{line_id}")

    # Then the size field is visible and spans most of the 390px viewport width. With the CSS
    # specificity fix, .data-table .search-edit-panel td (0,2,1) beats .data-table tbody td
    # (0,1,2), so the panel renders as a full-width block instead of a flex card cell.
    size_field = panel.get_by_role("textbox", name="Package size")
    expect(size_field).to_be_visible()
    box = size_field.bounding_box()
    assert box is not None, "Size field has no bounding box"
    assert box["width"] > 280, f"Size field appears collapsed: width={box['width']:.0f}px"

    # And saving works on mobile too
    size_field.fill("500ml")
    panel.get_by_role("button", name="Save").click()
    expect(mobile_page.locator(f"#search-edit-{line_id}")).to_have_count(0)

    assert not errors, f"Unexpected 4xx during mobile panel save flow: {errors}"
