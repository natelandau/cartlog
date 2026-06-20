"""Tests for the receipt deletion service."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from cartlog.db.models import (
    Category,
    IngestionJob,
    JobStatus,
    LineItem,
    ParseCostEvent,
    Product,
    Receipt,
    ReceiptStatus,
    Store,
)
from cartlog.receipts.service import (
    ReparseImageMissingError,
    apply_line_item_edit,
    apply_receipt_edit,
    delete_receipt,
    image_file_available,
    reparse_receipt,
)
from cartlog.web.forms import LineEdit, ReceiptEdit
from tests.factories import seed_receipts


def _make_receipt(
    session, *, image_path: Path, status: ReceiptStatus = ReceiptStatus.PARSED
) -> Receipt:
    """Create and commit a one-line receipt, reusing a shared store/product if present."""
    store = session.query(Store).filter_by(
        chain_name="Safeway", location="Main St"
    ).first() or Store(chain_name="Safeway", location="Main St")
    product = session.query(Product).filter_by(canonical_name="eggs").first() or Product(
        canonical_name="eggs"
    )
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 1, 1),
        total=Decimal("3.00"),
        currency="USD",
        image_path=str(image_path),
        raw_parser_json="{}",
        source="cli",
        status=status,
    )
    receipt.line_items.append(
        LineItem(
            product=product,
            raw_description="EGGS",
            quantity=Decimal(1),
            unit_price=Decimal("3.00"),
            line_total=Decimal("3.00"),
        )
    )
    session.add(receipt)
    session.commit()
    return receipt


def test_delete_receipt_removes_receipt_and_line_items(session, tmp_path) -> None:
    """Verify deleting a receipt removes it and its line items but keeps shared store/product."""
    # Given a storage dir with one receipt whose image file exists
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "r-abc123.png"
    image.write_bytes(b"img")
    receipt = _make_receipt(session, image_path=image)
    rid = receipt.id

    # When deleting the receipt
    deleted = delete_receipt(session, rid, storage_dir=storage)

    # Then the receipt and its line items are gone, but the shared store/product remain
    assert deleted is True
    assert session.get(Receipt, rid) is None
    assert session.query(LineItem).count() == 0
    assert session.query(Store).count() == 1
    assert session.query(Product).count() == 1


def test_delete_receipt_unknown_id_returns_false(session, tmp_path) -> None:
    """Verify deleting a nonexistent receipt id returns False and changes nothing."""
    # Given an empty storage dir and no receipts
    storage = tmp_path / "storage"
    storage.mkdir()

    # When deleting an id that does not exist
    deleted = delete_receipt(session, 999, storage_dir=storage)

    # Then it reports nothing was deleted
    assert deleted is False


def test_delete_receipt_removes_ingestion_job(session, tmp_path) -> None:
    """Verify deleting a receipt also removes the ingestion job that produced it."""
    # Given a receipt with a linked, completed ingestion job
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "j-abc123.png"
    image.write_bytes(b"img")
    receipt = _make_receipt(session, image_path=image)
    job = IngestionJob(
        source="web",
        image_path=str(image),
        status=JobStatus.DONE,
        receipt_id=receipt.id,
    )
    session.add(job)
    session.commit()

    # When deleting the receipt
    delete_receipt(session, receipt.id, storage_dir=storage)

    # Then the linked ingestion job is gone, and the now-unreferenced image file is removed
    assert session.query(IngestionJob).count() == 0
    assert not image.exists()


def test_delete_receipt_keeps_shared_image_file(session, tmp_path) -> None:
    """Verify the image file is kept when a second receipt references the same path."""
    # Given two receipts that share one content-hashed image file (a duplicate upload)
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "dup-abc123.png"
    image.write_bytes(b"img")
    first = _make_receipt(session, image_path=image)
    second = _make_receipt(session, image_path=image)

    # When deleting the first copy
    delete_receipt(session, first.id, storage_dir=storage)

    # Then the surviving receipt and the shared file both remain
    assert session.get(Receipt, second.id) is not None
    assert image.exists()


def test_delete_receipt_removes_unshared_image_file(session, tmp_path) -> None:
    """Verify the image file is deleted when no other receipt or job references it."""
    # Given a single receipt that solely owns its image file
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "solo-abc123.png"
    image.write_bytes(b"img")
    receipt = _make_receipt(session, image_path=image)

    # When deleting it
    delete_receipt(session, receipt.id, storage_dir=storage)

    # Then the now-orphaned file is removed from disk
    assert not image.exists()


def test_delete_receipt_keeps_file_outside_storage_dir(session, tmp_path) -> None:
    """Verify a receipt whose image lives outside storage_dir is deleted without unlinking it."""
    # Given a receipt whose image_path points outside the configured storage dir
    storage = tmp_path / "storage"
    storage.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"img")
    receipt = _make_receipt(session, image_path=outside)

    # When deleting it
    deleted = delete_receipt(session, receipt.id, storage_dir=storage)

    # Then the row is gone but the out-of-storage file is left untouched
    assert deleted is True
    assert outside.exists()


def test_delete_receipt_keeps_image_referenced_by_another_job(session, tmp_path) -> None:
    """Verify the image file survives when an unrelated ingestion job still references it."""
    # Given a receipt whose image file is also referenced by a separate, unrelated job (e.g. a
    # second upload of the same bytes that has not produced its own receipt yet)
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "shared-abc123.png"
    image.write_bytes(b"img")
    receipt = _make_receipt(session, image_path=image)
    other_job = IngestionJob(
        source="web",
        image_path=str(image),
        status=JobStatus.PENDING,
    )
    session.add(other_job)
    session.commit()

    # When deleting the receipt
    delete_receipt(session, receipt.id, storage_dir=storage)

    # Then the unrelated job and the file it pins both remain
    assert session.query(IngestionJob).count() == 1
    assert image.exists()


def test_delete_receipt_survives_image_unlink_failure(session, tmp_path, monkeypatch) -> None:
    """Verify a filesystem error removing the image does not fail the committed deletion."""
    # Given a receipt whose image file cannot be unlinked (e.g. a read-only filesystem)
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "locked-abc123.png"
    image.write_bytes(b"img")
    receipt = _make_receipt(session, image_path=image)

    def _raise_permission_error(self: Path, **_kwargs: object) -> None:
        msg = "read-only filesystem"
        raise PermissionError(msg)

    monkeypatch.setattr(Path, "unlink", _raise_permission_error)

    # When deleting it
    deleted = delete_receipt(session, receipt.id, storage_dir=storage)

    # Then the deletion still succeeds and the receipt row is gone
    assert deleted is True
    assert session.get(Receipt, receipt.id) is None


# ---------------------------------------------------------------------------
# apply_receipt_edit helpers and tests
# ---------------------------------------------------------------------------


def _needs_review_receipt(session: Session) -> Receipt:
    """Return the seeded needs_review receipt (two lines: eggs + milk)."""
    seed_receipts(session)
    receipt = session.query(Receipt).filter_by(status=ReceiptStatus.NEEDS_REVIEW).first()
    assert receipt is not None
    return receipt


def _line_edit(line: LineItem, **overrides: object) -> LineEdit:
    """Build a LineEdit mirroring an existing line, with optional field overrides."""
    fields: dict[str, object] = {
        "line_id": line.id,
        "raw_description": line.raw_description,
        "canonical_name": line.product.canonical_name,
        "category_id": None,
        "quantity": line.quantity,
        "unit": line.unit,
        "unit_size": line.unit_size,
        "unit_price": line.unit_price,
        "line_total": line.line_total,
    }
    fields.update(overrides)
    return LineEdit(**fields)


def _edit(receipt: Receipt, lines: list[LineEdit]) -> ReceiptEdit:
    """Build a ReceiptEdit reusing the receipt's current header values."""
    return ReceiptEdit(
        chain_name=receipt.store.chain_name,
        location=receipt.store.location,
        purchase_date=receipt.purchase_date,
        total=receipt.total,
        currency=receipt.currency,
        lines=lines,
    )


