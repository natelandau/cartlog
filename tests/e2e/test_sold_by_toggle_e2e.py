"""End-to-end coverage for the sold-by mode toggle in both line-item editors.

Covers:
  (a) Receipt editor: Sold-by select shows/hides the Size-each and Unit groups; Add item
      clones a working row whose toggle also works.
  (b) Search inline editor: Sold-by toggle swaps groups; saving By-the-item with
      size_amount + size_unit POSTs successfully and the row updates.
  (c) No request returns 4xx/5xx during these flows.

The live_server seeds an admin user (e2e-admin / "violet pantry koala") and a
needs_review receipt that the receipt editor can open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e


def _login(page: Page, base_url: str) -> None:
    """Log in as the seeded admin user."""
    page.goto(f"{base_url}/login")
    page.locator("input[name='username']").fill("e2e-admin")
    page.locator("input[name='password']").fill("violet pantry koala")
    page.locator("button[type='submit']").click()
    page.wait_for_url(f"{base_url}/")


def _open_needs_review_edit(page: Page, base_url: str) -> str:
    """Log in, navigate to the needs_review receipt's edit panel, return the receipt path."""
    _login(page, base_url)
    # The receipts list with a needs_review filter surfaces the seeded receipt.
    page.goto(f"{base_url}/receipts?status=needs_review")
    page.wait_for_load_state("networkidle")
    # Click through to the detail page.
    first_link = page.locator("a[href^='/receipts/']").first
    expect(first_link).to_be_visible()
    receipt_href = first_link.get_attribute("href")
    assert receipt_href is not None
    page.goto(f"{base_url}{receipt_href}")
    page.wait_for_load_state("networkidle")
    # Open the edit panel via the Edit receipt button on the items section.
    edit_btn = page.get_by_role("button", name="Edit receipt")
    expect(edit_btn).to_be_visible()
    edit_btn.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(250)
    return receipt_href


def _open_search_panel(page: Page, base_url: str) -> str:
    """Log in, search for milk, open the first row's edit panel, return its line-item id."""
    _login(page, base_url)
    page.goto(f"{base_url}/search?q=milk")
    page.wait_for_load_state("networkidle")
    first_row = page.locator("tr[id^='search-row-']").first
    expect(first_row).to_be_visible()
    row_id = first_row.get_attribute("id")
    assert row_id is not None
    line_id = row_id.removeprefix("search-row-")
    first_row.get_by_role("button", name="Edit").click()
    expect(page.locator(f"#search-edit-{line_id}")).to_be_visible()
    page.wait_for_load_state("networkidle")
    # Wait until the sold_by select is interactive rather than sleeping an arbitrary duration.
    # This ensures htmx has processed the swapped nodes before the test interacts with them.
    expect(page.locator("select[name='sold_by']").first).to_be_visible()
    return line_id


# ---------------------------------------------------------------------------
# Task 11: receipt editor toggle
# ---------------------------------------------------------------------------


def test_receipt_editor_sold_by_toggle_hides_and_shows_groups(page: Page, live_server: str) -> None:
    """Verify switching Sold-by hides Size-each and reveals Unit, and switching back reverses it."""
    errors: list[tuple[int, str]] = []
    # Exclude /image 404s: seeded test receipts have no image file on disk, so the image
    # endpoint always returns 404 in the e2e fixture. It is not a bug in our implementation.
    page.on(
        "response",
        lambda r: (
            errors.append((r.status, r.url)) if r.status >= 400 and "/image" not in r.url else None
        ),
    )

    # Given the receipt edit form is open
    _open_needs_review_edit(page, live_server)

    # Then the first line card is present and Size-each group is visible by default (item mode)
    first_card = page.locator(".line-item").first
    size_each_group = first_card.locator("[data-sold-by-group='item']")
    unit_group = first_card.locator("[data-sold-by-group='measure']")
    expect(size_each_group).to_be_visible()
    expect(unit_group).to_be_hidden()

    # When switching to "By weight/volume"
    first_card.locator("select[name='sold_by']").select_option("measure")

    # Then Size-each hides and Unit becomes visible
    expect(size_each_group).to_be_hidden()
    expect(unit_group).to_be_visible()

    # When switching back to "By the item"
    first_card.locator("select[name='sold_by']").select_option("item")

    # Then Size-each is visible again and Unit is hidden
    expect(size_each_group).to_be_visible()
    expect(unit_group).to_be_hidden()

    assert not errors, f"Unexpected 4xx during receipt editor toggle: {errors}"


def test_receipt_editor_add_item_clones_working_toggle(page: Page, live_server: str) -> None:
    """Verify the Add item button clones a row whose Sold-by toggle also works."""
    errors: list[tuple[int, str]] = []
    # Exclude /image 404s: seeded test receipts have no image file on disk.
    page.on(
        "response",
        lambda r: (
            errors.append((r.status, r.url)) if r.status >= 400 and "/image" not in r.url else None
        ),
    )

    # Given the receipt edit form is open
    _open_needs_review_edit(page, live_server)
    initial_count = page.locator(".line-item").count()

    # When clicking Add item
    page.get_by_role("button", name="Add item").click()

    # Then a new row appears
    new_count = page.locator(".line-item").count()
    assert new_count == initial_count + 1, f"Expected {initial_count + 1} cards, got {new_count}"

    # And the new row's Size-each group is visible (ITEM is the default)
    last_card = page.locator(".line-item").last
    size_each_group = last_card.locator("[data-sold-by-group='item']")
    unit_group = last_card.locator("[data-sold-by-group='measure']")
    expect(size_each_group).to_be_visible()
    expect(unit_group).to_be_hidden()

    # When toggling the new row to By weight/volume
    last_card.locator("select[name='sold_by']").select_option("measure")

    # Then its toggle also works correctly
    expect(size_each_group).to_be_hidden()
    expect(unit_group).to_be_visible()

    assert not errors, f"Unexpected 4xx during Add item clone toggle: {errors}"


