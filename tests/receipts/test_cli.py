"""Tests for the `cartlog receipts` CLI sub-app."""

from __future__ import annotations

from typer.testing import CliRunner

from cartlog import cli as cli_module
from cartlog.config import Settings
from cartlog.db.models import IngestionJob, JobStatus, Receipt
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


def _point_first_receipt_at_image(db_url: str, image_path: str) -> int:
    """Repoint one seeded receipt at the given image path and return its id."""
    factory = create_session_factory(db_url)
    try:
        with factory() as session:
            receipt = session.query(Receipt).first()
            assert receipt is not None
            receipt.image_path = image_path
            session.commit()
            return receipt.id
    finally:
        factory.kw["bind"].dispose()


def test_receipts_reparse_requeues_receipt(seeded_db_url, tmp_path, monkeypatch) -> None:
    """Verify `receipts reparse <id>` confirms, deletes the receipt, and prints the new job id."""
    # Given a receipt whose image file exists inside the storage dir
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "cli-rp.png"
    image.write_bytes(b"img")
    settings = Settings(database_url=seeded_db_url, image_storage_dir=storage)
    monkeypatch.setattr(receipts_cli, "get_settings", lambda: settings)
    rid = _point_first_receipt_at_image(seeded_db_url, str(image))

    # When reparsing it and confirming the prompt
    result = runner.invoke(cli_module.app, ["receipts", "reparse", str(rid)], input="y\n")

    # Then it exits cleanly, the old receipt is gone, and a pending job exists for the image
    assert result.exit_code == 0, result.output
    factory = create_session_factory(seeded_db_url)
    try:
        with factory() as session:
            assert session.get(Receipt, rid) is None
            jobs = session.query(IngestionJob).filter_by(image_path=str(image)).all()
            assert len(jobs) == 1
            assert jobs[0].status == JobStatus.PENDING
    finally:
        factory.kw["bind"].dispose()
    assert image.exists()


def test_receipts_reparse_declined_makes_no_change(seeded_db_url, tmp_path, monkeypatch) -> None:
    """Verify declining the confirmation leaves the receipt untouched."""
    # Given a receipt with an on-disk image
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "cli-keep.png"
    image.write_bytes(b"img")
    settings = Settings(database_url=seeded_db_url, image_storage_dir=storage)
    monkeypatch.setattr(receipts_cli, "get_settings", lambda: settings)
    rid = _point_first_receipt_at_image(seeded_db_url, str(image))

    # When reparsing but answering no
    result = runner.invoke(cli_module.app, ["receipts", "reparse", str(rid)], input="n\n")

    # Then the command aborts and the receipt still exists
    assert result.exit_code != 0
    factory = create_session_factory(seeded_db_url)
    try:
        with factory() as session:
            assert session.get(Receipt, rid) is not None
    finally:
        factory.kw["bind"].dispose()


def test_receipts_reparse_unknown_id_exits_nonzero(seeded_db_url, tmp_path, monkeypatch) -> None:
    """Verify reparsing an unknown id prints an error and exits non-zero."""
    # Given settings pointing at the seeded DB
    settings = Settings(database_url=seeded_db_url, image_storage_dir=tmp_path / "storage")
    monkeypatch.setattr(receipts_cli, "get_settings", lambda: settings)

    # When reparsing an id that does not exist
    result = runner.invoke(cli_module.app, ["receipts", "reparse", "99999"], input="y\n")

    # Then it fails with a friendly message
    assert result.exit_code == 1
    assert "No receipt with id 99999" in result.output


def test_receipts_reparse_missing_image_exits_nonzero(seeded_db_url, tmp_path, monkeypatch) -> None:
    """Verify reparsing a receipt whose image is missing prints an error and exits non-zero."""
    # Given settings whose storage dir does not contain the seeded receipt's /tmp/x.png image
    settings = Settings(database_url=seeded_db_url, image_storage_dir=tmp_path / "storage")
    monkeypatch.setattr(receipts_cli, "get_settings", lambda: settings)
    rid = _first_receipt_id(seeded_db_url)

    # When reparsing it
    result = runner.invoke(cli_module.app, ["receipts", "reparse", str(rid)], input="y\n")

    # Then it fails because the image is unavailable, and the receipt is untouched
    assert result.exit_code == 1
    assert "image" in result.output.lower()
    factory = create_session_factory(seeded_db_url)
    try:
        with factory() as session:
            assert session.get(Receipt, rid) is not None
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