def test_apply_receipt_edit_updates_existing_line(session) -> None:
    """Verify an edited line's fields are written back to the existing row."""
    # Given the needs_review receipt and an edit that changes the first line's quantity
    receipt = _needs_review_receipt(session)
    eggs, milk = receipt.line_items
    edit = _edit(receipt, [_line_edit(eggs, quantity=Decimal(4)), _line_edit(milk)])

    # When applying the edit
    apply_receipt_edit(session, receipt, edit)

    # Then the existing line is updated in place, not duplicated
    refreshed = session.get(LineItem, eggs.id)
    assert refreshed is not None
    assert refreshed.quantity == Decimal(4)
    assert len(session.get(Receipt, receipt.id).line_items) == 2


def test_apply_receipt_edit_adds_new_line(session) -> None:
    """Verify a line with no id is created and linked to the receipt."""
    # Given the receipt plus a seeded bakery category
    receipt = _needs_review_receipt(session)
    bakery = Category(name="bakery")
    session.add(bakery)
    session.commit()
    eggs, milk = receipt.line_items
    # And an edit appending a brand-new bread line assigned to bakery by id
    new_line = LineEdit(
        line_id=None,
        raw_description="BREAD",
        canonical_name="bread",
        category_id=bakery.id,
        quantity=Decimal(1),
        unit=None,
        unit_size=None,
        unit_price=Decimal("2.50"),
        line_total=Decimal("2.50"),
    )
    edit = _edit(receipt, [_line_edit(eggs), _line_edit(milk), new_line])

    # When applying the edit
    apply_receipt_edit(session, receipt, edit)

    # Then a third line exists, linked to a new bread product in the bakery category
    receipt = session.get(Receipt, receipt.id)
    assert len(receipt.line_items) == 3
    bread = session.query(Product).filter_by(canonical_name="bread").one()
    assert bread.category is not None
    assert bread.category.name == "bakery"


