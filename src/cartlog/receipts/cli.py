"""`cartlog receipts` sub-app: manage stored receipts."""

from __future__ import annotations

import typer

from cartlog.config import get_settings
from cartlog.db.models import Receipt
from cartlog.db.session import create_session_factory
from cartlog.receipts.service import delete_receipt

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
