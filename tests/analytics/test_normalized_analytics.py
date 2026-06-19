"""Tests that store_comparison uses normalized unit price for sorting."""

from datetime import date
from decimal import Decimal

from cartlog.analytics.service import AnalyticsService
from cartlog.db.models import Category, LineItem, Product, Receipt, ReceiptStatus, Store


def _milk_line(product, *, unit_size, line_total):
    from cartlog.units import normalize_line_item  # noqa: PLC0415

    norm = normalize_line_item(
        quantity=Decimal(1), unit="ea", unit_size=unit_size, line_total=Decimal(line_total)
    )
    return LineItem(
        product=product,
        raw_description="MILK",
        quantity=Decimal(1),
        unit="ea",
        unit_size=unit_size,
        unit_price=Decimal(line_total),
        line_total=Decimal(line_total),
        measure_quantity=norm.measure_quantity,
        measure_dimension=norm.measure_dimension,
        normalized_unit_price=norm.normalized_unit_price,
        measure_status=norm.measure_status,
    )


def test_store_comparison_uses_normalized_price(session):
    """Verify store_comparison sorts by normalized unit price and exposes avg_normalized_unit_price."""
    milk = Product(canonical_name="milk", category=Category(name="dairy"))
    cheap = Store(chain_name="Costco", location="A")
    pricey = Store(chain_name="Corner", location="B")
    # Costco: 1.5L @ 4.50 -> 0.003/ml. Corner: 1L @ 3.50 -> 0.0035/ml (more per ml).
    for store, size, total in [(cheap, "1.5L", "4.50"), (pricey, "1L", "3.50")]:
        r = Receipt(
            store=store,
            purchase_date=date(2026, 1, 1),
            total=Decimal(total),
            currency="USD",
            image_path="/tmp/x.png",  # noqa: S108
            raw_parser_json="{}",
            source="cli",
            status=ReceiptStatus.PARSED,
        )
        r.line_items.append(_milk_line(milk, unit_size=size, line_total=total))
        session.add(r)
    session.commit()

    result = AnalyticsService(session).store_comparison("milk")
    by_store = {row.store_chain: row for row in result.rows}
    assert by_store["Costco"].avg_normalized_unit_price == Decimal("0.003000")
    assert by_store["Corner"].avg_normalized_unit_price == Decimal("0.003500")
    # Cheaper-per-ml store sorts first once normalized.
    assert result.rows[0].store_chain == "Costco"


def _milk_line_priced(product, *, unit_size, unit_price, line_total):
    from cartlog.units import normalize_line_item  # noqa: PLC0415

    norm = normalize_line_item(
        quantity=Decimal(1), unit="ea", unit_size=unit_size, line_total=Decimal(line_total)
    )
    return LineItem(
        product=product,
        raw_description="MILK",
        quantity=Decimal(1),
        unit="ea",
        unit_size=unit_size,
        unit_price=Decimal(unit_price),
        line_total=Decimal(line_total),
        measure_quantity=norm.measure_quantity,
        measure_dimension=norm.measure_dimension,
        normalized_unit_price=norm.normalized_unit_price,
        measure_status=norm.measure_status,
    )


def test_store_comparison_free_item_sorts_first(session):
    """Verify a $0 normalized price sorts as cheapest rather than falling back to raw price."""
    # Given milk free at one store (coupon: list price 2.99 but $0 paid) and paid at another.
    # The free store's raw unit_price (2.99) exceeds the paid store's, so a falsy-zero fallback
    # to raw price would wrongly rank the free store last.
    milk = Product(canonical_name="milk", category=Category(name="dairy"))
    free = Store(chain_name="Freebie", location="A")
    paid = Store(chain_name="Paid", location="B")
    lines = {
        free: _milk_line_priced(milk, unit_size="1L", unit_price="2.99", line_total="0.00"),
        paid: _milk_line_priced(milk, unit_size="1L", unit_price="3.50", line_total="3.50"),
    }
    for store, line in lines.items():
        r = Receipt(
            store=store,
            purchase_date=date(2026, 1, 1),
            total=line.line_total,
            currency="USD",
            image_path="/tmp/x.png",  # noqa: S108
            raw_parser_json="{}",
            source="cli",
            status=ReceiptStatus.PARSED,
        )
        r.line_items.append(line)
        session.add(r)
    session.commit()

    # When comparing stores by normalized price
    result = AnalyticsService(session).store_comparison("milk")

    # Then the free item ($0/ml) sorts first, not behind the paid store via a raw-price fallback
    by_store = {row.store_chain: row for row in result.rows}
    assert by_store["Freebie"].avg_normalized_unit_price == Decimal("0.000000")
    assert result.rows[0].store_chain == "Freebie"
