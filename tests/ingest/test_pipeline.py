"""Tests for processing a claimed ingestion job into a persisted receipt."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai.usage import RunUsage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cartlog.categories.service import CategoryService
from cartlog.db.base import Base
from cartlog.db.models import (
    IngestionJob,
    JobStatus,
    JobStep,
    ParseCostEvent,
    Product,
    Receipt,
    ReviewReasonCode,
)
from cartlog.ingest.pipeline import process_job
from cartlog.ingest.queue import enqueue_job
from cartlog.parsing.pricing import estimate_cost
from cartlog.parsing.schema import ParsedLineItem, ParsedReceipt
from tests.conftest import FakeReceiptParser

if TYPE_CHECKING:
    from collections.abc import Callable

    from cartlog.parsing.schema import ParsedReceipt as ParsedReceiptAlias


def _enqueue(session, tmp_path, *, name="scan.png", data=b"\x89PNG fake"):
    """Enqueue a job for a freshly written source file and return it."""
    src = tmp_path / name
    src.write_bytes(data)
    return enqueue_job(session, src_path=src, source="cli", storage_dir=tmp_path / "storage")


def test_process_job_persists_receipt_and_marks_done(session, fake_parser, tmp_path):
    """Verify a successful parse persists a receipt and marks the job done."""
    # Given categories exist so nothing is unmapped and the receipt gets status 'parsed'
    svc = CategoryService(session)
    svc.ensure_uncategorized()
    svc.create_category(name="dairy & eggs")
    svc.create_category(name="produce")
    session.flush()

    # And a pending job
    job = _enqueue(session, tmp_path)

    # When processing it
    receipt = process_job(
        session,
        job,
        parser=fake_parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
    )

    # Then a parsed receipt persists and the job is done and linked
    assert receipt is not None
    assert receipt.status == "parsed"
    assert fake_parser.calls == [Path(job.image_path)]
    assert job.status == JobStatus.DONE
    assert job.receipt_id == receipt.id
    assert session.query(Receipt).count() == 1


def test_process_job_flags_low_confidence_for_review(session, tmp_path, sample_parsed_receipt):
    """Verify a low-confidence parse ends DONE with needs_review status and exactly one LOW_CONFIDENCE reason.

    Given taxonomy is seeded so categories are fully mapped.
    And a parser returning a receipt with confidence 0.4 (below the 0.7 threshold).
    When processing the job.
    Then the job is DONE, receipt status is needs_review, and the only reason is LOW_CONFIDENCE.
    """
    # Given taxonomy seeded so UNMAPPED_CATEGORY cannot co-fire
    svc = CategoryService(session)
    svc.ensure_uncategorized()
    svc.create_category(name="dairy & eggs")
    svc.create_category(name="produce")
    session.flush()

    # And a low-confidence parse result and a pending job
    low = sample_parsed_receipt.model_copy(update={"confidence": 0.4})
    parser = FakeReceiptParser(low)
    job = _enqueue(session, tmp_path)

    # When processing with a 0.7 threshold
    receipt = process_job(
        session,
        job,
        parser=parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
    )

    # Then the job is done, receipt is needs_review, and the reason set is exactly {LOW_CONFIDENCE}
    assert receipt is not None
    assert job.status == JobStatus.DONE
    assert receipt.status == "needs_review"
    codes = {r.code for r in receipt.review_reasons}
    assert codes == {ReviewReasonCode.LOW_CONFIDENCE}


def test_process_job_requeues_on_failure_and_keeps_file(session, tmp_path):
    """Verify a parse failure re-queues the job, persists no receipt, and keeps the file."""

    # Given a parser that always raises and a pending job
    class FailingParser:
        def parse(self, file_path: Path, *, usage=None):
            msg = "boom"
            raise ValueError(msg)

    job = _enqueue(session, tmp_path)

    # When processing it
    result = process_job(
        session,
        job,
        parser=FailingParser(),
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
    )

    # Then no receipt persists, the job is re-queued, and the stored file survives for retry
    assert result is None
    assert job.status == JobStatus.PENDING
    assert job.retry_count == 1
    assert job.last_error is not None
    assert Path(job.image_path).exists()
    assert session.query(Receipt).count() == 0


def test_process_job_marks_failed_when_retries_exhausted(session, tmp_path):
    """Verify a job with no retries left is marked failed on parse error."""

    # Given a failing parser and a job that has already used its retry budget
    class FailingParser:
        def parse(self, file_path: Path, *, usage=None):
            msg = "boom"
            raise ValueError(msg)

    job = _enqueue(session, tmp_path)

    # When processing with max_retries=0
    result = process_job(
        session,
        job,
        parser=FailingParser(),
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=0,
    )

    # Then the job is permanently failed and no receipt exists
    assert result is None
    assert job.status == JobStatus.FAILED
    assert session.query(Receipt).count() == 0


def test_process_job_commits_extracting_step_before_parse(tmp_path, sample_parsed_receipt):
    """Verify the job's step is committed as 'extracting' by the time parse() runs.

    Uses a StaticPool in-memory engine so a second session sees the committed step the way a
    web request's session would.
    """
    # Given a shared in-memory database and a job
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    class StepObservingParser:
        """Records the job's committed step at the moment parse() is invoked."""

        def __init__(self, factory: Callable[[], Session], result: ParsedReceiptAlias) -> None:
            self._factory = factory
            self._result = result
            self.observed_step: str | None = None  # None until parse() runs

        def parse(self, file_path: Path, *, usage=None) -> ParsedReceiptAlias:
            with self._factory() as other:
                job = other.query(IngestionJob).first()
                self.observed_step = job.step if job is not None else None
            return self._result

    parser = StepObservingParser(factory, sample_parsed_receipt)
    try:
        with factory() as session:
            job = _enqueue(session, tmp_path)
            # When processing the job
            process_job(
                session,
                job,
                parser=parser,
                review_confidence_threshold=0.7,
                total_mismatch_tolerance=0.05,
                max_retries=3,
            )
        # Then a second session saw 'extracting' committed during the parse call
        assert parser.observed_step == JobStep.EXTRACTING
    finally:
        engine.dispose()


