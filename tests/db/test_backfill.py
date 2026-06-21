"""Tests for the normalize_existing_measures backfill function."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cartlog.db.backfill import normalize_existing_measures, recompute_product_typical_sizes
from cartlog.db.models import Category, LineItem, Product, Receipt, Store
from cartlog.units import MeasureSource, MeasureStatus


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


def _seed(session, *, unit, unit_size):
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
            unit=unit,
            unit_size=unit_size,
            unit_price=Decimal("4.50"),
            line_total=Decimal("4.50"),
        )
    )
    session.add(receipt)
    session.commit()
    return receipt.line_items[0]


def test_backfill_resolves_from_unit_size(session):
    """Verify backfill computes normalized columns from stored unit_size."""
    # Given a line item with a measurable unit_size but no normalization yet
    line = _seed(session, unit="ea", unit_size="1.5L")

    # When the backfill runs
    updated = normalize_existing_measures(session)
    session.refresh(line)

    # Then the normalization columns are populated and the count is correct
    assert updated == 1
    assert line.measure_status == "resolved"
    assert line.normalized_unit_price == Decimal("0.003000")


def test_backfill_is_idempotent(session):
    """Verify a second backfill run detects no further changes."""
    # Given a line item that has already been normalized
    _seed(session, unit="ea", unit_size="1.5L")
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
    assert extracted.unit_size == "16oz"  # the recovered size is persisted

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

    # Then it did not raise, the deterministic pass still resolved its line, and the size-less
    # line is simply left unresolved rather than crashing the backfill
    assert deterministic.measure_status == MeasureStatus.RESOLVED
    assert deterministic.measure_source == MeasureSource.EXTRACTED
    assert sizeless.measure_status != MeasureStatus.RESOLVED


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
            unit="lb",
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
