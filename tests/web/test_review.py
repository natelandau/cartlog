"""Tests for the review/correct workflow."""

from __future__ import annotations

from decimal import Decimal

from cartlog.db.models import (
    Category,
    LineItem,
    Product,
    Receipt,
    ReceiptReviewReason,
    ReceiptStatus,
    ReviewReasonCode,
)


def _needs_review_id(app_client) -> int:
    """Return the id of the seeded needs_review receipt."""
    state = app_client.app.state
    with state.session_factory() as session:
        receipt = session.query(Receipt).filter_by(status=ReceiptStatus.NEEDS_REVIEW).first()
        assert receipt is not None
        return receipt.id


def _line_ids(app_client, receipt_id: int) -> list[int]:
    """Return the line-item ids of the given receipt in stored order."""
    state = app_client.app.state
    with state.session_factory() as session:
        receipt = session.get(Receipt, receipt_id)
        assert receipt is not None
        return [li.id for li in receipt.line_items]


def test_edit_partial_renders_editable_fields(app_client) -> None:
    """Verify GET /receipts/{id}/edit returns an editable items form fragment."""
    # Given the seeded needs_review receipt
    rid = _needs_review_id(app_client)

    # When loading the edit partial
    response = app_client.get(f"/receipts/{rid}/edit")

    # Then the fragment exposes header + line-item inputs and the category picker
    assert response.status_code == 200
    assert 'name="total"' in response.text
    assert 'name="line_total"' in response.text
    assert 'name="category_id"' in response.text


def test_review_save_updates_header_and_reassigns_product(app_client) -> None:
    """Verify POST /receipts/{id} updates fields and get-or-creates a reassigned product."""
    # Given the needs_review receipt (seeded with two lines: eggs + milk)
    rid = _needs_review_id(app_client)
    eggs_line_id, milk_line_id = _line_ids(app_client, rid)

    # Given a payload that edits the total and renames the first line's product to a new
    # name while keeping the second line's product unchanged. httpx form-encodes a dict,
    # and list values become repeated keys, matching the real form's per-line rows.
    data = {
        "chain_name": "Safeway",
        "location": "Main St",
        "purchase_date": "2026-03-05",
        "total": "9.99",
        "currency": "USD",
        "line_id": [str(eggs_line_id), str(milk_line_id)],
        "raw_description": ["EGGS LARGE", "2% MILK"],
        "canonical_name": ["free range eggs", "milk"],  # first rename -> new product
        "quantity": ["1", "1"],
        "sold_by": ["item", "item"],
        "measure_unit": ["", ""],
        "size_amount": ["", ""],
        "size_unit": ["", ""],
        "unit_price": ["3.20", "2.00"],
        "line_total": ["3.20", "2.00"],
    }

    # When saving
    response = app_client.post(f"/receipts/{rid}", data=data)

    # Then it succeeds, the new product exists, and status is unchanged
    assert response.status_code == 200
    state = app_client.app.state
    with state.session_factory() as session:
        new_product = (
            session.query(Product).filter_by(canonical_name="free range eggs").one_or_none()
        )
        assert new_product is not None
        receipt = session.get(Receipt, rid)
        assert receipt is not None
        assert receipt.total == Decimal("9.99")
        # The renamed line now points at the newly created product.
        eggs_line = session.get(LineItem, eggs_line_id)
        assert eggs_line is not None
        assert eggs_line.product.canonical_name == "free range eggs"
        # The untouched line keeps its existing (reused) product, not a duplicate.
        milk_line = session.get(LineItem, milk_line_id)
        assert milk_line is not None
        assert milk_line.product.canonical_name == "milk"
        # Saving must NOT flip the review status.
        assert receipt.status == ReceiptStatus.NEEDS_REVIEW


def test_mark_reviewed_flips_status(app_client) -> None:
    """Verify POST /receipts/{id}/mark-reviewed sets status to parsed."""
    # Given the needs_review receipt
    rid = _needs_review_id(app_client)

    # When marking it reviewed
    response = app_client.post(f"/receipts/{rid}/mark-reviewed")

    # Then the status becomes parsed
    assert response.status_code == 200
    state = app_client.app.state
    with state.session_factory() as session:
        receipt = session.get(Receipt, rid)
        assert receipt is not None
        assert receipt.status == ReceiptStatus.PARSED


def test_review_save_invalid_total_rerenders_with_error(app_client) -> None:
    """Verify an invalid total re-renders the form and writes nothing."""
    # Given the needs_review receipt
    rid = _needs_review_id(app_client)
    eggs_line_id, _milk_line_id = _line_ids(app_client, rid)

    # Given a payload with an unparsable total
    data = {
        "chain_name": "Safeway",
        "location": "Main St",
        "purchase_date": "2026-03-05",
        "total": "not-a-number",
        "currency": "USD",
        "line_id": str(eggs_line_id),
        "raw_description": "EGGS LARGE",
        "canonical_name": "free range eggs",  # would create a product if the save committed
        "quantity": "1",
        "sold_by": "item",
        "measure_unit": "",
        "size_amount": "",
        "size_unit": "",
        "unit_price": "3.20",
        "line_total": "3.20",
    }

    # When posting it
    response = app_client.post(f"/receipts/{rid}", data=data)

    # Then the form comes back with an error and nothing was written
    assert response.status_code == 422
    assert "error" in response.text.lower()
    state = app_client.app.state
    with state.session_factory() as session:
        # The would-be new product was never created and the receipt is unchanged.
        assert (
            session.query(Product).filter_by(canonical_name="free range eggs").one_or_none() is None
        )
        receipt = session.get(Receipt, rid)
        assert receipt is not None
        assert receipt.total != Decimal("9.99")


