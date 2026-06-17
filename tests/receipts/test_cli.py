"""Tests for the `cartlog receipts` CLI sub-app."""

from __future__ import annotations

from typer.testing import CliRunner

from cartlog import cli as cli_module
from cartlog.config import Settings
from cartlog.db.models import Receipt
from cartlog.db.session import create_session_factory
from cartlog.receipts import cli as receipts_cli

runner = CliRunner()


def _first_receipt_id(db_url: str) -> int:
    """Return the id of any one seeded receipt."""
    factory = create_session_factory(db_url)
    try:
        with factory() as session:
            receipt = session.query(Receipt).first()
            assert receipt is not None
            return receipt.id
    finally:
        factory.kw["bind"].dispose()


def test_receipts_delete_removes_receipt(seeded_db_url, tmp_path, monkeypatch) -> None:
    """Verify `receipts delete <id>` deletes the receipt and prints a summary."""
    # Given settings pointing at the seeded DB
    settings = Settings(database_url=seeded_db_url, image_storage_dir=tmp_path / "storage")
    monkeypatch.setattr(receipts_cli, "get_settings", lambda: settings)
    rid = _first_receipt_id(seeded_db_url)

    # When deleting that receipt
    result = runner.invoke(cli_module.app, ["receipts", "delete", str(rid)])

    # Then it exits cleanly, prints a summary, and the row is gone
    assert result.exit_code == 0, result.output
    assert f"Deleted receipt #{rid}" in result.output
    factory = create_session_factory(seeded_db_url)
    try:
        with factory() as session:
            assert session.get(Receipt, rid) is None
    finally:
        factory.kw["bind"].dispose()


def test_receipts_delete_unknown_id_exits_nonzero(seeded_db_url, tmp_path, monkeypatch) -> None:
    """Verify deleting an unknown id prints an error and exits non-zero."""
    # Given settings pointing at the seeded DB
    settings = Settings(database_url=seeded_db_url, image_storage_dir=tmp_path / "storage")
    monkeypatch.setattr(receipts_cli, "get_settings", lambda: settings)

    # When deleting an id that does not exist
    result = runner.invoke(cli_module.app, ["receipts", "delete", "99999"])

    # Then it fails with a friendly message
    assert result.exit_code == 1
    assert "No receipt with id 99999" in result.output
