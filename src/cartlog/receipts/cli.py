"""`cartlog receipts` sub-app: manage stored receipts."""

from __future__ import annotations

import typer

from cartlog.config import get_settings
from cartlog.db.models import Receipt
from cartlog.db.session import create_session_factory
from cartlog.receipts.service import (
    ReparseImageMissingError,
    delete_receipt,
    image_file_available,
    reparse_receipt,
)

receipts_app = typer.Typer(help="Manage stored receipts.", no_args_is_help=True)


@receipts_app.command("delete")
def delete_command(
    receipt_id: int = typer.Argument(..., help="Id of the receipt to delete."),
) -> None:
    """Delete a receipt and its line items, ingestion job, and unshared image file."""
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    try:
        with session_factory() as session:
            receipt = session.get(Receipt, receipt_id)
            if receipt is None:
                typer.echo(f"No receipt with id {receipt_id}.", err=True)
                raise typer.Exit(code=1)
            # Capture a summary before deletion; the row is unusable afterward.
            chain = receipt.store.chain_name
            item_count = len(receipt.line_items)
            delete_receipt(session, receipt_id, storage_dir=settings.image_storage_dir)
        typer.echo(f"Deleted receipt #{receipt_id} ({chain}, {item_count} items).")
    finally:
        # One-shot command: dispose the engine so it leaves no open database connection.
        session_factory.kw["bind"].dispose()


@receipts_app.command("reparse")
def reparse_command(
    receipt_id: int = typer.Argument(..., help="Id of the receipt to reparse."),
) -> None:
    """Delete a receipt's parsed data and queue its image to be parsed again from scratch."""
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    try:
        with session_factory() as session:
            receipt = session.get(Receipt, receipt_id)
            if receipt is None:
                typer.echo(f"No receipt with id {receipt_id}.", err=True)
                raise typer.Exit(code=1)
            # Refuse before prompting so the user is never asked to confirm a reparse that
            # cannot run; the service raises the same error as a backstop for a file that
            # vanishes between this check and the parse.
            if not image_file_available(receipt.image_path, storage_dir=settings.image_storage_dir):
                typer.echo(
                    f"Image file for receipt {receipt_id} is missing; cannot reparse.", err=True
                )
                raise typer.Exit(code=1)
            typer.confirm(
                f"Delete receipt #{receipt_id}'s parsed data and parse its image again "
                "from scratch? This cannot be undone.",
                abort=True,
            )
            try:
                job = reparse_receipt(session, receipt_id, storage_dir=settings.image_storage_dir)
            except ReparseImageMissingError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(code=1) from exc
        # job is not None here: receipt existence was checked above before the destructive call.
        if job is None:  # pragma: no cover
            msg = f"reparse_receipt returned None despite receipt {receipt_id} existing"
            raise RuntimeError(msg)
        typer.echo(f"Reparsing receipt #{receipt_id}; queued job #{job.id}.")
    finally:
        # One-shot command: dispose the engine so it leaves no open database connection.
        session_factory.kw["bind"].dispose()
