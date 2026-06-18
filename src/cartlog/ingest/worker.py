"""Background worker that drains the ingestion job queue."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from cartlog.ingest.pipeline import process_job
from cartlog.ingest.queue import claim_next_job, reap_stale_jobs

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.orm import Session, sessionmaker

    from cartlog.config import Settings
    from cartlog.parsing.category_classifier import CategoryClassifier
    from cartlog.parsing.parser import ReceiptParser


def run_once(  # noqa: PLR0913 - forwards settings-derived knobs to process_job
    session_factory: sessionmaker[Session],
    *,
    parser: ReceiptParser,
    review_confidence_threshold: float,
    total_mismatch_tolerance: float,
    max_retries: int,
    retry_backoff_base_seconds: float = 0.0,
    classifier: CategoryClassifier | None = None,
    max_reclassify_attempts: int = 2,
    parse_model: str | None = None,
    classify_model: str | None = None,
) -> bool:
    """Claim and process a single job in its own session/transaction.

    Each job gets a fresh session so a failure can never poison the next one.

    Args:
        session_factory: Factory yielding sessions bound to the application database.
        parser: ReceiptParser used to process the claimed job.
        review_confidence_threshold: Threshold forwarded to process_job.
        total_mismatch_tolerance: Tolerance forwarded to process_job for total mismatch checks.
        max_retries: Retry budget forwarded to process_job.
        retry_backoff_base_seconds: Base delay for exponential backoff forwarded to process_job.
        classifier: Optional focused classifier forwarded to process_job for auto-reclassification.
        max_reclassify_attempts: Per-product LLM reclassification cap forwarded to process_job.
        parse_model: Provider-prefixed model id forwarded to process_job for cost tracking.
        classify_model: Provider-prefixed model id forwarded to process_job for cost tracking.

    Returns:
        True if a job was processed, False if the queue was empty.
    """
    with session_factory() as session:
        job = claim_next_job(session)
        if job is None:
            return False
        process_job(
            session,
            job,
            parser=parser,
            review_confidence_threshold=review_confidence_threshold,
            total_mismatch_tolerance=total_mismatch_tolerance,
            max_retries=max_retries,
            retry_backoff_base_seconds=retry_backoff_base_seconds,
            classifier=classifier,
            max_reclassify_attempts=max_reclassify_attempts,
            parse_model=parse_model,
            classify_model=classify_model,
        )
        return True


def run_worker(  # noqa: PLR0913 - top-level entrypoint forwarding settings-derived knobs
    session_factory: sessionmaker[Session],
    *,
    parser: ReceiptParser,
    review_confidence_threshold: float,
    total_mismatch_tolerance: float,
    max_retries: int,
    poll_interval: float,
    retry_backoff_base_seconds: float = 0.0,
    stale_timeout_seconds: float | None = None,
    classifier: CategoryClassifier | None = None,
    max_reclassify_attempts: int = 2,
    stop: Callable[[], bool] | None = None,
    parse_model: str | None = None,
    classify_model: str | None = None,
) -> None:
    """Continuously drain the queue, sleeping poll_interval when idle.

    Args:
        session_factory: Factory yielding sessions bound to the application database.
        parser: ReceiptParser used to process each job.
        review_confidence_threshold: Threshold forwarded to process_job.
        total_mismatch_tolerance: Tolerance forwarded to process_job for total mismatch checks.
        max_retries: Retry budget forwarded to process_job and the reaper.
        poll_interval: Seconds to sleep when the queue is empty.
        retry_backoff_base_seconds: Base delay for exponential backoff before a retry.
        stale_timeout_seconds: If set, each iteration re-queues jobs stuck in 'parsing' longer
            than this (recovering work abandoned by a crashed worker). None disables the reaper.
        classifier: Optional focused classifier forwarded to process_job for auto-reclassification.
        max_reclassify_attempts: Per-product LLM reclassification cap forwarded to process_job.
        stop: Optional predicate checked each iteration; the loop exits when it returns
            True. Defaults to running until interrupted.
        parse_model: Provider-prefixed model id forwarded to process_job for cost tracking.
        classify_model: Provider-prefixed model id forwarded to process_job for cost tracking.
    """
    # Reap at most once per stale window; a tighter cadence only re-scans for an event that
    # cannot occur more often than that, wasting a full table scan on every poll.
    last_reap: float | None = None
    while stop is None or not stop():
        if stale_timeout_seconds is not None and (
            last_reap is None or time.monotonic() - last_reap >= stale_timeout_seconds
        ):
            last_reap = time.monotonic()
            with session_factory() as session:
                reap_stale_jobs(
                    session,
                    stale_after_seconds=stale_timeout_seconds,
                    max_retries=max_retries,
                    retry_backoff_base_seconds=retry_backoff_base_seconds,
                )
        processed = run_once(
            session_factory,
            parser=parser,
            review_confidence_threshold=review_confidence_threshold,
            total_mismatch_tolerance=total_mismatch_tolerance,
            max_retries=max_retries,
            retry_backoff_base_seconds=retry_backoff_base_seconds,
            classifier=classifier,
            max_reclassify_attempts=max_reclassify_attempts,
            parse_model=parse_model,
            classify_model=classify_model,
        )
        if not processed:
            time.sleep(poll_interval)


@contextmanager
def worker_pool(
    session_factory: sessionmaker[Session],
    *,
    parser: ReceiptParser,
    settings: Settings,
    count: int,
    classifier: CategoryClassifier | None = None,
) -> Iterator[list[threading.Thread]]:
    """Run `count` daemon worker threads draining the queue for the duration of the block.

    On exit the threads are signaled to stop and joined, so workers always shut down
    gracefully whether the block returns normally or raises. Daemon threads guarantee the
    process can still exit if a worker is mid-parse past the join timeout.
    """
    stop_event = threading.Event()
    threads = [
        threading.Thread(
            target=run_worker,
            kwargs={
                "session_factory": session_factory,
                "parser": parser,
                "review_confidence_threshold": settings.review_confidence_threshold,
                "total_mismatch_tolerance": settings.total_mismatch_tolerance,
                "max_retries": settings.max_retries,
                "poll_interval": settings.worker_poll_interval,
                "retry_backoff_base_seconds": settings.retry_backoff_base_seconds,
                "stale_timeout_seconds": settings.parsing_stale_timeout_seconds,
                "classifier": classifier,
                "max_reclassify_attempts": settings.max_reclassify_attempts,
                "parse_model": settings.parse_model,
                "classify_model": settings.classify_model,
                "stop": stop_event.is_set,
            },
            name=f"cartlog-worker-{i}",
            daemon=True,
        )
        for i in range(count)
    ]
    for thread in threads:
        thread.start()
    try:
        yield threads
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=settings.worker_poll_interval + 5.0)