def test_apply_receipt_edit_removes_absent_line(session) -> None:
    """Verify a current line omitted from the post is deleted."""
    # Given the two-line receipt and an edit that posts only the first line
    receipt = _needs_review_receipt(session)
    eggs, milk = receipt.line_items
    milk_id = milk.id
    edit = _edit(receipt, [_line_edit(eggs)])

    # When applying the edit
    apply_receipt_edit(session, receipt, edit)

    # Then the omitted line is gone and only the kept line remains
    assert session.get(LineItem, milk_id) is None
    assert len(session.get(Receipt, receipt.id).line_items) == 1


def test_apply_receipt_edit_repoint_product_inherits_category(session) -> None:
    """Verify renaming a line's product repoints it and adopts that product's category."""
    # Given an existing 'hummus' product categorized as 'refrigerated'
    receipt = _needs_review_receipt(session)
    refrigerated = Category(name="refrigerated")
    session.add(Product(canonical_name="hummus", category=refrigerated))
    session.commit()
    eggs, milk = receipt.line_items
    # And an edit repointing the first line from 'eggs' to the existing 'hummus' product
    edit = _edit(receipt, [_line_edit(eggs, canonical_name="hummus"), _line_edit(milk)])

    # When applying the edit
    apply_receipt_edit(session, receipt, edit)

    # Then the line points at hummus and inherits its refrigerated category
    line = session.get(LineItem, eggs.id)
    assert line.product.canonical_name == "hummus"
    assert line.product.category.name == "refrigerated"


def test_apply_receipt_edit_category_writeback_affects_shared_product(session) -> None:
    """Verify editing a line's category_id rewrites the shared product's category for all."""
    # Given the receipt; 'eggs' is shared by other seeded receipts, and a breakfast category
    receipt = _needs_review_receipt(session)
    breakfast = Category(name="breakfast")
    session.add(breakfast)
    session.commit()
    eggs, milk = receipt.line_items
    # And an edit setting the eggs line category_id to breakfast's id
    edit = _edit(receipt, [_line_edit(eggs, category_id=breakfast.id), _line_edit(milk)])

    # When applying the edit
    apply_receipt_edit(session, receipt, edit)

    # Then the shared eggs product now resolves to breakfast everywhere
    eggs_product = session.query(Product).filter_by(canonical_name="eggs").one()
    assert eggs_product.category.name == "breakfast"


def test_apply_receipt_edit_empty_lines_removes_all(session) -> None:
    """Verify posting an empty line list deletes every existing line item."""
    # Given the two-line needs_review receipt
    receipt = _needs_review_receipt(session)
    receipt_id = receipt.id

    # When applying an edit whose line set is empty
    apply_receipt_edit(session, receipt, _edit(receipt, []))

    # Then the receipt survives but all of its line items are gone
    assert session.get(Receipt, receipt_id) is not None
    assert session.get(Receipt, receipt_id).line_items == []