# ---------------------------------------------------------------------------
# Task 12: search inline editor toggle
# ---------------------------------------------------------------------------


def test_search_editor_sold_by_toggle_hides_and_shows_groups(page: Page, live_server: str) -> None:
    """Verify the search inline editor Sold-by toggle swaps the Size-each and Unit groups."""
    errors: list[tuple[int, str]] = []
    # Exclude /image 404s: seeded test receipts have no image file on disk, so the image
    # endpoint always returns 404 in the e2e fixture. It is not a bug in our implementation.
    page.on(
        "response",
        lambda r: (
            errors.append((r.status, r.url)) if r.status >= 400 and "/image" not in r.url else None
        ),
    )

    # Given an open search edit panel
    line_id = _open_search_panel(page, live_server)
    panel = page.locator(f"#search-edit-{line_id}")

    # Then Size-each group is visible by default (item mode)
    size_each_group = panel.locator("[data-sold-by-group='item']")
    unit_group = panel.locator("[data-sold-by-group='measure']")
    expect(size_each_group).to_be_visible()
    expect(unit_group).to_be_hidden()

    # When switching to "By weight/volume"
    panel.locator("select[name='sold_by']").select_option("measure")

    # Then Size-each hides and Unit becomes visible
    expect(size_each_group).to_be_hidden()
    expect(unit_group).to_be_visible()

    # When switching back to "By the item"
    panel.locator("select[name='sold_by']").select_option("item")

    # Then Size-each is visible again and Unit is hidden
    expect(size_each_group).to_be_visible()
    expect(unit_group).to_be_hidden()

    assert not errors, f"Unexpected 4xx during search editor toggle: {errors}"


def test_search_editor_save_by_item_with_size_succeeds(page: Page, live_server: str) -> None:
    """Verify saving By-the-item with size_amount + size_unit POSTs successfully."""
    errors: list[tuple[int, str]] = []
    # Exclude /image 404s: seeded test receipts have no image file on disk, so the image
    # endpoint always returns 404 in the e2e fixture. It is not a bug in our implementation.
    page.on(
        "response",
        lambda r: (
            errors.append((r.status, r.url)) if r.status >= 400 and "/image" not in r.url else None
        ),
    )

    # Given an open search edit panel in item mode
    line_id = _open_search_panel(page, live_server)
    panel = page.locator(f"#search-edit-{line_id}")

    # When setting a size amount and unit and saving
    panel.locator("select[name='sold_by']").select_option("item")
    panel.locator("input[name='size_amount']").fill("2")
    # "l" is the canonical litre token from UNIT_FACTORS; a rename there would surface here immediately.
    panel.locator("select[name='size_unit']").select_option("l")
    panel.get_by_role("button", name="Save").click()

    # Then the panel closes and the read row is restored with the Edit button
    expect(page.locator(f"#search-edit-{line_id}")).to_have_count(0)
    row = page.locator(f"#search-row-{line_id}")
    expect(row).to_be_visible()
    expect(row.get_by_role("button", name="Edit")).to_be_visible()

    # And no request errored (POST must return < 400). That the saved size persists and renders
    # is asserted deterministically in tests/web/test_search_edit.py
    # (test_search_item_save_persists_item_size); checking the post-swap row text here races the
    # htmx read-row swap settling and flakes, so it is verified at the route level instead.
    assert not errors, f"Unexpected 4xx during search editor save: {errors}"


def test_search_editor_save_gram_size_renders_rounded_ounces(page: Page, live_server: str) -> None:
    """Verify a saved gram size renders to a US reader as rounded ounces, not raw grams."""
    errors: list[tuple[int, str]] = []
    # Exclude /image 404s: seeded test receipts have no image file on disk in the e2e fixture.
    page.on(
        "response",
        lambda r: (
            errors.append((r.status, r.url)) if r.status >= 400 and "/image" not in r.url else None
        ),
    )

    # Given an open search edit panel in item mode (imperial is the default unit system)
    line_id = _open_search_panel(page, live_server)
    panel = page.locator(f"#search-edit-{line_id}")

    # When saving a cereal-box gram size with the long precision that the bug report showed
    panel.locator("select[name='sold_by']").select_option("item")
    panel.locator("input[name='size_amount']").fill("382.7183")
    panel.locator("select[name='size_unit']").select_option("g")
    panel.get_by_role("button", name="Save").click()

    # Then the restored read row shows the size converted to ounces and rounded (382.7183 g ->
    # 13.5 oz), never the raw "382.7183 g". expect() polls, so it rides out the htmx row swap.
    row = page.locator(f"#search-row-{line_id}")
    expect(row).to_contain_text("13.5 oz")
    expect(row).not_to_contain_text("382.7183")

    assert not errors, f"Unexpected 4xx during gram-size save: {errors}"
