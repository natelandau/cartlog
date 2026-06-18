"""Process a claimed ingestion job: parse its stored file and persist the receipt."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai.usage import RunUsage

from cartlog.categories.reclassify import (
    DEFAULT_MAX_RECLASSIFY_ATTEMPTS,
    reclassify_receipt,
    unmapped_categories_for,
)
from cartlog.categories.service import CategoryService
from cartlog.db.models import JobStep, ReceiptStatus
from cartlog.ingest.cost import record_classify_cost, record_parse_cost
from cartlog.ingest.persistence import persist_receipt
from cartlog.ingest.queue import complete_job, fail_job, set_job_step
from cartlog.ingest.review import build_review_reasons
from cartlog.parsing.pricing import estimate_cost

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from cartlog.db.models import IngestionJob, Receipt
    from cartlog.parsing.category_classifier import CategoryClassifier
    from cartlog.parsing.parser import ReceiptParser

logger = logging.getLogger(__name__)


def _notify_step(on_step: Callable[[JobStep], None] | None, step: JobStep) -> None:
    """Report a step to a progress callback, swallowing display errors.

    The callback drives optional UI (such as the CLI checklist); a failure there must
    never abort parsing or consume the job's retry budget.
    """
    if on_step is None:
        return
    with contextlib.suppress(Exception):
        on_step(step)


def _reclassify_and_recompute_unmapped(
    session: Session,
    receipt: Receipt,
    classifier: CategoryClassifier,
    *,
    max_attempts: int,
    usage: RunUsage | None = None,
) -> list[str]:
    """Reclassify the receipt's Uncategorized products, then recompute the unmapped guesses.

    A reclassification failure (e.g. a model/network error) must not fail the receipt: the
    items simply stay Uncategorized and are flagged for manual review, which is the intended
    fallback. The flush ensures any reclassifier rescues are reflected before recomputing.
    """
    uncategorized_id = CategoryService(session).ensure_uncategorized().id
    try:
        reclassify_receipt(session, receipt, classifier, max_attempts=max_attempts, usage=usage)
    except Exception:  # noqa: BLE001  # reclassification is best-effort; never fail the receipt
        logger.warning(
            "Reclassification failed for receipt %s; leaving items uncategorized for review",
            receipt.id,
            exc_info=True,
        )
    session.flush()
    return unmapped_categories_for(receipt, uncategorized_id)


def process_job(  # noqa: PLR0913
    session: Session,
    job: IngestionJob,
    *,
    parser: ReceiptParser,
    review_confidence_threshold: float,
    total_mismatch_tolerance: float,
    max_retries: int,
    retry_backoff_base_seconds: float = 0.0,
    classifier: CategoryClassifier | None = None,
    max_reclassify_attempts: int = DEFAULT_MAX_RECLASSIFY_ATTEMPTS,
    on_step: Callable[[JobStep], None] | None = None,
    parse_model: str | None = None,
    classify_model: str | None = None,
) -> Receipt | None:
    """Parse a claimed job's stored file and persist the result. Commits.

    On success the job is marked done and linked to the new receipt. On failure the
    transaction is rolled back and the job is re-queued (or failed once retries are
    exhausted); the stored file is left in place so a retry can re-parse it.

    Status is determined from structured review reasons: a receipt accumulates zero or more
    ReceiptReviewReason rows and is flagged needs_review iff at least one reason fires.
    Reasons include low extraction confidence, line-item total mismatch, and unmapped categories.

    Args:
        session: SQLAlchemy session; this function commits.
        job: A job already claimed (status 'parsing').
        parser: ReceiptParser implementation to extract structured data from the file.
        review_confidence_threshold: Confidence scores below this trigger a LOW_CONFIDENCE reason.
        total_mismatch_tolerance: Maximum allowed absolute difference between line-item sum and
            grand total before a TOTAL_MISMATCH reason fires.
        max_retries: Retry budget before the job is permanently marked 'failed'.
        retry_backoff_base_seconds: Base delay for exponential backoff before a retry.
        classifier: Optional focused classifier; when provided, the receipt's Uncategorized
            products are re-homed before review reasons are computed. None disables the pass.
        max_reclassify_attempts: Per-product cap on LLM reclassification attempts.
        on_step: Optional callback invoked with each JobStep as parsing advances, for
            progress display. The worker omits it.
        parse_model: Provider-prefixed model id used to price the parse call (e.g.
            "anthropic:claude-opus-4-8"). None skips cost estimation but still records token counts.
        classify_model: Provider-prefixed model id used to price the classify call. None skips cost
            estimation but still records token counts.

    Returns:
        The committed Receipt on success, or None if the job failed.
    """
    try:
        parse_usage = RunUsage()
        classify_usage = RunUsage()
        set_job_step(session, job, JobStep.EXTRACTING)
        _notify_step(on_step, JobStep.EXTRACTING)
        parsed = parser.parse(Path(job.image_path), usage=parse_usage)
        # Record-on-spend: write the parse cost to the durable ledger before the SAVING step,
        # which may still fail. The ledger row outlives the job (and any later reparse/delete).
        parse_cost = estimate_cost(parse_model, parse_usage) if parse_model else None
        cost_event = record_parse_cost(
            session,
            job_id=job.id,
            input_tokens=parse_usage.input_tokens,
            output_tokens=parse_usage.output_tokens,
            model=parse_model,
            cost=parse_cost,
        )
        set_job_step(session, job, JobStep.SAVING)
        _notify_step(on_step, JobStep.SAVING)
        # Persist first (categories resolve here, yielding the unmapped list), then decide
        # status from the full reason set. The transaction is still open, so mutating the
        # just-flushed receipt's status and reasons commits atomically via complete_job.
        receipt, unmapped = persist_receipt(
            session,
            parsed,
            image_path=job.image_path,
            source=job.source,
            status=ReceiptStatus.PARSED,
            raw_json=parsed.model_dump_json(),
        )
        # Second pass: re-home this receipt's Uncategorized products before deciding review
        # status, so a rescued line no longer trips UNMAPPED_CATEGORY. Recompute the unmapped
        # list from what genuinely remains after the sweep.
        if classifier is not None:
            unmapped = _reclassify_and_recompute_unmapped(
                session,
                receipt,
                classifier,
                max_attempts=max_reclassify_attempts,
                usage=classify_usage,
            )
            classify_cost = (
                estimate_cost(classify_model, classify_usage) if classify_model else None
            )
            record_classify_cost(
                session,
                cost_event,
                input_tokens=classify_usage.input_tokens,
                output_tokens=classify_usage.output_tokens,
                model=classify_model,
                cost=classify_cost,
            )
        reasons = build_review_reasons(
            parsed,
            unmapped=unmapped,
            confidence_threshold=review_confidence_threshold,
            tolerance=total_mismatch_tolerance,
        )
        if reasons:
            receipt.status = ReceiptStatus.NEEDS_REVIEW
            receipt.review_reasons.extend(reasons)
            session.flush()
        # persist_receipt already flushed, so receipt.id is populated for the link below.
        complete_job(session, job, receipt=receipt)
    except Exception as exc:  # noqa: BLE001  # any parse/persist error re-queues the job
        session.rollback()
        fail_job(
            session,
            job,
            error=str(exc),
            max_retries=max_retries,
            retry_backoff_base_seconds=retry_backoff_base_seconds,
        )
        return None
    return receipt
