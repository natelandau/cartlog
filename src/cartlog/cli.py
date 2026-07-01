"""Command-line interface for running the cartlog web service."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path  # noqa: TC003

import typer
import uvicorn

from cartlog.backup import BackupError, create_backup
from cartlog.bootstrap import prepare_runtime
from cartlog.categories.service import CategoryService
from cartlog.config import get_settings
from cartlog.db.session import create_session_factory
from cartlog.exceptions import ModelConfigurationError
from cartlog.ingest.folder_watcher import folder_watcher
from cartlog.ingest.worker import worker_pool
from cartlog.parsing.factory import build_ingest_classifier, build_parser

app = typer.Typer(help="cartlog: scan, parse, and store grocery receipts.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """cartlog: scan, parse, and store grocery receipts."""


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
    try:
        with session_factory() as session:
            allowed_categories = CategoryService(session).allowed_categories()
            parser = build_parser(settings, allowed_categories)
            classifier = build_ingest_classifier(settings, allowed_categories)
    except ModelConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    # Import here so module import (e.g. for --help) doesn't pay the cost of building the web app.
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
        web_app = create_app(dev=dev)
        server = uvicorn.Server(uvicorn.Config(web_app, host=host, port=port))
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
            ) as worker_threads,
            folder_watcher(session_factory, settings),
            suppress(KeyboardInterrupt),
        ):
            # Expose the live worker threads so the /healthz probe can report pool liveness.
            web_app.state.worker_threads = worker_threads
            server.run()
    finally:
        if css_watcher is not None:
            # Stop the Tailwind watcher and reap it so it leaves no orphaned process.
            css_watcher.terminate()
            with suppress(Exception):
                css_watcher.wait(timeout=5)
        session_factory.kw["bind"].dispose()


@app.command()
def backup(
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help=(
            "Destination file or directory for the .tar.gz. Overrides CARTLOG_BACKUP_DIR; "
            "when neither is set, a timestamped file is written to the current directory."
        ),
    ),
) -> None:
    """Write a single .tar.gz of the database and receipt images.

    The archive holds a consistent, compacted `cartlog.db` (safe to run while the server is
    live) and the full `receipt_images/` directory, laid out so a restore can extract it
    into a fresh data directory and run the app unchanged.

    Destination precedence: --output, then CARTLOG_BACKUP_DIR, then the current directory.
    """
    settings = get_settings()
    try:
        result = create_backup(settings, output)
    except BackupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Backup written to {result.path} "
        f"({result.database_bytes} bytes database, {result.image_count} image(s))."
    )
