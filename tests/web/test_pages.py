"""Tests that web pages render with expected content."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cartlog.db.models import Receipt, ReceiptStatus, Role, Store, User
from tests.factories import seed_user


def test_dashboard_renders_recent_and_review_count(app_client):
    """Verify the dashboard shows recent receipts and a needs-review link."""
    # When loading the dashboard
    response = app_client.get("/")

    # Then it renders with seeded content
    assert response.status_code == 200
    assert "Recent receipts" in response.text
    assert "need review" in response.text  # seed has one needs_review receipt


def test_receipt_list_renders_all_by_default(app_client):
    """Verify GET /receipts lists seeded receipts."""
    # When loading the list
    response = app_client.get("/receipts")

    # Then receipts are shown
    assert response.status_code == 200
    assert "Safeway" in response.text
    assert "Costco" in response.text


def test_receipt_list_filters_by_status(app_client):
    """Verify the status query param filters the list."""
    # When filtering to needs_review
    response = app_client.get("/receipts", params={"status": "needs_review"})

    # Then only a review link is present and a Review action is offered
    assert response.status_code == 200
    assert "Review" in response.text


def test_receipt_detail_renders_line_items(app_client):
    """Verify GET /receipts/{id} shows header and line items."""
    # Given the first seeded receipt id
    rid = app_client.get("/api/analytics/search", params={"q": "eggs"}).json()[0]["receipt_id"]

    # When loading its detail page
    response = app_client.get(f"/receipts/{rid}")

    # Then the page renders with line-item context
    assert response.status_code == 200
    assert "eggs" in response.text


def test_receipt_detail_unknown_id_404(app_client):
    """Verify an unknown receipt id returns 404."""
    # When loading a missing receipt
    response = app_client.get("/receipts/999999")

    # Then it is not found
    assert response.status_code == 404


def test_receipt_image_rejects_path_outside_storage(app_client):
    """Verify the image route 404s when the stored path escapes the storage dir."""
    # The seeded receipts use image_path "/tmp/x.png", which resolves outside the test's
    # image_storage_dir, so the is_relative_to guard must refuse to serve it.
    rid = app_client.get("/api/analytics/search", params={"q": "eggs"}).json()[0]["receipt_id"]

    # When requesting that receipt's image
    response = app_client.get(f"/receipts/{rid}/image")

    # Then the route refuses the out-of-storage path
    assert response.status_code == 404


def test_receipt_image_serves_file_inside_storage(app_client):
    """Verify the image route streams a real file located inside the storage dir."""
    # Given a real image file inside the configured storage dir and a receipt pointing at it
    state = app_client.app.state
    storage = state.settings.image_storage_dir
    storage.mkdir(parents=True, exist_ok=True)
    image = storage / "real.png"
    image.write_bytes(b"\x89PNG real-bytes")
    with state.session_factory() as session:
        store = session.query(Store).first()
        receipt = Receipt(
            store=store,
            purchase_date=date(2026, 4, 1),
            total=Decimal("1.00"),
            currency="USD",
            image_path=str(image),
            raw_parser_json="{}",
            source="web",
            status=ReceiptStatus.PARSED,
        )
        session.add(receipt)
        session.commit()
        receipt_id = receipt.id

    # When requesting that receipt's image
    response = app_client.get(f"/receipts/{receipt_id}/image")

    # Then the file is streamed back verbatim
    assert response.status_code == 200
    assert response.content == b"\x89PNG real-bytes"


def test_upload_page_renders_form(app_client):
    """Verify GET /upload renders a file-upload form."""
    # When loading the upload page
    response = app_client.get("/upload")

    # Then a form targeting /receipts is present
    assert response.status_code == 200
    assert 'type="file"' in response.text


def test_upload_page_allows_multiple_file_selection(app_client):
    """Verify the upload form lets users pick multiple files at once."""
    # When loading the upload page
    response = app_client.get("/upload")

    # Then the file input is a multi-select bound to the batch field name
    assert response.status_code == 200
    assert "multiple" in response.text
    assert 'name="files"' in response.text


def test_search_page_renders_box(app_client):
    """Verify GET /search renders a search input."""
    # When loading the search page
    response = app_client.get("/search")

    # Then a search box is present
    assert response.status_code == 200
    assert 'name="q"' in response.text


def test_search_page_prefills_query_from_param(app_client):
    """Verify GET /search?q=eggs prefills the box and adds a load trigger so it auto-runs."""
    # When deep-linking to the search page with a query
    response = app_client.get("/search", params={"q": "eggs"})

    # Then the box is prefilled and the input fires on load so results render immediately
    assert response.status_code == 200
    assert 'value="eggs"' in response.text
    assert "load" in response.text


def test_search_results_partial_returns_matches(app_client):
    """Verify GET /search/results renders matching line items as an HTML fragment."""
    # When searching for eggs
    response = app_client.get("/search/results", params={"q": "eggs"})

    # Then matches render in the fragment
    assert response.status_code == 200
    assert "eggs" in response.text


def test_search_results_no_match_message(app_client):
    """Verify a no-match search renders a clear message, not an error."""
    # When searching for something absent
    response = app_client.get("/search/results", params={"q": "zzzznope"})

    # Then a friendly message renders
    assert response.status_code == 200
    assert "no matching" in response.text.lower()


def test_insights_index_redirects_to_default_view(app_client):
    """Verify GET /insights lands on the default analysis."""
    # When loading the bare insights index without following the redirect
    response = app_client.get("/insights", follow_redirects=False)

    # Then it temporarily redirects to the default view
    assert response.status_code == 307
    assert response.headers["location"] == "/insights/price-history"


def test_insights_view_renders_full_shell(app_client):
    """Verify a plain GET of an analysis renders the shell: select, panel, and rendering layer."""
    # When loading an analysis as a full page
    response = app_client.get("/insights/price-history")

    # Then the shell, all registered options, the panel, and insights.js are present
    assert response.status_code == 200
    assert "/static/insights.js" in response.text
    assert 'id="insights-panel"' in response.text
    assert 'data-insight-view="price-history"' in response.text
    assert ">Price history</option>" in response.text
    assert ">Store comparison</option>" in response.text
    assert ">Category spend</option>" in response.text
    # Plotly is lazy-loaded by JS, so it must NOT be hard-linked in the server HTML
    assert "plotly.min.js" not in response.text


def test_insights_view_htmx_returns_bare_fragment(app_client):
    """Verify an htmx request returns only the fragment, without the shell chrome."""
    # When htmx requests an analysis
    response = app_client.get("/insights/store-comparison", headers={"HX-Request": "true"})

    # Then the fragment renders without the navbar or the select shell
    assert response.status_code == 200
    assert 'data-insight-view="store-comparison"' in response.text
    assert 'id="insight-select"' not in response.text
    assert "<nav" not in response.text


def test_insights_view_history_restore_returns_full_shell(app_client):
    """Verify an htmx history-restore re-fetch gets the full shell, not a bare fragment."""
    # When htmx re-fetches on a history-cache miss (it sets both headers and swaps into <body>)
    response = app_client.get(
        "/insights/store-comparison",
        headers={"HX-Request": "true", "HX-History-Restore-Request": "true"},
    )

    # Then the full shell is returned so the page is not replaced by a chrome-less fragment
    assert response.status_code == 200
    assert "/static/insights.js" in response.text
    assert 'id="insight-select"' in response.text


def test_insights_unknown_view_404s(app_client):
    """Verify an unregistered analysis key is a 404, not a blank shell."""
    # When requesting a view that is not registered
    response = app_client.get("/insights/not-a-view")

    # Then the request 404s
    assert response.status_code == 404


def test_insights_js_rerenders_on_history_restore(app_client):
    """Verify back/forward (htmx history restore) re-triggers chart rendering, else the panel is blank."""
    # When fetching the served Insights rendering layer
    response = app_client.get("/static/insights.js")

    # Then it wires a renderer to htmx's history-restore event
    assert response.status_code == 200
    assert "htmx:historyRestore" in response.text


def test_charts_redirects_to_insights(app_client):
    """Verify the legacy /charts path permanently redirects to /insights."""
    # When loading the old charts URL without following the redirect
    response = app_client.get("/charts", follow_redirects=False)

    # Then it permanently redirects to the new page
    assert response.status_code == 301
    assert response.headers["location"] == "/insights"


def test_detail_offers_edit_for_parsed_receipt(app_client):
    """Verify a parsed (non-review) receipt's detail page exposes an Edit affordance."""
    # Given a parsed receipt id (search returns the eggs line on a parsed receipt)
    rid = app_client.get("/api/analytics/search", params={"q": "bananas"}).json()[0]["receipt_id"]

    # When loading its detail page
    response = app_client.get(f"/receipts/{rid}")

    # Then an edit control is present even though the receipt is not needs_review
    assert response.status_code == 200
    assert f"/receipts/{rid}/edit" in response.text