def test_process_job_clears_step_on_done(session, fake_parser, tmp_path):
    """Verify a completed job carries no sub-step."""
    # Given a pending job
    job = _enqueue(session, tmp_path)

    # When it processes successfully
    process_job(
        session,
        job,
        parser=fake_parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
    )

    # Then the step is cleared
    assert job.status == JobStatus.DONE
    assert job.step is None


def test_process_job_clears_step_on_failure(session, tmp_path):
    """Verify a failed job carries no sub-step."""

    class FailingParser:
        def parse(self, file_path, *, usage=None):
            msg = "boom"
            raise ValueError(msg)

    # Given a pending job and a failing parser
    job = _enqueue(session, tmp_path)

    # When processing fails
    process_job(
        session,
        job,
        parser=FailingParser(),
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
    )

    # Then no stale step remains
    assert job.step is None


def test_process_job_reports_steps_via_callback(session, fake_parser, tmp_path):
    """Verify process_job reports extracting then saving through the on_step callback."""
    # Given a pending job and a recording callback
    job = _enqueue(session, tmp_path)
    seen: list[str] = []

    def record(step: JobStep) -> None:
        seen.append(step)

    # When processing with an on_step callback
    process_job(
        session,
        job,
        parser=fake_parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
        on_step=record,
    )

    # Then both stages were reported in order
    assert seen == [JobStep.EXTRACTING, JobStep.SAVING]


def test_process_job_callback_stops_at_failure(session, tmp_path):
    """Verify a parse failure reports only the extracting stage, not saving."""

    class FailingParser:
        def parse(self, file_path, *, usage=None):
            msg = "boom"
            raise ValueError(msg)

    # Given a pending job, a failing parser, and a recording callback
    job = _enqueue(session, tmp_path)
    seen: list[str] = []

    def record(step: JobStep) -> None:
        seen.append(step)

    # When processing fails during parse
    process_job(
        session,
        job,
        parser=FailingParser(),
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
        on_step=record,
    )

    # Then only the extracting stage was reported
    assert seen == [JobStep.EXTRACTING]


