"""Independent checks that decide whether a parsed receipt needs human review."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from cartlog.db.models import ReceiptReviewReason, ReviewReasonCode

if TYPE_CHECKING:
    from cartlog.parsing.schema import ParsedReceipt


def build_review_reasons(
    parsed: ParsedReceipt,
    *,
    unmapped: list[str],
    confidence_threshold: float,
    tolerance: float,
) -> list[ReceiptReviewReason]:
    """Return one ReceiptReviewReason per check that fires (empty list means clean).

    Each check is independent so a receipt can carry several reasons at once. Reasons are
    detached (no receipt_id yet); the pipeline appends them to the receipt before commit.

    Args:
        parsed: The structured receipt produced by the parser.
        unmapped: Category strings that fell through to Uncategorized during persistence.
        confidence_threshold: Confidence scores below this value trigger LOW_CONFIDENCE.
        tolerance: Maximum allowed absolute difference between line-item sum and grand total.

    Returns:
        A list of ReceiptReviewReason instances (detached from any session).
    """
    reasons: list[ReceiptReviewReason] = []

    if parsed.confidence < confidence_threshold:
        reasons.append(
            ReceiptReviewReason(
                code=ReviewReasonCode.LOW_CONFIDENCE,
                detail=f"extraction confidence {parsed.confidence:.2f} < {confidence_threshold:.2f}",
            )
        )

    if not parsed.line_items:
        reasons.append(
            ReceiptReviewReason(
                code=ReviewReasonCode.NO_LINE_ITEMS,
                detail="the parser extracted no line items",
            )
        )
    else:
        line_sum = sum((Decimal(str(li.line_total)) for li in parsed.line_items), Decimal(0))
        grand_total = Decimal(str(parsed.total))
        if abs(line_sum - grand_total) > Decimal(str(tolerance)):
            reasons.append(
                ReceiptReviewReason(
                    code=ReviewReasonCode.TOTAL_MISMATCH,
                    detail=f"items sum {line_sum} vs total {grand_total}",
                )
            )

    if unmapped:
        reasons.append(
            ReceiptReviewReason(
                code=ReviewReasonCode.UNMAPPED_CATEGORY,
                detail="unmapped: " + ", ".join(unmapped),
            )
        )

    return reasons
