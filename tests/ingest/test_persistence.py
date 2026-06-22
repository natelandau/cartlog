"""Tests for persisting parsed receipts and deduplicating related rows."""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Query

from cartlog.categories.service import UNCATEGORIZED_NAME, CategoryService
from cartlog.db.base import Base
from cartlog.db.models import Category, LineItem, Product, Receipt, Store
from cartlog.db.session import create_session_factory
from cartlog.ingest.persistence import _get_or_create, persist_receipt
from cartlog.parsing.schema import ParsedLineItem, ParsedReceipt
from cartlog.units import MeasureSource, MeasureStatus, SoldBy


def _receipt_with(item: ParsedLineItem) -> ParsedReceipt:
    return ParsedReceipt(
        store_name="Safeway",
        store_location="Main St",
        purchase_date=date(2026, 3, 1),
        currency="USD",
        total=item.line_total,
        confidence=0.9,
        line_items=[item],
    )


def test_persist_populates_normalized_columns_from_llm_measure(session):
    """Verify normalization columns are populated from LLM-provided measure on persist."""
    parsed = _receipt_with(
        ParsedLineItem(
            raw_description="MILK 1.5L",
            canonical_name="milk",
            category="dairy & eggs",
            quantity=1,
            unit="ea",
            unit_size="1.5L",
            measure_value=1.5,
            measure_unit="l",
            unit_price=4.5,
            line_total=4.5,
        )
    )
    receipt, _ = persist_receipt(
        session,
        parsed,
        image_path="/tmp/x.png",  # noqa: S108
        source="cli",
        status="parsed",
        raw_json="{}",
    )
    session.flush()
    line = receipt.line_items[0]
    assert line.measure_status == "resolved"
    assert line.measure_dimension == "volume"
    assert line.measure_quantity == Decimal("1500.0000")
    assert line.normalized_unit_price == Decimal("0.003000")


def test_persist_resolves_unsized_line_per_each(session):
    """Verify a line item with no unit or size is resolved as per-each (count dimension)."""
    # Under the structured model, every ITEM line resolves to at least $/each even when
    # no per-item size is present; NOT_APPLICABLE no longer applies to item lines.
    parsed = _receipt_with(
        ParsedLineItem(
            raw_description="APPLE",
            canonical_name="apple",
            category="produce",
            quantity=1,
            unit_price=0.99,
            line_total=0.99,
        )
    )
    receipt, _ = persist_receipt(
        session,
        parsed,
        image_path="/tmp/x.png",  # noqa: S108
        source="cli",
        status="parsed",
        raw_json="{}",
    )
    session.flush()
    line = receipt.line_items[0]
    assert line.measure_status == MeasureStatus.RESOLVED
    assert line.measure_dimension == "count"
    assert line.sold_by == SoldBy.ITEM


def test_persist_receipt_creates_rows(session, sample_parsed_receipt):
    """Verify persisting a receipt creates the receipt, stores, products, and categories."""
    receipt, _unmapped = persist_receipt(
        session,
        sample_parsed_receipt,
        image_path="receipt_images/a.png",
        source="cli",
        status="parsed",
        raw_json='{"k": "v"}',
    )
    session.commit()

    assert receipt.id is not None
    assert receipt.total == Decimal("6.96")
    assert receipt.status == "parsed"
    assert len(receipt.line_items) == 2
    assert session.query(Store).count() == 1
    assert session.query(Product).count() == 2
    eggs = session.query(Product).filter_by(canonical_name="eggs").one()
    assert eggs.category.name == "Uncategorized"


def test_persist_records_original_category_on_line_items(session, sample_parsed_receipt):
    """Verify each line item stores the parser's verbatim original category guess."""
    # Given the sample receipt (eggs -> 'dairy & eggs', bananas -> 'produce')
    # When persisting it
    receipt, _unmapped = persist_receipt(
        session,
        sample_parsed_receipt,
        image_path="a.png",
        source="cli",
        status="parsed",
        raw_json="{}",
    )
    session.commit()

    # Then each line item carries the original category string the parser returned,
    # independent of how it resolved against the taxonomy
    originals = {li.raw_description: li.original_category for li in receipt.line_items}
    assert originals == {"GV LRG EGGS 12CT": "dairy & eggs", "BANANAS": "produce"}


def test_persist_receipt_reuses_existing_store_and_product(session, sample_parsed_receipt):
    """Verify ingesting the same receipt twice deduplicates stores and products."""
    persist_receipt(
        session,
        sample_parsed_receipt,
        image_path="a.png",
        source="cli",
        status="parsed",
        raw_json="{}",
    )
    persist_receipt(
        session,
        sample_parsed_receipt,
        image_path="b.png",
        source="cli",
        status="parsed",
        raw_json="{}",
    )
    session.commit()

    # Two receipts, but stores and products are deduplicated.
    assert session.query(Receipt).count() == 2
    assert session.query(Store).count() == 1
    assert session.query(Product).count() == 2
    assert session.query(LineItem).count() == 4