def test_process_job_clean_receipt_has_parsed_status_and_no_reasons(
    session, tmp_path, sample_parsed_receipt
):
    """Verify a fully-mapped, high-confidence, totals-matching receipt gets status parsed with no review reasons.

    Given categories 'dairy & eggs' and produce exist in the taxonomy.
    And a receipt with confidence 0.95, line items summing exactly to 6.96, and mapped categories.
    When processing the job.
    Then status is parsed and review_reasons is empty.
    """
    # Given the taxonomy categories for the sample receipt are seeded
    svc = CategoryService(session)
    svc.ensure_uncategorized()
    svc.create_category(name="dairy & eggs")
    svc.create_category(name="produce")
    session.flush()

    # And a job using the sample receipt (confidence=0.95, total=6.96, lines sum to 6.96)
    parser = FakeReceiptParser(sample_parsed_receipt)
    job = _enqueue(session, tmp_path)

    # When processing the job
    receipt = process_job(
        session,
        job,
        parser=parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
    )

    # Then the receipt is parsed with zero review reasons
    assert receipt is not None
    assert receipt.status == "parsed"
    assert receipt.review_reasons == []


def test_process_job_total_mismatch_creates_review_reason(session, tmp_path):
    """Verify a receipt whose line items diverge from the grand total gets exactly one TOTAL_MISMATCH reason.

    Given taxonomy is seeded so UNMAPPED_CATEGORY cannot co-fire.
    And a receipt with total 10.00 but line items summing to 2.00.
    When processing the job.
    Then the receipt status is needs_review and the reason set is exactly {TOTAL_MISMATCH}.
    """
    # Given taxonomy seeded so UNMAPPED_CATEGORY cannot co-fire
    svc = CategoryService(session)
    svc.ensure_uncategorized()
    svc.create_category(name="produce")
    session.flush()

    # And a receipt with a total mismatch and high confidence
    mismatched = ParsedReceipt(
        store_name="Test",
        purchase_date=date(2026, 1, 1),
        currency="USD",
        total=10.0,
        confidence=0.95,
        line_items=[
            ParsedLineItem(
                raw_description="item",
                canonical_name="item",
                category="produce",
                quantity=1,
                unit_price=1.0,
                line_total=1.0,
            ),
            ParsedLineItem(
                raw_description="item2",
                canonical_name="item2",
                category="produce",
                quantity=1,
                unit_price=1.0,
                line_total=1.0,
            ),
        ],
    )
    parser = FakeReceiptParser(mismatched)
    job = _enqueue(session, tmp_path)

    # When processing the job
    receipt = process_job(
        session,
        job,
        parser=parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
    )

    # Then the receipt is needs_review with the reason set exactly {TOTAL_MISMATCH}
    assert receipt is not None
    assert receipt.status == "needs_review"
    codes = {r.code for r in receipt.review_reasons}
    assert codes == {ReviewReasonCode.TOTAL_MISMATCH}


def test_process_job_unmapped_category_flags_review(session, tmp_path, sample_parsed_receipt):
    """Verify a receipt with unmapped categories gets exactly one UNMAPPED_CATEGORY reason.

    Given no taxonomy is seeded (so sample receipt categories are unmapped).
    And a receipt with high confidence and matching totals.
    When processing the job.
    Then the receipt status is needs_review and the reason set is exactly {UNMAPPED_CATEGORY}.
    """
    # Given only Uncategorized exists; no 'dairy & eggs' or produce seeded so categories are unmapped
    svc = CategoryService(session)
    svc.ensure_uncategorized()
    session.flush()

    # And a high-confidence, totals-matching parse (sample_parsed_receipt sums to 6.96 = total)
    parser = FakeReceiptParser(sample_parsed_receipt)
    job = _enqueue(session, tmp_path)

    # When processing the job
    receipt = process_job(
        session,
        job,
        parser=parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
    )

    # Then the receipt is needs_review with the reason set exactly {UNMAPPED_CATEGORY}
    assert receipt is not None
    assert receipt.status == "needs_review"
    codes = {r.code for r in receipt.review_reasons}
    assert codes == {ReviewReasonCode.UNMAPPED_CATEGORY}


