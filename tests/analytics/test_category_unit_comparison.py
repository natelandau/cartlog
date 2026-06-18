# tests/analytics/test_category_unit_comparison.py
from datetime import date
from decimal import Decimal

from cartlog.analytics.service import AnalyticsService
from cartlog.db.models import Category, LineItem, Product, Receipt, ReceiptStatus, Store
from cartlog.units import normalize_line_item


def _line(product, *, unit, unit_size, qty, total):
    norm = normalize_line_item(
        quantity=Decimal(qty), unit=unit, unit_size=unit_size, line_total=Decimal(total)
    )
    return LineItem(
        product=product,
        raw_description=product.canonical_name,
        quantity=Decimal(qty),
        unit=unit,
        unit_size=unit_size,
        unit_price=Decimal(total),
        line_total=Decimal(total),
        measure_quantity=norm.measure_quantity,
        measure_dimension=norm.measure_dimension,
        normalized_unit_price=norm.normalized_unit_price,
        measure_status=norm.measure_status,
    )


def test_category_units_ranks_weight_products(session):
    produce = Category(name="produce")
    bananas = Product(canonical_name="bananas", category=produce)
    grapes = Product(canonical_name="grapes", category=produce)
    store = Store(chain_name="Safeway", location="Main St")
    r = Receipt(
        store=store,
        purchase_date=date(2026, 1, 1),
        total=Decimal("10"),
        currency="USD",
        image_path="/tmp/x.png",
        raw_parser_json="{}",
        source="cli",
        status=ReceiptStatus.PARSED,
    )
    # bananas 2 lb @ 1.00 (cheap/g); grapes 1 lb @ 4.00 (dear/g)
    r.line_items.append(_line(bananas, unit="lb", unit_size=None, qty="2", total="1.00"))
    r.line_items.append(_line(grapes, unit="lb", unit_size=None, qty="1", total="4.00"))
    session.add(r)
    session.commit()

    result = AnalyticsService(session).category_unit_comparison("produce")
    names = [row.canonical_name for row in result.weight_rows]
    assert names == ["bananas", "grapes"]  # cheapest per gram first
    assert result.volume_rows == []