def test_persist_resolves_known_category_and_reports_unmapped(
    session, sample_parsed_receipt
) -> None:
    """Verify known categories resolve and unknown ones route to Uncategorized and are reported."""
    # Given a taxonomy where 'dairy & eggs' exists but 'produce' does not
    svc = CategoryService(session)
    svc.create_category(name="dairy & eggs")
    session.flush()

    # When persisting a receipt whose line items are 'dairy & eggs' (known) and 'produce' (unknown)
    _receipt, unmapped = persist_receipt(
        session,
        sample_parsed_receipt,
        image_path="/x.png",
        source="test",
        status="parsed",
        raw_json="{}",
    )
    session.commit()

    # Then the known item resolves, the unknown goes to Uncategorized, and is reported
    eggs_product = session.query(Product).filter_by(canonical_name="eggs").one()
    assert eggs_product.category.name == "dairy & eggs"
    bananas = session.query(Product).filter_by(canonical_name="bananas").one()
    assert bananas.category.name == UNCATEGORIZED_NAME
    assert unmapped == ["produce"]
    # Ingest never auto-creates a category
    assert session.query(Category).filter_by(name="produce").count() == 0


def test_persist_blank_category_not_reported_as_unmapped(session) -> None:
    """Verify a line item with a blank category string is not added to the unmapped list."""
    # Given a parsed receipt whose single line item has an empty category string
    parsed = ParsedReceipt(
        store_name="TestMart",
        store_location=None,
        purchase_date=date(2026, 5, 1),
        currency="USD",
        total=1.99,
        confidence=0.90,
        line_items=[
            ParsedLineItem(
                raw_description="MYSTERY ITEM",
                canonical_name="mystery item",
                category="",
                quantity=1,
                unit_price=1.99,
                line_total=1.99,
            ),
        ],
    )

    # When persisting the receipt with no taxonomy seeded
    _receipt, unmapped = persist_receipt(
        session,
        parsed,
        image_path="/tmp/x.png",  # noqa: S108
        source="test",
        status="parsed",
        raw_json="{}",
    )
    session.commit()

    # Then the product resolves to Uncategorized and the blank string is not reported as unmapped
    product = session.query(Product).filter_by(canonical_name="mystery item").one()
    assert product.category.name == UNCATEGORIZED_NAME
    assert unmapped == []


def test_get_or_create_resolves_concurrent_unique_collision(tmp_path, mocker) -> None:
    """Verify _get_or_create resolves to the existing row when a concurrent insert wins the unique race."""
    # Given two sessions on a shared on-disk database (in-memory SQLite cannot be shared
    # across connections, and the race only exists between separate connections)
    factory = create_session_factory(f"sqlite:///{tmp_path / 'race.db'}")
    engine = factory.kw["bind"]
    with factory() as setup:
        Base.metadata.create_all(setup.get_bind())
    session_a = factory()
    session_b = factory()
    try:
        # Given a competing worker has already created and committed "bananas"
        session_a.add(Product(canonical_name="bananas"))
        session_a.commit()
        winner_id = session_a.query(Product).filter_by(canonical_name="bananas").one().id

        # Given worker B's lookup misses it (simulating A committing in B's read->insert window)
        stale = mocker.patch.object(Query, "one_or_none", autospec=True)
        stale.side_effect = [None]  # only the get-or-create lookup is mocked; the retry uses one()

        # When B get-or-creates the same name, its insert hits the unique constraint
        product = _get_or_create(session_b, Product, canonical_name="bananas")
        mocker.stopall()  # restore real queries for the assertions below
        session_b.commit()

        # Then B resolves to the committed row instead of raising or creating a duplicate
        assert product.id == winner_id
        assert session_b.query(Product).filter_by(canonical_name="bananas").count() == 1
    finally:
        session_b.close()
        session_a.close()
        engine.dispose()


def test_persist_sets_measure_source_extracted_from_description(session):
    """Verify a line whose size lives only in the description is extracted and marked EXTRACTED."""
    # Given a receipt with a line item whose size is only in the raw description
    parsed = ParsedReceipt(
        store_name="Test",
        purchase_date=date(2026, 1, 1),
        currency="USD",
        total=5.0,
        confidence=0.9,
        line_items=[
            ParsedLineItem(
                raw_description="Granola, Maple Sea Salt, 11oz, Bob's",
                canonical_name="granola",
                category="",
                quantity=1.0,
                unit=None,
                unit_size=None,
                measure_value=None,
                measure_unit=None,
                unit_price=5.0,
                line_total=5.0,
            )
        ],
    )

    # When persisting the receipt
    receipt, _ = persist_receipt(
        session,
        parsed,
        image_path="x.png",
        source="test",
        status="parsed",
        raw_json="{}",
    )
    session.flush()

    # Then the line item is EXTRACTED with the correct structured size
    line = receipt.line_items[0]
    assert line.measure_source == MeasureSource.EXTRACTED
    assert line.measure_status == MeasureStatus.RESOLVED
    assert line.size_amount == Decimal(11)
    assert line.size_unit == "oz"


def test_ingest_persists_structured_measure(session):
    """Verify structured measure fields are populated correctly from a packaged line item on persist."""
    # Given a receipt with a packaged line item: quantity=2, unit="ea", unit_size="16oz", line_total=20.60
    parsed = _receipt_with(
        ParsedLineItem(
            raw_description="GRANOLA BAR 16oz",
            canonical_name="granola bar",
            category="snacks",
            quantity=2,
            unit="ea",
            unit_size="16oz",
            measure_value=None,
            measure_unit=None,
            unit_price=10.30,
            line_total=20.60,
        )
    )

    # When persisting the receipt
    receipt, _ = persist_receipt(
        session,
        parsed,
        image_path="/tmp/x.png",  # noqa: S108
        source="cli",
        status="parsed",
        raw_json="{}",
    )
    session.flush()

    # Then the line item carries structured measure fields
    line = receipt.line_items[0]
    assert line.sold_by == SoldBy.ITEM
    assert (line.size_amount, line.size_unit) == (Decimal(16), "oz")
    assert line.measure_dimension == "weight"
