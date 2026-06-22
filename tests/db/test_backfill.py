"""Tests for the normalize_existing_measures backfill function."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cartlog.db.backfill import normalize_existing_measures, recompute_product_typical_sizes
from cartlog.db.models import LineItem, Product, Receipt, Store
from cartlog.units import MeasureSource, MeasureStatus, SoldBy


def _attach_to_receipt(session, line: LineItem) -> None:
    """Create a Store + Receipt, append the line, and add the receipt to the session."""
    store = Store(chain_name="TestMart", location="Anywhere")
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 1, 1),
        total=Decimal("5.00"),
        currency="USD",
        image_path="/tmp/receipt.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status="parsed",
    )
    receipt.line_items.append(line)
    session.add(receipt)


@pytest.fixture
def product_factory(session):
    """Return a factory that creates and flushes a Product with a given canonical_name."""

    def make(canonical_name: str = "test product") -> Product:
        product = Product(canonical_name=canonical_name)
        session.add(product)
        session.flush()
        return product

    return make


@pytest.fixture
def line_factory(session):
    """Return a factory that creates a LineItem attached to a fresh receipt.

    Accepts the structured measure kwargs (sold_by, measure_unit, size_amount, size_unit,
    raw_description, measure_source) plus quantity, unit_price, and line_total. Creates a
    Product and Store/Receipt if not provided.
    """

    def make(
        *,
        product: Product | None = None,
        raw_description: str = "Test Item",
        quantity: Decimal = Decimal(1),
        unit_price: Decimal = Decimal("5.00"),
        line_total: Decimal = Decimal("5.00"),
        sold_by: SoldBy = SoldBy.ITEM,
        measure_unit: str | None = None,
        size_amount: Decimal | None = None,
        size_unit: str | None = None,
        measure_source: MeasureSource = MeasureSource.NONE,
        measure_status: MeasureStatus = MeasureStatus.NOT_APPLICABLE,
    ) -> LineItem:
        if product is None:
            product = Product(canonical_name=raw_description.lower())
            session.add(product)
        store = Store(chain_name="TestMart", location="Anywhere")
        receipt = Receipt(
            store=store,
            purchase_date=date(2026, 1, 1),
            total=line_total,
            currency="USD",
            image_path="/tmp/receipt.png",  # noqa: S108
            raw_parser_json="{}",
            source="cli",
            status="parsed",
        )
        line = LineItem(
            product=product,
            raw_description=raw_description,
            quantity=quantity,
            unit_price=unit_price,
            line_total=line_total,
            sold_by=sold_by,
            measure_unit=measure_unit,
            size_amount=size_amount,
            size_unit=size_unit,
            measure_source=measure_source,
            measure_status=measure_status,
        )
        receipt.line_items.append(line)
        session.add(receipt)
        session.flush()
        return line

    return make


def test_backfill_resolves_structured_size(session, line_factory):
    """Verify backfill recomputes derived columns from existing structured size fields."""
    # Given a line with structured size but no derived columns filled yet
    line = line_factory(
        raw_description="MILK",
        size_amount=Decimal("1.5"),
        size_unit="l",
        measure_source=MeasureSource.PRINTED,
        unit_price=Decimal("4.50"),
        line_total=Decimal("4.50"),
    )

    # When the backfill runs
    updated = normalize_existing_measures(session)
    session.refresh(line)

    # Then derived normalization columns are populated correctly
    assert updated == 1
    assert line.measure_status == MeasureStatus.RESOLVED
    # 1.5 L = 1500 ml; $4.50 / 1500 = $0.003000
    assert line.normalized_unit_price == Decimal("0.003000")


def test_backfill_is_idempotent(session, line_factory):
    """Verify a second backfill run on an already-normalized line changes nothing."""
    # Given a line that has already been normalized
    line_factory(
        raw_description="MILK",
        size_amount=Decimal("1.5"),
        size_unit="l",
        measure_source=MeasureSource.PRINTED,
        unit_price=Decimal("4.50"),
        line_total=Decimal("4.50"),
    )
    normalize_existing_measures(session)

    # When the backfill runs a second time
    second = normalize_existing_measures(session)

    # Then no rows are changed
    assert second == 0


def test_recompute_typical_size_sets_dominant_and_skips_ambiguous(session):
    """Verify dominant size is learned and ambiguous products are cleared to NULL."""
    store = Store(chain_name="TestMart", location="Anywhere")
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 1, 1),
        total=Decimal("10.00"),
        currency="USD",
        image_path="/tmp/r.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status="parsed",
    )

    # Helper: a resolved line attached to the shared receipt.
    def resolved_line(product, base, dim, source=MeasureSource.PRINTED):
        return LineItem(
            receipt=receipt,
            product=product,
            raw_description="x",
            quantity=Decimal(1),
            unit_price=Decimal(1),
            line_total=Decimal(1),
            measure_quantity=base,
            measure_dimension=dim,
            normalized_unit_price=Decimal(1),
            measure_status=MeasureStatus.RESOLVED,
            measure_source=source,
        )

    # Given a pasta product seen 3x at 453g (printed) and 1x at 900g (inferred, excluded)
    pasta = Product(canonical_name="pasta")
    # Given a soup product split evenly between two sizes (no dominance)
    soup = Product(canonical_name="soup")
    session.add_all([store, receipt, pasta, soup])
    for _ in range(3):
        session.add(resolved_line(pasta, Decimal("453.5920"), "weight"))
    session.add(resolved_line(pasta, Decimal("900.0000"), "weight", source=MeasureSource.INFERRED))
    session.add(resolved_line(soup, Decimal("300.0000"), "weight"))
    session.add(resolved_line(soup, Decimal("800.0000"), "weight"))
    session.flush()

    # When the typical-size learner runs
    recompute_product_typical_sizes(session)
    session.refresh(pasta)
    session.refresh(soup)

    # Then pasta learns the dominant size and soup is cleared
    assert pasta.typical_measure_value == Decimal("453.5920")
    assert pasta.typical_measure_dimension == "weight"
    assert soup.typical_measure_value is None


def test_backfill_pass1_extracts_size_from_description(session):
    """Verify pass 1 (deterministic) resolves a blank line whose size is in its description."""
    # Given a line with size embedded in the description and no structured size
    product = Product(canonical_name="granola")
    line = LineItem(
        product=product,
        raw_description="Granola 11oz Bob's",
        quantity=Decimal(1),
        unit_price=Decimal(5),
        line_total=Decimal(5),
        measure_status=MeasureStatus.NOT_APPLICABLE,
        measure_source=MeasureSource.NONE,
    )
    _attach_to_receipt(session, line)
    session.flush()

    # When the backfill runs without an LLM extractor (deterministic passes only)
    normalize_existing_measures(session)
    session.refresh(line)

    # Then the size is extracted from the description and the line is resolved
    assert line.measure_status == MeasureStatus.RESOLVED
    assert line.measure_source == MeasureSource.EXTRACTED


def test_backfill_preserves_manual_source(session):
    """Verify backfill never downgrades a human-pinned MANUAL line."""
    # Given a line that was manually pinned by a user
    product = Product(canonical_name="granola")
    line = LineItem(
        product=product,
        raw_description="Granola 11oz Bob's",
        quantity=Decimal(1),
        unit_price=Decimal(5),
        line_total=Decimal(5),
        measure_quantity=Decimal("311.84"),
        measure_dimension="weight",
        normalized_unit_price=Decimal("0.016"),
        measure_status=MeasureStatus.RESOLVED,
        measure_source=MeasureSource.MANUAL,
    )
    _attach_to_receipt(session, line)
    session.flush()

    # When the backfill runs
    normalize_existing_measures(session)
    session.refresh(line)

    # Then the manual source is preserved
    assert line.measure_source == MeasureSource.MANUAL


def test_backfill_is_idempotent_new(session):
    """Verify a second run with no new data changes zero rows."""
    # Given no line items in the database (empty DB)
    # When the backfill runs twice
    normalize_existing_measures(session)

    # Then the second run changes nothing
    assert normalize_existing_measures(session) == 0


def test_backfill_is_idempotent_with_extracted_and_inferred_mix(session):
    """Verify a converged mix of EXTRACTED and INFERRED lines is stable on a re-run."""
    # Given a product with two lines whose 16oz size is recoverable from text (-> EXTRACTED)
    # and one blank line that can only be filled by inference once the typical is learned.
    product = Product(canonical_name="granola")
    store = Store(chain_name="TestMart", location="Anywhere")
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 2, 1),
        total=Decimal("15.00"),
        currency="USD",
        image_path="/tmp/g.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status="parsed",
    )

    def blank_line(desc: str) -> LineItem:
        return LineItem(
            product=product,
            raw_description=desc,
            quantity=Decimal(1),
            unit_price=Decimal(5),
            line_total=Decimal(5),
            measure_status=MeasureStatus.NOT_APPLICABLE,
            measure_source=MeasureSource.NONE,
        )

    receipt.line_items.extend(
        [blank_line("Granola 16oz Bob's"), blank_line("Granola 16oz Bob's"), blank_line("Granola")]
    )
    session.add(receipt)
    session.commit()

    # And the backfill has run once to converge (recover the sizes, learn the typical, infer).
    normalize_existing_measures(session)
    lines = session.query(LineItem).order_by(LineItem.id).all()
    extracted = next(line for line in lines if line.measure_source == MeasureSource.EXTRACTED)
    inferred = next(line for line in lines if line.measure_source == MeasureSource.INFERRED)
    # The recovered size is persisted into structured columns (size_amount + size_unit).
    assert extracted.size_amount == Decimal(16)
    assert extracted.size_unit == "oz"

    # When the backfill runs twice more
    first = normalize_existing_measures(session)
    second = normalize_existing_measures(session)

    # Then it is stable: the second run changes nothing and provenance is preserved
    assert first == 0
    assert second == 0
    session.refresh(extracted)
    session.refresh(inferred)
    assert extracted.measure_source == MeasureSource.EXTRACTED
    assert extracted.measure_status == MeasureStatus.RESOLVED
    assert inferred.measure_source == MeasureSource.INFERRED
    assert inferred.measure_status == MeasureStatus.RESOLVED


def test_backfill_pass2_extractor_error_does_not_crash(session):
    """Verify a raising LLM size extractor in pass 2 is swallowed so startup never crashes."""

    # Given an extractor that always raises (e.g. a network/truncation failure at startup)
    class _RaisingExtractor:
        def extract(self, lines, *, usage=None):
            msg = "model exploded"
            raise ValueError(msg)

    # And one line whose size is recoverable deterministically and one genuinely size-less line
    product = Product(canonical_name="granola")
    deterministic = LineItem(
        product=product,
        raw_description="Granola 11oz Bob's",
        quantity=Decimal(1),
        unit_price=Decimal(5),
        line_total=Decimal(5),
        measure_status=MeasureStatus.NOT_APPLICABLE,
        measure_source=MeasureSource.NONE,
    )
    sizeless = LineItem(
        product=Product(canonical_name="mystery"),
        raw_description="Mystery Item",
        quantity=Decimal(1),
        unit_price=Decimal(5),
        line_total=Decimal(5),
        measure_status=MeasureStatus.NOT_APPLICABLE,
        measure_source=MeasureSource.NONE,
    )
    store = Store(chain_name="TestMart", location="Anywhere")
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 1, 1),
        total=Decimal("10.00"),
        currency="USD",
        image_path="/tmp/r.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status="parsed",
    )
    receipt.line_items.extend([deterministic, sizeless])
    session.add(receipt)
    session.commit()

    # When the backfill runs with the raising extractor configured
    normalize_existing_measures(session, size_extractor=_RaisingExtractor())
    session.refresh(deterministic)
    session.refresh(sizeless)

    # Then it did not raise, the deterministic pass still resolved its line. The size-less line
    # has no per-item size (size_amount stays None) rather than crashing the backfill.
    assert deterministic.measure_status == MeasureStatus.RESOLVED
    assert deterministic.measure_source == MeasureSource.EXTRACTED
    assert sizeless.size_amount is None


def test_recompute_excludes_sold_by_weight_lines(session):
    """Verify sold-by-weight lines do not seed a typical size (per_package is just the factor)."""
    # Given a product sold loose by the pound on two receipts (no package size)
    product = Product(canonical_name="deli ham")
    store = Store(chain_name="TestMart", location="Anywhere")
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 1, 1),
        total=Decimal("10.00"),
        currency="USD",
        image_path="/tmp/r.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status="parsed",
    )

    def weight_line(qty: str) -> LineItem:
        quantity = Decimal(qty)
        return LineItem(
            receipt=receipt,
            product=product,
            raw_description="HAM",
            quantity=quantity,
            sold_by=SoldBy.MEASURE,
            measure_unit="lb",
            unit_price=Decimal(8),
            line_total=Decimal(8) * quantity,
            # measure_quantity is quantity * grams-per-lb, so per_package would be the constant
            # 453.592 for every weight-sold line regardless of how many pounds were bought.
            measure_quantity=(quantity * Decimal("453.592")).quantize(Decimal("0.0001")),
            measure_dimension="weight",
            normalized_unit_price=Decimal("0.0176"),
            measure_status=MeasureStatus.RESOLVED,
            measure_source=MeasureSource.PRINTED,
        )

    session.add(receipt)
    session.add_all([weight_line("2"), weight_line("3")])
    session.flush()

    # When the typical-size learner runs
    recompute_product_typical_sizes(session)
    session.refresh(product)

    # Then no typical size is learned from the conversion-factor per_package values
    assert product.typical_measure_value is None
    assert product.typical_measure_dimension is None


def test_backfill_fills_structured_size_from_text(session, line_factory):
    """Verify pass 1 extracts size_amount/size_unit from raw_description into structured columns."""
    # Given a blank ITEM line with the size embedded in the description
    line = line_factory(
        raw_description="Soda 2L",
        sold_by=SoldBy.ITEM,
        size_amount=None,
        size_unit=None,
        measure_source=MeasureSource.NONE,
    )

    # When the backfill runs
    normalize_existing_measures(session)
    session.refresh(line)

    # Then structured size columns are populated from the description text
    assert (line.size_amount, line.size_unit) == (Decimal(2), "l")
    assert line.measure_source == MeasureSource.EXTRACTED


def test_backfill_skips_manual_lines(session, line_factory):
    """Verify backfill never modifies a MANUAL line even when it lacks a size."""
    # Given a MANUAL blank line with a size-bearing description
    line = line_factory(
        raw_description="Soda 2L",
        sold_by=SoldBy.ITEM,
        size_amount=None,
        size_unit=None,
        measure_source=MeasureSource.MANUAL,
    )

    # When the backfill runs
    normalize_existing_measures(session)
    session.refresh(line)

    # Then the line is untouched; MANUAL is never overwritten
    assert line.size_amount is None
    assert line.measure_source == MeasureSource.MANUAL


def test_typical_inference_fills_per_each_only_line(session):
    """Verify a blank line infers its size once two siblings establish a dominant typical."""
    # Given a product with two blank lines whose size is recoverable from text (500 g per item)
    product = Product(canonical_name="oatmeal")
    store = Store(chain_name="TestMart", location="Anywhere")
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 1, 1),
        total=Decimal("15.00"),
        currency="USD",
        image_path="/tmp/r.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status="parsed",
    )

    def blank_line(desc: str) -> LineItem:
        return LineItem(
            product=product,
            raw_description=desc,
            quantity=Decimal(1),
            unit_price=Decimal(5),
            line_total=Decimal(5),
            measure_status=MeasureStatus.NOT_APPLICABLE,
            measure_source=MeasureSource.NONE,
        )

    # Two lines with "500g" in the description; a third with no size clue at all
    first = blank_line("Oatmeal 500g")
    second = blank_line("Oatmeal 500g")
    third = blank_line("Oatmeal")
    receipt.line_items.extend([first, second, third])
    session.add(receipt)
    session.commit()

    # When the backfill runs (pass 1 extracts from text, pass 3 learns typical, pass 4 infers)
    normalize_existing_measures(session)
    session.refresh(third)

    # Then the third line has its dimension inferred from the learned typical size
    assert third.measure_dimension == "weight"
    assert third.measure_source == MeasureSource.INFERRED
