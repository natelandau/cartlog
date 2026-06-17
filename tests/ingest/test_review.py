"""Tests for ingest review-reason checks."""

from __future__ import annotations

from datetime import date

from cartlog.db.models import ReviewReasonCode
from cartlog.ingest.review import build_review_reasons
from cartlog.parsing.schema import ParsedLineItem, ParsedReceipt


def _receipt(*, total: float, lines: list[tuple[float, float]], confidence: float) -> ParsedReceipt:
    return ParsedReceipt(
        store_name="S",
        purchase_date=date(2026, 1, 1),
        currency="USD",
        total=total,
        confidence=confidence,
        line_items=[
            ParsedLineItem(
                raw_description="x",
                canonical_name="x",
                category="produce",
                quantity=1,
                unit_price=up,
                line_total=lt,
            )
            for up, lt in lines
        ],
    )


def test_low_confidence_flagged() -> None:
    """Verify a receipt with confidence below the threshold gets a LOW_CONFIDENCE reason.

    Given a receipt with confidence 0.4 and a threshold of 0.7.
    When build_review_reasons is called.
    Then LOW_CONFIDENCE is present in the returned reason codes.
    """
    # Given a low-confidence receipt
    parsed = _receipt(total=2.0, lines=[(1.0, 1.0), (1.0, 1.0)], confidence=0.4)

    # When checking for review reasons
    reasons = build_review_reasons(parsed, unmapped=[], confidence_threshold=0.7, tolerance=0.05)
    codes = {r.code for r in reasons}

    # Then LOW_CONFIDENCE is flagged
    assert ReviewReasonCode.LOW_CONFIDENCE in codes


def test_total_mismatch_flagged() -> None:
    """Verify a receipt whose line items do not sum to the grand total gets a TOTAL_MISMATCH reason.

    Given a receipt with total 5.0 but line items summing to 2.0 (beyond the 0.05 tolerance).
    When build_review_reasons is called.
    Then TOTAL_MISMATCH is present in the returned reason codes.
    """
    # Given a receipt with mismatched totals
    parsed = _receipt(total=5.0, lines=[(1.0, 1.0), (1.0, 1.0)], confidence=0.95)

    # When checking for review reasons
    reasons = build_review_reasons(parsed, unmapped=[], confidence_threshold=0.7, tolerance=0.05)
    codes = {r.code for r in reasons}

    # Then TOTAL_MISMATCH is flagged
    assert ReviewReasonCode.TOTAL_MISMATCH in codes


def test_unmapped_flagged_with_detail() -> None:
    """Verify unmapped categories produce a single UNMAPPED_CATEGORY reason with names in detail.

    Given a clean receipt but with two unmapped category strings.
    When build_review_reasons is called.
    Then exactly one UNMAPPED_CATEGORY reason exists and the detail contains the first name.
    """
    # Given a receipt with unmapped categories
    parsed = _receipt(total=2.0, lines=[(1.0, 1.0), (1.0, 1.0)], confidence=0.95)

    # When checking for review reasons
    reasons = build_review_reasons(
        parsed, unmapped=["mystery", "weird"], confidence_threshold=0.7, tolerance=0.05
    )
    unmapped = [r for r in reasons if r.code == ReviewReasonCode.UNMAPPED_CATEGORY]

    # Then one reason exists and contains the unmapped name
    assert len(unmapped) == 1
    detail = unmapped[0].detail
    assert detail is not None
    assert "mystery" in detail


def test_no_line_items_flagged() -> None:
    """Verify a receipt with no line items gets NO_LINE_ITEMS and not TOTAL_MISMATCH.

    Given a receipt with empty line_items, a non-zero total, high confidence, and no unmapped.
    When build_review_reasons is called.
    Then NO_LINE_ITEMS is present and TOTAL_MISMATCH is absent.
    """
    # Given a receipt with no line items but a non-zero total
    parsed = ParsedReceipt(
        store_name="S",
        purchase_date=date(2026, 1, 1),
        currency="USD",
        total=9.99,
        confidence=0.95,
        line_items=[],
    )

    # When checking for review reasons
    reasons = build_review_reasons(parsed, unmapped=[], confidence_threshold=0.7, tolerance=0.05)
    codes = {r.code for r in reasons}

    # Then NO_LINE_ITEMS fires but TOTAL_MISMATCH does not
    assert ReviewReasonCode.NO_LINE_ITEMS in codes
    assert ReviewReasonCode.TOTAL_MISMATCH not in codes


def test_confidence_at_threshold_not_flagged() -> None:
    """Verify a receipt with confidence exactly at the threshold is not flagged LOW_CONFIDENCE.

    Given a receipt with confidence 0.7 and a threshold of 0.7.
    When build_review_reasons is called.
    Then LOW_CONFIDENCE is absent.
    """
    # Given a receipt whose confidence equals the threshold exactly
    parsed = _receipt(total=2.0, lines=[(1.0, 1.0), (1.0, 1.0)], confidence=0.7)

    # When checking for review reasons
    reasons = build_review_reasons(parsed, unmapped=[], confidence_threshold=0.7, tolerance=0.05)
    codes = {r.code for r in reasons}

    # Then LOW_CONFIDENCE is not flagged (boundary is exclusive)
    assert ReviewReasonCode.LOW_CONFIDENCE not in codes


def test_total_mismatch_at_tolerance_not_flagged() -> None:
    """Verify a receipt whose total differs from item sum by exactly the tolerance is not flagged.

    Given a receipt with total 2.05 and line items summing to 2.00, with tolerance 0.05.
    When build_review_reasons is called.
    Then TOTAL_MISMATCH is absent.
    """
    # Given a receipt where the difference equals the tolerance exactly
    parsed = _receipt(total=2.05, lines=[(1.0, 1.0), (1.0, 1.0)], confidence=0.95)

    # When checking for review reasons
    reasons = build_review_reasons(parsed, unmapped=[], confidence_threshold=0.7, tolerance=0.05)
    codes = {r.code for r in reasons}

    # Then TOTAL_MISMATCH is not flagged (boundary is exclusive)
    assert ReviewReasonCode.TOTAL_MISMATCH not in codes


def test_clean_receipt_has_no_reasons() -> None:
    """Verify a clean receipt with high confidence and matching totals returns no reasons.

    Given a receipt with confidence 0.95, line items summing exactly to the total, and no unmapped.
    When build_review_reasons is called.
    Then an empty list is returned.
    """
    # Given a clean receipt
    parsed = _receipt(total=2.0, lines=[(1.0, 1.0), (1.0, 1.0)], confidence=0.95)

    # When checking for review reasons
    # Then no reasons are returned
    assert build_review_reasons(parsed, unmapped=[], confidence_threshold=0.7, tolerance=0.05) == []