def test_apply_receipt_edit_dedups_new_shared_product(session) -> None:
    """Verify two new lines sharing a brand-new product name reuse one product row, not duplicate."""
    # Given the receipt and a seeded bakery category
    receipt = _needs_review_receipt(session)
    bakery = Category(name="bakery")
    session.add(bakery)
    session.commit()
    eggs, milk = receipt.line_items
    # And an edit adding two new lines that both reference the SAME new product name
    new_a = LineEdit(
        line_id=None,
        raw_description="BREAD A",
        canonical_name="bread",
        category_id=bakery.id,
        quantity=Decimal(1),
        unit=None,
        unit_size=None,
        unit_price=Decimal("2.00"),
        line_total=Decimal("2.00"),
    )
    new_b = LineEdit(
        line_id=None,
        raw_description="BREAD B",
        canonical_name="bread",
        category_id=bakery.id,
        quantity=Decimal(1),
        unit=None,
        unit_size=None,
        unit_price=Decimal("2.50"),
        line_total=Decimal("2.50"),
    )
    edit = _edit(receipt, [_line_edit(eggs), _line_edit(milk), new_a, new_b])

    # When applying the edit
    apply_receipt_edit(session, receipt, edit)

    # Then exactly one bread product exists (no unique-constraint failure) and all 4 lines are saved
    assert session.query(Product).filter_by(canonical_name="bread").count() == 1
    assert len(session.get(Receipt, receipt.id).line_items) == 4


# ---------------------------------------------------------------------------
# apply_line_item_edit tests
# ---------------------------------------------------------------------------


def test_apply_line_item_edit_reassigns_to_existing_product(session) -> None:
    """Verify editing a line repoints it to an existing product and adopts that category."""
    # Given a needs_review receipt and an existing 'hummus' product in 'refrigerated'
    receipt = _needs_review_receipt(session)
    refrigerated = Category(name="refrigerated")
    session.add(Product(canonical_name="hummus", category=refrigerated))
    session.commit()
    eggs, _milk = receipt.line_items

    # When reassigning the eggs line to 'hummus' with no category override
    apply_line_item_edit(session, eggs, canonical_name="hummus", category_id=None)

    # Then the line points at hummus and inherits its refrigerated category
    line = session.get(LineItem, eggs.id)
    assert line.product.canonical_name == "hummus"
    assert line.product.category.name == "refrigerated"


def test_apply_line_item_edit_creates_new_product(session) -> None:
    """Verify a brand-new canonical name creates the product and repoints only this line."""
    # Given a needs_review receipt
    receipt = _needs_review_receipt(session)
    eggs, milk = receipt.line_items
    milk_product_id = milk.product_id

    # When reassigning the eggs line to a name that does not yet exist
    apply_line_item_edit(session, eggs, canonical_name="duck eggs", category_id=None)

    # Then a new product exists, the eggs line uses it, and the milk line is untouched
    assert session.query(Product).filter_by(canonical_name="duck eggs").one()
    assert session.get(LineItem, eggs.id).product.canonical_name == "duck eggs"
    assert session.get(LineItem, milk.id).product_id == milk_product_id


def test_apply_line_item_edit_category_writeback_affects_shared_product(session) -> None:
    """Verify a category_id rewrites the shared product's category for every line on it."""
    # Given a needs_review receipt and a breakfast category
    receipt = _needs_review_receipt(session)
    breakfast = Category(name="breakfast")
    session.add(breakfast)
    session.commit()
    eggs, _milk = receipt.line_items

    # When setting the eggs line's category to breakfast (name unchanged)
    apply_line_item_edit(
        session, eggs, canonical_name=eggs.product.canonical_name, category_id=breakfast.id
    )

    # Then the shared eggs product resolves to breakfast everywhere
    eggs_product = session.query(Product).filter_by(canonical_name="eggs").one()
    assert eggs_product.category.name == "breakfast"


def test_apply_line_item_edit_unknown_category_raises(session) -> None:
    """Verify an unknown category_id raises ValueError and does not commit a product swap."""
    # Given a needs_review receipt
    receipt = _needs_review_receipt(session)
    eggs, _milk = receipt.line_items
    original_product_id = eggs.product_id

    # When editing with a category id that does not exist
    with pytest.raises(ValueError, match="Category id 999999 does not exist"):
        apply_line_item_edit(session, eggs, canonical_name="eggs", category_id=999999)

    # Then nothing was committed for this line
    session.rollback()
    assert session.get(LineItem, eggs.id).product_id == original_product_id


