"""Tests that web pages render with expected content."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cartlog.db.models import Receipt, ReceiptStatus, Store


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


def test_charts_page_renders_shell(app_client):
    """Verify GET /charts renders chart containers and loads Plotly + charts.js."""
    # When loading the charts page
    response = app_client.get("/charts")

    # Then the chart shell and scripts are present
    assert response.status_code == 200
    assert "plotly.min.js" in response.text
    assert 'id="price-history-chart"' in response.text


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
