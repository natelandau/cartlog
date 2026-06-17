"""Tests for the taxonomy model constraints and helpers."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from cartlog.db.models import Category, ReceiptReviewReason, ReviewReasonCode


def test_duplicate_category_name_rejected(session) -> None:
    """Verify two categories with the same name are rejected by the unique constraint."""
    # Given a category
    session.add(Category(name="dairy"))
    session.flush()
    # When adding another category with the same name
    session.add(Category(name="dairy"))
    # Then the unique constraint rejects it
    with pytest.raises(IntegrityError):
        session.flush()


def test_review_reason_cascades_with_receipt(session, sample_parsed_receipt) -> None:
    """Verify deleting a receipt removes its review reasons."""
    from cartlog.ingest.persistence import persist_receipt  # noqa: PLC0415

    receipt, _ = persist_receipt(
        session,
        sample_parsed_receipt,
        image_path="/x.png",
        source="test",
        status="needs_review",
        raw_json="{}",
    )
    receipt.review_reasons.append(
        ReceiptReviewReason(code=ReviewReasonCode.LOW_CONFIDENCE, detail="0.40")
    )
    session.commit()
    assert session.query(ReceiptReviewReason).count() == 1
    session.delete(receipt)
    session.commit()
    assert session.query(ReceiptReviewReason).count() == 0