# ---------------------------------------------------------------------------
# reparse_receipt and image_file_available tests
# ---------------------------------------------------------------------------


def test_reparse_receipt_enqueues_new_job_for_same_image(session, tmp_path) -> None:
    """Verify reparse creates a pending job for the same image and deletes the old receipt."""
    # Given a parsed receipt whose image file exists inside the storage dir
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "rp-abc123.png"
    image.write_bytes(b"img")
    receipt = _make_receipt(session, image_path=image)
    rid = receipt.id

    # When reparsing it
    job = reparse_receipt(session, rid, storage_dir=storage)

    # Then a new pending job points at the same image, the old receipt is gone,
    # and the image file is preserved on disk
    assert job is not None
    assert job.status == JobStatus.PENDING
    assert job.image_path == str(image)
    assert job.receipt_id is None
    assert session.get(Receipt, rid) is None
    assert session.query(LineItem).count() == 0
    assert image.exists()


def test_reparse_receipt_preserves_source(session, tmp_path) -> None:
    """Verify the reparse job keeps the original receipt's source value."""
    # Given a web-sourced receipt with an on-disk image
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "rp-src.png"
    image.write_bytes(b"img")
    receipt = _make_receipt(session, image_path=image)
    receipt.source = "web"
    session.commit()

    # When reparsing it
    job = reparse_receipt(session, receipt.id, storage_dir=storage)

    # Then the new job carries the same source
    assert job is not None
    assert job.source == "web"


def test_reparse_receipt_preserves_uploader(session, tmp_path) -> None:
    """Verify the reparse job keeps the original receipt's uploader attribution."""
    # Given a receipt uploaded by a known user with an on-disk image
    from cartlog.db.models import Role, User  # noqa: PLC0415

    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "rp-user.png"
    image.write_bytes(b"img")
    user = User(username="reparse-user", password_hash="x", role=Role.EDITOR)
    session.add(user)
    session.flush()
    receipt = _make_receipt(session, image_path=image)
    receipt.user_id = user.id
    session.commit()

    # When reparsing it
    job = reparse_receipt(session, receipt.id, storage_dir=storage)

    # Then the new job carries the same uploader so attribution survives the reparse
    assert job is not None
    assert job.user_id == user.id


def test_reparse_receipt_unknown_id_returns_none(session, tmp_path) -> None:
    """Verify reparsing a nonexistent id returns None and creates no job."""
    # Given an empty storage dir and no matching receipt
    storage = tmp_path / "storage"
    storage.mkdir()

    # When reparsing an id that does not exist
    job = reparse_receipt(session, 999, storage_dir=storage)

    # Then nothing happens
    assert job is None
    assert session.query(IngestionJob).count() == 0


def test_reparse_receipt_missing_image_raises_and_keeps_receipt(session, tmp_path) -> None:
    """Verify a missing image file aborts reparse before any destructive change."""
    # Given a receipt whose recorded image file does not exist on disk
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "gone.png"  # never written
    receipt = _make_receipt(session, image_path=image)
    rid = receipt.id

    # When reparsing it
    with pytest.raises(ReparseImageMissingError):
        reparse_receipt(session, rid, storage_dir=storage)

    # Then the receipt and its line items remain intact and no job was created
    assert session.get(Receipt, rid) is not None
    assert session.query(LineItem).count() == 1
    assert session.query(IngestionJob).count() == 0


def test_reparse_receipt_image_outside_storage_raises(session, tmp_path) -> None:
    """Verify an image path outside the storage dir is treated as unavailable."""
    # Given a receipt whose image lives outside the configured storage dir
    storage = tmp_path / "storage"
    storage.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"img")
    receipt = _make_receipt(session, image_path=outside)
    rid = receipt.id

    # When reparsing it
    with pytest.raises(ReparseImageMissingError):
        reparse_receipt(session, rid, storage_dir=storage)

    # Then the receipt and its line items remain intact and no job was created
    assert session.get(Receipt, rid) is not None
    assert session.query(LineItem).count() == 1
    assert session.query(IngestionJob).count() == 0


