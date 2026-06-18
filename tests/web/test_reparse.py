"""Tests for the POST /receipts/{id}/reparse route and its detail-page button."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cartlog.db.models import IngestionJob, JobStatus, Receipt, ReceiptStatus, Store
from tests.web.helpers import first_receipt_id


def _receipt_with_on_disk_image(app_client) -> int:
    """Create a receipt whose image file exists inside the app's storage dir; return its id."""
    state = app_client.app.state
    storage = state.settings.image_storage_dir
    storage.mkdir(parents=True, exist_ok=True)
    image = storage / "web-rp.png"
    image.write_bytes(b"img")
    with state.session_factory() as session:
        receipt = Receipt(
            store=Store(chain_name="ReparseMart", location=None),
            purchase_date=date(2026, 1, 1),
            total=Decimal("1.00"),
            currency="USD",
            image_path=str(image),
            raw_parser_json="{}",
            source="web",
            status=ReceiptStatus.PARSED,
        )
        session.add(receipt)
        session.commit()
        return receipt.id


def test_reparse_returns_hx_redirect_and_requeues(app_client) -> None:
    """Verify reparse deletes the receipt, queues a job for its image, and redirects htmx."""
    # Given a receipt whose image file exists in storage
    rid = _receipt_with_on_disk_image(app_client)

    # When posting a reparse
    response = app_client.post(f"/receipts/{rid}/reparse")

    # Then htmx is told to redirect, the old receipt is gone, and a pending job exists
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/receipts"
    with app_client.app.state.session_factory() as session:
        assert session.get(Receipt, rid) is None
        jobs = session.query(IngestionJob).filter_by(status=JobStatus.PENDING).all()
        assert len(jobs) == 1


def test_reparse_unknown_id_returns_404(app_client) -> None:
    """Verify reparsing a nonexistent receipt returns 404."""
    # When reparsing an id that does not exist
    response = app_client.post("/receipts/99999/reparse")

    # Then the route reports not found
    assert response.status_code == 404


def test_reparse_missing_image_returns_409(app_client) -> None:
    """Verify reparsing a receipt whose image is absent returns a 4xx and keeps the receipt."""
    # Given a seeded receipt whose image_path (/tmp/x.png) is not in the test storage dir
    rid = first_receipt_id(app_client)

    # When posting a reparse
    response = app_client.post(f"/receipts/{rid}/reparse")

    # Then the route refuses and the receipt is untouched
    assert response.status_code == 409
    with app_client.app.state.session_factory() as session:
        assert session.get(Receipt, rid) is not None


def test_detail_shows_reparse_button_when_image_present(app_client) -> None:
    """Verify the detail page renders the reparse button when the image file exists."""
    # Given a receipt with an on-disk image
    rid = _receipt_with_on_disk_image(app_client)

    # When viewing its detail page
    response = app_client.get(f"/receipts/{rid}")

    # Then the reparse control is present with a confirmation prompt
    assert response.status_code == 200
    assert f'hx-post="/receipts/{rid}/reparse"' in response.text


def test_detail_hides_reparse_button_when_image_missing(app_client) -> None:
    """Verify the detail page omits the reparse button when the image file is missing."""
    # Given a seeded receipt whose image file is not in storage
    rid = first_receipt_id(app_client)

    # When viewing its detail page
    response = app_client.get(f"/receipts/{rid}")

    # Then no reparse control is rendered (delete is still present)
    assert response.status_code == 200
    assert f"/receipts/{rid}/reparse" not in response.text
    assert f'hx-delete="/receipts/{rid}"' in response.text