class _FakeClassifier:
    """A classifier test double returning canned answers by canonical name."""

    def __init__(self, answers: dict[str, str | None]) -> None:
        self.answers = answers

    def classify(self, products, *, usage=None):  # duck-typed double
        return {p.canonical_name: self.answers.get(p.canonical_name) for p in products}


def test_process_job_auto_reclassifies_uncategorized(session, fake_parser, tmp_path):
    """Verify ingestion re-homes a miscategorized line so it is not flagged for review."""
    # Given a taxonomy where eggs maps but the parser's 'produce' guess does not, plus a
    # classifier that knows bananas are fruits
    svc = CategoryService(session)
    svc.create_category(name="dairy & eggs")
    svc.create_category(name="fruits")
    session.flush()
    job = _enqueue(session, tmp_path)
    classifier = _FakeClassifier({"bananas": "fruits"})

    # When processing the job with the classifier wired in
    receipt = process_job(
        session,
        job,
        parser=fake_parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
        classifier=classifier,
    )

    # Then bananas is re-homed to fruits and the receipt is not flagged UNMAPPED_CATEGORY
    assert receipt is not None
    bananas = session.query(Product).filter_by(canonical_name="bananas").one()
    assert bananas.category is not None
    assert bananas.category.name == "fruits"
    codes = {r.code for r in receipt.review_reasons}
    assert ReviewReasonCode.UNMAPPED_CATEGORY not in codes
    assert receipt.status == "parsed"


def test_process_job_reclassify_failure_leaves_item_for_review(session, fake_parser, tmp_path):
    """Verify a classifier error does not fail the receipt; the item is flagged for review."""
    # Given a taxonomy where 'produce' is unmapped and a classifier that raises
    svc = CategoryService(session)
    svc.create_category(name="dairy & eggs")
    session.flush()
    job = _enqueue(session, tmp_path)

    class _BoomClassifier:
        def classify(self, products, *, usage=None):
            msg = "model unavailable"
            raise RuntimeError(msg)

    # When processing the job and the reclassification call fails
    receipt = process_job(
        session,
        job,
        parser=fake_parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
        classifier=_BoomClassifier(),
    )

    # Then the receipt still persists and the unmapped item is flagged for manual review
    assert receipt is not None
    assert job.status == JobStatus.DONE
    bananas = session.query(Product).filter_by(canonical_name="bananas").one()
    assert bananas.category is not None
    assert bananas.category.name == "Uncategorized"
    codes = {r.code for r in receipt.review_reasons}
    assert ReviewReasonCode.UNMAPPED_CATEGORY in codes


def test_process_job_records_parse_usage_and_cost(session, tmp_path):
    """Verify a successful parse writes a cost event with parse tokens and a positive cost."""
    # Given a claimed job and a parser that reports token usage into the accumulator
    job = _enqueue(session, tmp_path)

    class UsageParser:
        def parse(self, file_path, *, usage=None):
            if usage is not None:
                usage.input_tokens += 1500
                usage.output_tokens += 400
            return ParsedReceipt(
                store_name="Test Store",
                purchase_date=date(2026, 1, 1),
                currency="USD",
                total=1.0,
                confidence=0.95,
                line_items=[
                    ParsedLineItem(
                        raw_description="item",
                        canonical_name="item",
                        category="",
                        quantity=1,
                        unit_price=1.0,
                        line_total=1.0,
                    )
                ],
            )

    # When processing the job with a known parse model
    process_job(
        session,
        job,
        parser=UsageParser(),
        review_confidence_threshold=0.0,
        total_mismatch_tolerance=1.0,
        max_retries=0,
        parse_model="anthropic:claude-opus-4-8",
    )

    # Then a cost event captured the parse usage and a positive cost
    event = session.query(ParseCostEvent).one()
    assert event.parse_input_tokens == 1500
    assert event.parse_output_tokens == 400
    assert event.parse_model == "anthropic:claude-opus-4-8"
    assert event.estimated_cost_usd is not None
    assert event.estimated_cost_usd > 0