def test_review_save_ragged_line_columns_rerenders_with_error(app_client) -> None:
    """Verify a tampered post with mismatched line column lengths is a 422, not a 500."""
    # Given the needs_review receipt
    rid = _needs_review_id(app_client)
    eggs_line_id, milk_line_id = _line_ids(app_client, rid)

    # Given a payload where line_id has two values but unit_price has only one (ragged)
    data = {
        "chain_name": "Safeway",
        "location": "Main St",
        "purchase_date": "2026-03-05",
        "total": "5.20",
        "currency": "USD",
        "line_id": [str(eggs_line_id), str(milk_line_id)],
        "raw_description": ["EGGS LARGE", "2% MILK"],
        "canonical_name": ["eggs", "milk"],
        "quantity": ["1", "1"],
        "sold_by": ["item", "item"],
        "measure_unit": ["", ""],
        "size_amount": ["", ""],
        "size_unit": ["", ""],
        "unit_price": ["3.20"],  # ragged: one value for two lines
        "line_total": ["3.20", "2.00"],
    }

    # When posting it
    response = app_client.post(f"/receipts/{rid}", data=data)

    # Then it is rejected as a form error rather than crashing
    assert response.status_code == 422
    assert "error" in response.text.lower()


def test_review_save_unknown_line_id_is_skipped(app_client) -> None:
    """Verify a phantom line_id row is skipped while the real lines are retained."""
    # Given the needs_review receipt's two real line ids
    rid = _needs_review_id(app_client)
    eggs_line_id, milk_line_id = _line_ids(app_client, rid)

    # Given a post of both real lines plus a third row referencing a phantom id
    data = {
        "chain_name": "Safeway",
        "location": "Main St",
        "purchase_date": "2026-03-05",
        "total": "5.20",
        "currency": "USD",
        "line_id": [str(eggs_line_id), str(milk_line_id), "999999"],
        "raw_description": ["EGGS LARGE", "2% MILK", "PHANTOM"],
        "canonical_name": ["eggs", "milk", "phantom product"],
        "quantity": ["1", "1", "1"],
        "sold_by": ["item", "item", "item"],
        "measure_unit": ["", "", ""],
        "size_amount": ["", "", ""],
        "size_unit": ["", "", ""],
        "unit_price": ["3.20", "2.00", "1.00"],
        "line_total": ["3.20", "2.00", "1.00"],
    }

    # When saving
    response = app_client.post(f"/receipts/{rid}", data=data)

    # Then the phantom creates no product and the two real lines survive
    assert response.status_code == 200
    state = app_client.app.state
    with state.session_factory() as session:
        assert (
            session.query(Product).filter_by(canonical_name="phantom product").one_or_none() is None
        )
        receipt = session.get(Receipt, rid)
        assert {li.id for li in receipt.line_items} == {eggs_line_id, milk_line_id}


def test_review_save_adds_and_removes_lines(app_client) -> None:
    """Verify a save that drops a line and appends a new one is applied as the full set."""
    # Given the two-line receipt
    rid = _needs_review_id(app_client)
    eggs_line_id, _milk_line_id = _line_ids(app_client, rid)

    # Given a post keeping only the eggs line and adding one new line (blank line_id)
    data = {
        "chain_name": "Safeway",
        "location": "Main St",
        "purchase_date": "2026-03-05",
        "total": "5.70",
        "currency": "USD",
        "line_id": [str(eggs_line_id), ""],
        "raw_description": ["EGGS LARGE", "BREAD"],
        "canonical_name": ["eggs", "bread"],
        "quantity": ["1", "1"],
        "sold_by": ["item", "item"],
        "measure_unit": ["", ""],
        "size_amount": ["", ""],
        "size_unit": ["", ""],
        "unit_price": ["3.20", "2.50"],
        "line_total": ["3.20", "2.50"],
    }

    # When saving
    response = app_client.post(f"/receipts/{rid}", data=data)

    # Then the milk line is gone, a bread line is added, and the read partial returns
    assert response.status_code == 200
    state = app_client.app.state
    with state.session_factory() as session:
        receipt = session.get(Receipt, rid)
        names = sorted(li.product.canonical_name for li in receipt.line_items)
        assert names == ["bread", "eggs"]


