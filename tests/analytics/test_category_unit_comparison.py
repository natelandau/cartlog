"""Tests for category_unit_comparison ranking products by normalized price."""

from datetime import date
from decimal import Decimal

from cartlog.analytics.service import AnalyticsService
from cartlog.db.models import Category, LineItem, Product, Receipt, ReceiptStatus, Store
from cartlog.units import SoldBy, compute_measure


def _line(product, *, sold_by, measure_unit=None, size_amount=None, size_unit=None, qty, total):
    norm = compute_measure(
        sold_by=sold_by,
        quantity=Decimal(qty),
        measure_unit=measure_unit,
        size_amount=size_amount,
        size_unit=size_unit,
        line_total=Decimal(total),
    )
    return LineItem(
        product=product,
        raw_description=product.canonical_name,
        quantity=Decimal(qty),
        sold_by=sold_by,
        measure_unit=measure_unit,
        size_amount=size_amount,
        size_unit=size_unit,
        unit_price=Decimal(total),
        line_total=Decimal(total),
        measure_quantity=norm.measure_quantity,
        measure_dimension=norm.measure_dimension,
        normalized_unit_price=norm.normalized_unit_price,
        measure_status=norm.measure_status,
    )


def test_category_units_ranks_weight_products(session):
    """Verify weight products in a category rank cheapest-per-gram first."""
    produce = Category(name="produce")
    bananas = Product(canonical_name="bananas", category=produce)
    grapes = Product(canonical_name="grapes", category=produce)
    store = Store(chain_name="Safeway", location="Main St")
    r = Receipt(
        store=store,
        purchase_date=date(2026, 1, 1),
        total=Decimal(10),
        currency="USD",
        image_path="/tmp/x.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status=ReceiptStatus.PARSED,
    )
    # bananas 2 lb @ 1.00 (cheap/g); grapes 1 lb @ 4.00 (dear/g)
    r.line_items.append(
        _line(bananas, sold_by=SoldBy.MEASURE, measure_unit="lb", qty="2", total="1.00")
    )
    r.line_items.append(
        _line(grapes, sold_by=SoldBy.MEASURE, measure_unit="lb", qty="1", total="4.00")
    )
    session.add(r)
    session.commit()

    result = AnalyticsService(session).category_unit_comparison("produce")
    names = [row.canonical_name for row in result.weight_rows]
    assert names == ["bananas", "grapes"]  # cheapest per gram first
    assert result.volume_rows == []


def test_category_units_excludes_resolved_row_with_null_price(session):
    """Verify a RESOLVED row with a NULL normalized price is excluded, not crashed on."""
    # Given a category with one healthy weight row and one integrity-violating row
    # (status RESOLVED but normalized_unit_price NULL) that must not reach the Decimal sum.
    produce = Category(name="produce")
    bananas = Product(canonical_name="bananas", category=produce)
    broken = Product(canonical_name="broken", category=produce)
    store = Store(chain_name="Safeway", location="Main St")
    r = Receipt(
        store=store,
        purchase_date=date(2026, 1, 1),
        total=Decimal(5),
        currency="USD",
        image_path="/tmp/x.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status=ReceiptStatus.PARSED,
    )
    r.line_items.append(
        _line(bananas, sold_by=SoldBy.MEASURE, measure_unit="lb", qty="2", total="1.00")
    )
    r.line_items.append(
        LineItem(
            product=broken,
            raw_description="broken",
            quantity=Decimal(1),
            sold_by=SoldBy.MEASURE,
            measure_unit="lb",
            unit_price=Decimal("4.00"),
            line_total=Decimal("4.00"),
            measure_quantity=Decimal("453.5920"),
            measure_dimension="weight",
            normalized_unit_price=None,  # integrity violation: resolved but no price
            measure_status="resolved",
        )
    )
    session.add(r)
    session.commit()

    # When ranking the category by normalized price
    result = AnalyticsService(session).category_unit_comparison("produce")

    # Then the broken row is silently excluded and the healthy row still ranks
    names = [row.canonical_name for row in result.weight_rows]
    assert names == ["bananas"]
