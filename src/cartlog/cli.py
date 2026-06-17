"""Command-line interface for scanning, parsing, and storing grocery receipts."""

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003  # Typer resolves command annotations at runtime

import typer
import uvicorn
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model, infer_model
from rich.console import Console
from sqlalchemy.orm import Session  # noqa: TC002  # used in helper annotations at runtime

from cartlog.analytics.cli import query_app
from cartlog.bootstrap import prepare_runtime
from cartlog.categories.service import CategoryService
from cartlog.cli_progress import StageChecklist
from cartlog.config import Settings, get_settings
from cartlog.db.cli import db_app
from cartlog.db.models import IngestionJob, JobStatus, JobStep, Receipt
from cartlog.db.session import create_session_factory
from cartlog.ingest.pipeline import process_job
from cartlog.ingest.queue import claim_job, enqueue_job
from cartlog.ingest.worker import run_worker, worker_pool
from cartlog.parsing.category_classifier import LLMCategoryClassifier
from cartlog.parsing.llm_parser import LLMReceiptParser
from cartlog.receipts.cli import receipts_app

app = typer.Typer(help="cartlog: scan, parse, and store grocery receipts.", no_args_is_help=True)
app.add_typer(query_app, name="query")
app.add_typer(receipts_app, name="receipts")
app.add_typer(db_app, name="db")

console = Console()


@app.callback()
def main() -> None:
    """cartlog: scan, parse, and store grocery receipts."""