def test_process_job_persists_parse_cost_even_when_saving_fails(session, tmp_path, monkeypatch):
    """Verify the parse cost event survives a failure in a later step (record-on-spend)."""
    # Given a parser that succeeds and reports usage, but persistence then fails
    job = _enqueue(session, tmp_path)

    class UsageParser:
        def parse(self, file_path, *, usage=None):
            if usage is not None:
                usage.input_tokens += 900
            return ParsedReceipt(
                store_name="Test Store",
                purchase_date=date(2026, 1, 1),
                currency="USD",
                total=1.0,
                confidence=0.95,
                line_items=[
                    ParsedLineItem(
                        raw_description="item",
                        canonical_name="item",
                        category="",
                        quantity=1,
                        unit_price=1.0,
                        line_total=1.0,
                    )
                ],
            )

    def boom(*args: object, **kwargs: object) -> None:
        msg = "save failed"
        raise RuntimeError(msg)

    monkeypatch.setattr("cartlog.ingest.pipeline.persist_receipt", boom)

    # When processing the job
    result = process_job(
        session,
        job,
        parser=UsageParser(),
        review_confidence_threshold=0.0,
        total_mismatch_tolerance=1.0,
        max_retries=0,
        parse_model="anthropic:claude-opus-4-8",
    )

    # Then the job failed but the spent parse tokens were committed to the ledger first
    assert result is None
    event = session.query(ParseCostEvent).one()
    assert event.parse_input_tokens == 900


def test_process_job_records_classify_usage_and_sums_cost(session, tmp_path):
    """Verify the classify pass adds its tokens and cost on top of the parse cost."""
    # Given a parser and a classifier that each report usage; the receipt has a product whose
    # category ("mystery-category") is not in the seeded taxonomy, so it lands in Uncategorized
    # and becomes eligible for the classify pass
    CategoryService(session).ensure_uncategorized()
    session.flush()
    job = _enqueue(session, tmp_path)

    class UsageParser:
        def parse(self, file_path, *, usage=None):
            if usage is not None:
                usage.input_tokens += 1000
                usage.output_tokens += 200
            return ParsedReceipt(
                store_name="Test Store",
                purchase_date=date(2026, 1, 1),
                currency="USD",
                total=1.0,
                confidence=0.95,
                line_items=[
                    ParsedLineItem(
                        raw_description="widget",
                        canonical_name="widget",
                        category="mystery-category",
                        quantity=1,
                        unit_price=1.0,
                        line_total=1.0,
                    )
                ],
            )

    class UsageClassifier:
        def classify(self, products, *, usage=None):
            if usage is not None:
                usage.input_tokens += 300
                usage.output_tokens += 50
            return {}

    # When processing with both models known
    process_job(
        session,
        job,
        parser=UsageParser(),
        review_confidence_threshold=0.0,
        total_mismatch_tolerance=1.0,
        max_retries=0,
        classifier=UsageClassifier(),
        parse_model="anthropic:claude-opus-4-8",
        classify_model="anthropic:claude-haiku-4-5",
    )

    # Then the cost event records classify tokens and a total exceeding the parse-only cost
    event = session.query(ParseCostEvent).one()
    assert event.classify_input_tokens == 300
    assert event.classify_output_tokens == 50
    assert event.classify_model == "anthropic:claude-haiku-4-5"
    parse_only = estimate_cost(
        model="anthropic:claude-opus-4-8", usage=RunUsage(input_tokens=1000, output_tokens=200)
    )
    assert event.estimated_cost_usd > parse_only