def test_edit_partial_renders_reconcile_hint(app_client) -> None:
    """Verify the edit form renders the live total-reconciliation hint element."""
    # Given the seeded needs_review receipt
    rid = _needs_review_id(app_client)

    # When loading the edit partial
    response = app_client.get(f"/receipts/{rid}/edit")

    # Then the reconciliation status placeholder is present for the client-side script to fill
    assert response.status_code == 200
    assert 'id="reconcile"' in response.text
    assert 'id="reconcile-text"' in response.text


def test_review_save_assigns_category_by_id(app_client) -> None:
    """Verify posting category_id reassigns the product's category by taxonomy pk."""
    # Given the needs_review receipt and the seeded 'produce' category id
    rid = _needs_review_id(app_client)
    eggs_line_id, milk_line_id = _line_ids(app_client, rid)
    state = app_client.app.state
    with state.session_factory() as session:
        produce = session.query(Category).filter_by(name="produce").one()
        produce_id = produce.id

    # Given a payload that posts the produce category_id on the eggs line
    data = {
        "chain_name": "Safeway",
        "location": "Main St",
        "purchase_date": "2026-03-05",
        "total": "5.20",
        "currency": "USD",
        "line_id": [str(eggs_line_id), str(milk_line_id)],
        "raw_description": ["EGGS LARGE", "2% MILK"],
        "canonical_name": ["eggs", "milk"],
        "category_id": [str(produce_id), str(produce_id)],
        "quantity": ["1", "1"],
        "sold_by": ["item", "item"],
        "measure_unit": ["", ""],
        "size_amount": ["", ""],
        "size_unit": ["", ""],
        "unit_price": ["3.20", "2.00"],
        "line_total": ["3.20", "2.00"],
    }

    # When saving
    response = app_client.post(f"/receipts/{rid}", data=data)

    # Then the save succeeds and the eggs product is now in produce
    assert response.status_code == 200
    with state.session_factory() as session:
        eggs_line = session.get(LineItem, eggs_line_id)
        assert eggs_line is not None
        assert eggs_line.product.category is not None
        assert eggs_line.product.category.name == "produce"


def test_receipt_detail_shows_review_reasons(app_client) -> None:
    """Verify a receipt's review reasons render on its detail page."""
    factory = app_client.app.state.session_factory
    # Given a receipt with a review reason
    with factory() as session:
        receipt = session.query(Receipt).first()
        receipt.review_reasons.append(
            ReceiptReviewReason(
                code=ReviewReasonCode.TOTAL_MISMATCH, detail="items sum 1 vs total 2"
            )
        )
        session.commit()
        rid = receipt.id
    # When viewing the receipt detail page
    response = app_client.get(f"/receipts/{rid}")
    # Then the reason and its detail are shown
    assert response.status_code == 200
    assert "items sum 1 vs total 2" in response.text


def test_review_save_invalid_category_id_returns_422(app_client) -> None:
    """Verify saving a receipt with a non-existent category_id re-renders as 422, not 500."""
    # Given the needs_review receipt and a category_id that does not exist
    rid = _needs_review_id(app_client)
    eggs_line_id, milk_line_id = _line_ids(app_client, rid)
    data = {
        "chain_name": "Safeway",
        "location": "Main St",
        "purchase_date": "2026-03-05",
        "total": "5.20",
        "currency": "USD",
        "line_id": [str(eggs_line_id), str(milk_line_id)],
        "raw_description": ["EGGS LARGE", "2% MILK"],
        "canonical_name": ["eggs", "milk"],
        "category_id": ["999999", "999999"],
        "quantity": ["1", "1"],
        "sold_by": ["item", "item"],
        "measure_unit": ["", ""],
        "size_amount": ["", ""],
        "size_unit": ["", ""],
        "unit_price": ["3.20", "2.00"],
        "line_total": ["3.20", "2.00"],
    }

    # When posting the edit
    response = app_client.post(f"/receipts/{rid}", data=data)

    # Then the route returns 422 (the edit form re-render), not a 500
    assert response.status_code == 422
    assert "error" in response.text.lower()


def test_mark_reviewed_clears_review_reasons(app_client) -> None:
    """Verify POST /receipts/{id}/mark-reviewed removes all review reasons from the receipt."""
    state = app_client.app.state
    # Given a needs_review receipt seeded with a review reason
    with state.session_factory() as session:
        receipt = session.query(Receipt).filter_by(status=ReceiptStatus.NEEDS_REVIEW).first()
        assert receipt is not None
        receipt.review_reasons.append(
            ReceiptReviewReason(code=ReviewReasonCode.UNMAPPED_CATEGORY, detail="produce")
        )
        session.commit()
        rid = receipt.id
        reason_detail = "produce"

    # When marking the receipt as reviewed
    response = app_client.post(f"/receipts/{rid}/mark-reviewed")

    # Then it succeeds, the reason text is absent from the response, and no reasons remain
    assert response.status_code == 200
    assert reason_detail not in response.text
    with state.session_factory() as session:
        receipt = session.get(Receipt, rid)
        assert receipt is not None
        assert receipt.status == ReceiptStatus.PARSED
        assert session.query(ReceiptReviewReason).filter_by(receipt_id=rid).count() == 0
