"""Tests for the ingestion worker loop."""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from cartlog.config import Settings
from cartlog.db.base import Base
from cartlog.db.models import IngestionJob, JobStatus
from cartlog.db.session import create_session_factory
from cartlog.ingest.queue import enqueue_job
from cartlog.ingest.worker import run_once, run_worker, worker_pool


def test_run_once_processes_a_queued_job(session_factory, fake_parser, tmp_path):
    """Verify run_once claims and processes a single pending job."""
    # Given one enqueued job
    src = tmp_path / "scan.png"
    src.write_bytes(b"\x89PNG fake")
    with session_factory() as session:
        enqueue_job(session, src_path=src, source="cli", storage_dir=tmp_path / "storage")

    # When running one iteration
    processed = run_once(
        session_factory,
        parser=fake_parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
    )

    # Then it reports work done and the job is complete
    assert processed is True
    with session_factory() as session:
        job = session.query(IngestionJob).one()
        assert job.status == JobStatus.DONE


def test_run_once_returns_false_when_queue_empty(session_factory, fake_parser):
    """Verify run_once reports no work when the queue is empty."""
    # Given an empty queue
    # When running one iteration
    processed = run_once(
        session_factory,
        parser=fake_parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
    )

    # Then it reports no work
    assert processed is False


def test_run_worker_drains_then_stops(session_factory, fake_parser, tmp_path):
    """Verify the worker loop drains the queue and honors the stop callback."""
    # Given two enqueued jobs
    with session_factory() as session:
        for name in ("a.png", "b.png"):
            src = tmp_path / name
            src.write_bytes(b"\x89PNG fake " + name.encode())
            enqueue_job(session, src_path=src, source="cli", storage_dir=tmp_path / "storage")

    # And a stop callback that halts after several iterations
    calls = {"n": 0}

    def stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 5

    # When running the worker with a zero poll interval
    run_worker(
        session_factory,
        parser=fake_parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
        poll_interval=0,
        stop=stop,
    )

    # Then both jobs were processed
    with session_factory() as session:
        done = session.query(IngestionJob).filter_by(status=JobStatus.DONE).count()
        assert done == 2


def test_run_worker_reaps_and_processes_stale_job(session_factory, fake_parser, tmp_path):
    """Verify the worker recovers a job stuck in parsing and then processes it to done."""
    # Given a job abandoned in 'parsing' long ago (a crashed worker)
    src = tmp_path / "scan.png"
    src.write_bytes(b"\x89PNG fake")
    with session_factory() as session:
        job = enqueue_job(session, src_path=src, source="cli", storage_dir=tmp_path / "storage")
        stale = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        session.query(IngestionJob).filter_by(id=job.id).update(
            {IngestionJob.status: JobStatus.PARSING, IngestionJob.updated_at: stale}
        )
        session.commit()

    calls = {"n": 0}

    def stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 5

    # When the worker runs with the reaper enabled and zero backoff so the reaped job is due
    run_worker(
        session_factory,
        parser=fake_parser,
        review_confidence_threshold=0.7,
        total_mismatch_tolerance=0.05,
        max_retries=3,
        poll_interval=0,
        retry_backoff_base_seconds=0,
        stale_timeout_seconds=60,
        stop=stop,
    )

    # Then the stale job was reaped (counted a retry) and then processed to completion
    with session_factory() as session:
        reaped = session.query(IngestionJob).one()
        assert reaped.status == JobStatus.DONE
        assert reaped.retry_count == 1


def test_worker_pool_drains_jobs_then_stops_on_exit(tmp_path, fake_parser):
    """Verify worker_pool processes queued jobs and all threads exit after the block."""
    # Given a file-based DB shared across threads (in-memory SQLite is not shared)
    db_path = tmp_path / "pool.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()

    session_factory = create_session_factory(database_url)
    storage = tmp_path / "storage"

    # And one pending job
    src = tmp_path / "scan.png"
    src.write_bytes(b"\x89PNG fake")
    with session_factory() as session:
        enqueue_job(session, src_path=src, source="web", storage_dir=storage)

    # And settings with a near-zero poll interval for a fast test
    settings = Settings(
        anthropic_api_key="test-key",
        database_url=database_url,
        image_storage_dir=storage,
        worker_poll_interval=0.01,
    )

    # When running the pool until the job is processed
    with worker_pool(session_factory, parser=fake_parser, settings=settings, count=2) as threads:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with session_factory() as session:
                if session.query(IngestionJob).one().status == JobStatus.DONE:
                    break
            time.sleep(0.02)

    # Then the job is done and every worker thread has exited
    with session_factory() as session:
        assert session.query(IngestionJob).one().status == JobStatus.DONE
    assert all(not thread.is_alive() for thread in threads)
    session_factory.kw["bind"].dispose()