def test_review_url_redirects_to_detail(app_client):
    """Verify the retired /review URL redirects to the unified detail page."""
    # Given any receipt id
    rid = app_client.get("/api/analytics/search", params={"q": "eggs"}).json()[0]["receipt_id"]

    # When requesting the old review URL without following redirects
    response = app_client.get(f"/receipts/{rid}/review", follow_redirects=False)

    # Then it redirects to the detail page
    assert response.status_code in (302, 307)
    assert response.headers["location"] == f"/receipts/{rid}"


def test_detail_renders_pdf_receipt_in_object_viewer(app_client):
    """Verify a PDF-backed receipt renders an <object> viewer instead of a broken <img>."""
    # Given a receipt whose stored file is a PDF
    state = app_client.app.state
    with state.session_factory() as session:
        store = session.query(Store).first()
        receipt = Receipt(
            store=store,
            purchase_date=date(2026, 4, 1),
            total=Decimal("1.00"),
            currency="USD",
            image_path="/tmp/receipt.pdf",  # noqa: S108
            raw_parser_json="{}",
            source="web",
            status=ReceiptStatus.PARSED,
        )
        session.add(receipt)
        session.commit()
        rid = receipt.id

    # When loading its detail page
    response = app_client.get(f"/receipts/{rid}")

    # Then the PDF viewer object is used and no receipt <img> is emitted
    assert response.status_code == 200
    assert 'type="application/pdf"' in response.text
    assert 'alt="Receipt image"' not in response.text