def test_image_file_available_true_only_inside_storage(tmp_path) -> None:
    """Verify image_file_available is True only for an existing file under storage_dir."""
    # Given a file inside storage and a file outside it
    storage = tmp_path / "storage"
    storage.mkdir()
    inside = storage / "in.png"
    inside.write_bytes(b"img")
    outside = tmp_path / "out.png"
    outside.write_bytes(b"img")

    # When / Then only the in-storage existing file is reported available
    assert image_file_available(str(inside), storage_dir=storage) is True
    assert image_file_available(str(outside), storage_dir=storage) is False
    assert image_file_available(str(storage / "missing.png"), storage_dir=storage) is False


# ---------------------------------------------------------------------------
# Normalization recompute on edit
# ---------------------------------------------------------------------------


def test_edit_recomputes_normalization(session) -> None:
    """Verify editing a line recomputes its normalization columns from unit/unit_size."""
    # Given a receipt with a line item that has no unit_size (so normalization is not_applicable)
    product = Product(canonical_name="milk", category=Category(name="dairy"))
    store = Store(chain_name="Safeway", location="Main St")
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 3, 1),
        total=Decimal("4.50"),
        currency="USD",
        image_path="/tmp/x.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status="parsed",
    )
    receipt.line_items.append(
        LineItem(
            product=product,
            raw_description="MILK",
            quantity=Decimal(1),
            unit="ea",
            unit_size=None,
            unit_price=Decimal("4.50"),
            line_total=Decimal("4.50"),
        )
    )
    session.add(receipt)
    session.commit()

    # When applying an edit that supplies a volume unit_size
    line = receipt.line_items[0]
    edit = ReceiptEdit(
        chain_name="Safeway",
        location="Main St",
        purchase_date=date(2026, 3, 1),
        total=Decimal("4.50"),
        currency="USD",
        lines=[
            LineEdit(
                line_id=line.id,
                raw_description="MILK 1.5L",
                canonical_name="milk",
                category_id=None,
                quantity=Decimal(1),
                unit="ea",
                unit_size="1.5L",
                unit_price=Decimal("4.50"),
                line_total=Decimal("4.50"),
            )
        ],
    )
    apply_receipt_edit(session, receipt, edit)

    # Then normalization columns are recomputed from the new unit_size
    session.refresh(line)
    assert line.measure_status == "resolved"
    assert line.measure_dimension == "volume"
    assert line.normalized_unit_price == Decimal("0.003000")


# ---------------------------------------------------------------------------
# Parse cost durability tests
# ---------------------------------------------------------------------------


def test_delete_receipt_keeps_parse_cost_events(session, tmp_path) -> None:
    """Verify deleting a receipt leaves its parse cost event in the ledger."""
    # Given a persisted receipt and a cost event recorded for its parse (no FK to receipt)
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "cost-abc123.png"
    image.write_bytes(b"img")
    receipt = _make_receipt(session, image_path=image)
    session.add(ParseCostEvent(job_id=None, estimated_cost_usd=Decimal("0.05")))
    session.commit()

    # When the receipt is deleted
    delete_receipt(session, receipt.id, storage_dir=storage)

    # Then the cost event survives; spend is not erased
    assert session.query(ParseCostEvent).count() == 1
    assert session.query(ParseCostEvent).one().estimated_cost_usd == Decimal("0.05")


def test_reparse_receipt_keeps_prior_parse_cost_events(session, tmp_path) -> None:
    """Verify reparsing a receipt keeps the original parse's cost event in the ledger."""
    # Given a persisted receipt with a stored image and a recorded cost event
    storage = tmp_path / "storage"
    storage.mkdir()
    image = storage / "rp-cost-abc123.png"
    image.write_bytes(b"img")
    receipt = _make_receipt(session, image_path=image)
    session.add(ParseCostEvent(job_id=None, estimated_cost_usd=Decimal("0.05")))
    session.commit()

    # When the receipt is reparsed (deletes the old receipt and enqueues a fresh job)
    reparse_receipt(session, receipt.id, storage_dir=storage)

    # Then the original parse's cost event is still counted
    assert session.query(ParseCostEvent).count() == 1
    assert session.query(ParseCostEvent).one().estimated_cost_usd == Decimal("0.05")