def _build_model(model_id: str) -> Model:
    """Build a Pydantic AI model from a provider-prefixed id, failing fast on a bad config.

    Construction reads the provider's API key from its native environment variable. A missing
    key raises UserError; an unknown provider prefix raises ValueError. Both are surfaced as a
    friendly CLI error naming the problem rather than a raw traceback.

    Raises:
        typer.BadParameter: If the provider key is unset or the model id names an unknown provider.
    """
    try:
        return infer_model(model_id)
    except (UserError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def build_parser(
    settings: Settings, allowed_categories: list[str] | None = None
) -> LLMReceiptParser:
    """Construct the production LLM parser from settings (patched out in tests).

    An empty list for `allowed_categories` is treated the same as None: both
    fall back to free-form category guidance in the prompt.
    """
    model = _build_model(settings.parse_model)
    return LLMReceiptParser(model=model, allowed_categories=allowed_categories)


def build_classifier(settings: Settings, allowed_categories: list[str]) -> LLMCategoryClassifier:
    """Construct the focused category classifier from settings (patched out in tests)."""
    # Validate the provider key before the taxonomy so a missing key reports first, matching
    # the pre-flight order callers rely on (a key error is the root cause, not the taxonomy).
    model = _build_model(settings.classify_model)
    if not allowed_categories:
        msg = "No categories in the taxonomy to classify into; seed categories first."
        raise typer.BadParameter(msg)
    return LLMCategoryClassifier(model=model, allowed_categories=allowed_categories)


def build_ingest_classifier(
    settings: Settings, allowed_categories: list[str]
) -> LLMCategoryClassifier | None:
    """Build the auto-reclassification classifier for ingestion, or None if no taxonomy exists.

    Returns None when no categories are seeded yet, so ingestion runs without the second pass
    rather than failing; the API key is already validated by the parser build. Pass the allowed
    taxonomy the caller already read, so the command issues a single query for it.
    """
    if not allowed_categories:
        return None
    return build_classifier(settings, allowed_categories)


@dataclass
class IngestResult:
    """Outcome of ingesting one file in a batch, used to build the summary and exit code."""

    receipt: Receipt | None
    ok: bool


def _process_enqueued_job(
    session: Session,
    job: IngestionJob,
    *,
    parser: LLMReceiptParser,
    settings: Settings,
    console: Console,
    classifier: LLMCategoryClassifier | None = None,
) -> tuple[bool, Receipt | None]:
    """Claim and parse one already-enqueued job in the foreground, rendering a checklist.

    Returns (claimed, receipt): claimed is False when a concurrent worker won the job;
    receipt is None when the claim was lost or the parse did not produce a receipt.
    """
    stages = [
        ("claiming", f"Claiming job #{job.id}"),
        (JobStep.EXTRACTING, "Reading receipt with the model"),
        (JobStep.SAVING, "Saving parsed receipt"),
    ]
    receipt: Receipt | None = None
    with StageChecklist(console, stages) as checklist:
        checklist.start("claiming")
        # Claim before parsing so a concurrent worker can't double-process this job.
        claimed = claim_job(session, job)
        # The claiming phase is over either way; mark it done so a claim lost to a
        # concurrent worker is not rendered as a red failure mark.
        checklist.finish()
        if not claimed:
            return False, None
        receipt = process_job(
            session,
            job,
            parser=parser,
            review_confidence_threshold=settings.review_confidence_threshold,
            total_mismatch_tolerance=settings.total_mismatch_tolerance,
            max_retries=settings.max_retries,
            retry_backoff_base_seconds=settings.retry_backoff_base_seconds,
            classifier=classifier,
            max_reclassify_attempts=settings.max_reclassify_attempts,
            on_step=checklist.start,
        )
        if receipt is not None:
            checklist.finish()
    return True, receipt


def _ingest_one(
    session: Session,
    job: IngestionJob,
    *,
    parser: LLMReceiptParser,
    settings: Settings,
    console: Console,
    classifier: LLMCategoryClassifier | None = None,
) -> IngestResult:
    """Parse one enqueued job and classify its outcome into a printed IngestResult."""
    claimed, receipt = _process_enqueued_job(
        session, job, parser=parser, settings=settings, console=console, classifier=classifier
    )
    if not claimed:
        message = f"Job #{job.id} is already being processed by a worker."
    elif receipt is None:
        if job.status == JobStatus.FAILED:
            message = f"Job #{job.id} failed permanently: {job.last_error}"
        else:
            message = f"Job #{job.id} parse failed, re-queued for retry: {job.last_error}"
    else:
        message = (
            f"Ingested receipt #{receipt.id} from {receipt.store.chain_name} "
            f"({len(receipt.line_items)} items, status={receipt.status})"
        )
    console.print(message)
    # A lost claim is handled elsewhere, so it is not a failure; a missing receipt after a
    # won claim is.
    ok = not claimed or receipt is not None
    return IngestResult(receipt, ok=ok)


@app.command()
def ingest(
    files: list[Path] = typer.Argument(
        ..., exists=True, readable=True, help="Receipt image(s) or PDF(s) to ingest."
    ),
    source: str = typer.Option("cli", help="Ingestion source label stored on the job."),
    no_wait: bool = typer.Option(  # noqa: FBT001
        False,  # noqa: FBT003
        "--no-wait",
        help="Enqueue only and exit; let a running worker parse them.",
    ),
) -> None:
    """Enqueue one or more receipts and, by default, parse each immediately for instant feedback."""
    settings = get_settings()

    # Build the parse model up front (when we will parse) so a missing provider key fails
    # before any files are stored or jobs enqueued, rather than stranding PENDING jobs.
    if not no_wait:
        _build_model(settings.parse_model)

    session_factory = create_session_factory(settings.database_url)

    try:
        with session_factory() as session:
            # Read the allowed taxonomy once and reuse it for the guard, parser, and classifier.
            allowed_categories = CategoryService(session).allowed_categories()

            # When parsing inline, validate the classify provider key too before storing any
            # files. The reclassification pass runs only when a taxonomy exists, so a classify
            # model on a different provider with a missing key would otherwise fail after jobs
            # are enqueued, stranding them as PENDING.
            if not no_wait and allowed_categories:
                _build_model(settings.classify_model)

            # Enqueue everything first so a running worker can see the whole batch and the
            # foreground parse loop is decoupled from storing the files.
            enqueued: list[tuple[Path, IngestionJob]] = []
            for path in files:
                job = enqueue_job(
                    session, src_path=path, source=source, storage_dir=settings.image_storage_dir
                )
                enqueued.append((path, job))

            if no_wait:
                for path, job in enqueued:
                    typer.echo(f"Enqueued job #{job.id} for {path.name} (status={job.status}).")
                return

            parser = build_parser(settings, allowed_categories)
            # Auto-reclassify miscategorized lines as part of ingestion (None if no taxonomy yet).
            classifier = build_ingest_classifier(settings, allowed_categories)

            results: list[IngestResult] = []
            for index, (path, job) in enumerate(enqueued, start=1):
                if len(enqueued) > 1:
                    console.print(f"[{index}/{len(enqueued)}] {path.name}")
                results.append(
                    _ingest_one(
                        session,
                        job,
                        parser=parser,
                        settings=settings,
                        console=console,
                        classifier=classifier,
                    )
                )

            ingested = sum(1 for r in results if r.receipt is not None)
            failed = sum(1 for r in results if not r.ok)
            # Only print a roll-up for true batches so single-file output is unchanged.
            if len(results) > 1:
                console.print(f"\n{ingested} ingested, {failed} failed.")
            if failed:
                raise typer.Exit(code=1)
    finally:
        # One-shot command: dispose the engine so it leaves no open database connection.
        session_factory.kw["bind"].dispose()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host interface to bind the web server to."),
    port: int = typer.Option(8000, help="Port to bind the web server to."),
    workers: int = typer.Option(1, min=1, help="Number of ingestion worker threads to run."),
    dev: bool = typer.Option(  # noqa: FBT001
        False,  # noqa: FBT003
        "--dev",
        help="Run in development mode: reload templates on change and show debug tracebacks.",
    ),
    skip_css_build: bool = typer.Option(  # noqa: FBT001
        False,  # noqa: FBT003
        "--skip-css-build",
        help="Serve the already-compiled stylesheet instead of rebuilding it (needs the frontend toolchain).",
    ),
) -> None:
    """Bootstrap the database, start the web server, and run ingestion worker(s).

    A single-command way to run the whole application: uploads enqueued by the web UI are
    processed by the in-process worker(s) without a separate process. Pass --dev to serve
    in development mode (templates reload on change); this is what `duty dev` runs.

    Pass --skip-css-build to serve a stylesheet compiled elsewhere (e.g. a Docker build
    stage), so the runtime needs neither Node nor the Tailwind toolchain.
    """
    settings = get_settings()
    prepare_runtime(settings)
    session_factory = create_session_factory(settings.database_url)
    # Build the parser before binding the port so a missing API key fails fast.
    # The allowed list is read once at startup; restart the server after taxonomy edits.
    with session_factory() as session:
        allowed_categories = CategoryService(session).allowed_categories()
        parser = build_parser(settings, allowed_categories)
        classifier = build_ingest_classifier(settings, allowed_categories)

    # Import here so the other CLI commands don't pay the cost of building the web app.
    from cartlog.web import assets  # noqa: PLC0415
    from cartlog.web.app import create_app  # noqa: PLC0415

    # css_watcher is referenced in the finally, so bind it before anything that can raise
    # (e.g. the CSS build) to keep that cleanup path total.
    css_watcher = None
    try:
        # Compile the stylesheet so the served CSS always matches the current templates.
        # Minify for production; keep dev output readable. In dev, also watch for rebuilds.
        # --skip-css-build trusts a stylesheet compiled ahead of time (e.g. in a Docker build
        # stage) so the runtime needs neither Node nor node_modules.
        if not skip_css_build:
            try:
                assets.build_css(watch=False, minify=not dev)
            except assets.AssetBuildError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(code=1) from exc
        css_watcher = assets.build_css(watch=True) if dev and not skip_css_build else None

        # Pass the app instance (not an import string) so --dev flows through to create_app.
        server = uvicorn.Server(uvicorn.Config(create_app(dev=dev), host=host, port=port))
        mode = " in dev mode" if dev else ""
        typer.echo(f"cartlog serving at http://{host}:{port} with {workers} worker(s){mode}.")
        # suppress is the inner context so a Ctrl-C raised before uvicorn installs its handler
        # is swallowed (no traceback), then worker_pool's exit gracefully stops the workers.
        with (
            worker_pool(
                session_factory,
                parser=parser,
                settings=settings,
                count=workers,
                classifier=classifier,
            ),
            suppress(KeyboardInterrupt),
        ):
            server.run()
    finally:
        if css_watcher is not None:
            # Stop the Tailwind watcher and reap it so it leaves no orphaned process.
            css_watcher.terminate()
            with suppress(Exception):
                css_watcher.wait(timeout=5)
        session_factory.kw["bind"].dispose()


@app.command()
def worker() -> None:
    """Continuously process queued ingestion jobs until interrupted."""
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    # Read the allowed taxonomy once at startup; restart the worker after taxonomy edits.
    with session_factory() as session:
        allowed_categories = CategoryService(session).allowed_categories()
        parser = build_parser(settings, allowed_categories)
        classifier = build_ingest_classifier(settings, allowed_categories)
    typer.echo("cartlog worker started; polling for jobs (Ctrl-C to stop).")

    try:
        run_worker(
            session_factory,
            parser=parser,
            review_confidence_threshold=settings.review_confidence_threshold,
            total_mismatch_tolerance=settings.total_mismatch_tolerance,
            max_retries=settings.max_retries,
            poll_interval=settings.worker_poll_interval,
            retry_backoff_base_seconds=settings.retry_backoff_base_seconds,
            stale_timeout_seconds=settings.parsing_stale_timeout_seconds,
            classifier=classifier,
            max_reclassify_attempts=settings.max_reclassify_attempts,
        )
    except KeyboardInterrupt:
        typer.echo("Worker stopped.")
    finally:
        session_factory.kw["bind"].dispose()