def test_detail_renders_side_by_side_grid(app_client):
    """Verify the detail page renders the image-and-items grid container."""
    # Given a seeded receipt id
    rid = app_client.get("/api/analytics/search", params={"q": "eggs"}).json()[0]["receipt_id"]

    # When loading its detail page
    response = app_client.get(f"/receipts/{rid}")

    # Then the two-column grid and image figure are present
    assert response.status_code == 200
    assert "lg:grid-cols-[" in response.text
    assert f"/receipts/{rid}/image" in response.text
    assert 'id="items-panel"' in response.text


# ---------------------------------------------------------------------------
# Role-aware navigation tests (Task 22)
# ---------------------------------------------------------------------------


def test_nav_shows_sign_out_for_authenticated_admin(admin_client):
    """Verify the navbar shows a Sign out control when the user is authenticated."""
    # When loading any page as an admin
    response = admin_client.get("/")

    # Then a sign-out control is visible
    assert response.status_code == 200
    assert "Sign out" in response.text


def test_nav_shows_sign_in_for_anonymous_user(anon_client):
    """Verify the navbar shows a Sign in link when no user is logged in."""
    # Given anonymous access is enabled (default) and at least one admin exists so
    # the first-run setup gate does not redirect to /setup
    with anon_client.app.state.session_factory() as s:
        seed_user(s, username="admin_gate", role=Role.ADMIN)

    # When loading the dashboard anonymously
    response = anon_client.get("/")

    # Then a sign-in link is shown and no sign-out control appears
    assert response.status_code == 200
    assert "Sign in" in response.text
    assert "Sign out" not in response.text


def test_admin_nav_shows_upload_and_admin_links(admin_client):
    """Verify an admin user sees both the Upload and Admin nav links."""
    # When loading the dashboard as an admin
    response = admin_client.get("/")

    # Then both gated nav items are present
    assert response.status_code == 200
    assert "/upload" in response.text
    assert "/admin" in response.text


def test_viewer_does_not_see_upload_or_admin_links(viewer_client):
    """Verify a viewer role cannot see the Upload or Admin nav links."""
    # When loading the dashboard as a viewer
    response = viewer_client.get("/")

    # Then neither gated link is present, but read-only links are
    assert response.status_code == 200
    assert "/upload" not in response.text
    assert "/admin" not in response.text
    assert "/receipts" in response.text
    assert "Dashboard" in response.text


def test_editor_sees_upload_but_not_admin_link(editor_client):
    """Verify an editor sees the Upload link but not the Admin link."""
    # When loading the dashboard as an editor
    response = editor_client.get("/")

    # Then Upload is present but Admin is not
    assert response.status_code == 200
    assert "/upload" in response.text
    assert "/admin" not in response.text


def test_receipt_detail_shows_uploader_username(admin_client):
    """Verify the receipt detail page shows the uploader's username when set."""
    # Given a receipt whose user_id points to a real user
    state = admin_client.app.state
    with state.session_factory() as session:
        user = session.query(User).first()
        assert user is not None
        store = session.query(Store).first()
        receipt = Receipt(
            store=store,
            purchase_date=date(2026, 5, 1),
            total=Decimal("9.99"),
            currency="USD",
            image_path="/tmp/uploader.png",  # noqa: S108
            raw_parser_json="{}",
            source="web",
            status=ReceiptStatus.PARSED,
            user_id=user.id,
        )
        session.add(receipt)
        session.commit()
        rid = receipt.id
        username = user.username

    # When loading its detail page
    response = admin_client.get(f"/receipts/{rid}")

    # Then the uploader's username is displayed
    assert response.status_code == 200
    assert f"Uploaded by {username}" in response.text


def test_receipt_detail_omits_uploader_when_none(admin_client):
    """Verify the receipt detail page omits uploader attribution when user_id is None."""
    # Given a receipt with no associated user (e.g. folder ingest)
    state = admin_client.app.state
    with state.session_factory() as session:
        store = session.query(Store).first()
        receipt = Receipt(
            store=store,
            purchase_date=date(2026, 5, 2),
            total=Decimal("5.00"),
            currency="USD",
            image_path="/tmp/system.png",  # noqa: S108
            raw_parser_json="{}",
            source="folder",
            status=ReceiptStatus.PARSED,
            user_id=None,
        )
        session.add(receipt)
        session.commit()
        rid = receipt.id

    # When loading its detail page
    response = admin_client.get(f"/receipts/{rid}")

    # Then no uploader line appears
    assert response.status_code == 200
    assert "Uploaded by" not in response.text
