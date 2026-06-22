"""Tests for the two-store comparison service and its helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cartlog.analytics.results import PriceBasis, ScaleMode, StorePairSort
from cartlog.analytics.service import AnalyticsService, _median_price, _store_label
from cartlog.db.models import Category, LineItem, Product, Receipt, ReceiptStatus, Store
from cartlog.parsing.structuring import parse_size
from cartlog.units import SoldBy, compute_measure


def _line(product, *, unit_size, line_total):
    """Build a normalized line item from a package size string and total.

    Parses `unit_size` (e.g. "1.5L", "500g") into structured (size_amount, size_unit) fields
    and uses compute_measure to derive the normalized measure columns.
    """
    parsed = parse_size(unit_size)
    size_amount = parsed[0] if parsed else None
    size_unit = parsed[1] if parsed else None
    norm = compute_measure(
        sold_by=SoldBy.ITEM,
        quantity=Decimal(1),
        measure_unit=None,
        size_amount=size_amount,
        size_unit=size_unit,
        line_total=Decimal(line_total),
    )
    return LineItem(
        product=product,
        raw_description=product.canonical_name.upper(),
        quantity=Decimal(1),
        sold_by=SoldBy.ITEM,
        size_amount=size_amount,
        size_unit=size_unit,
        unit_price=Decimal(line_total),
        line_total=Decimal(line_total),
        measure_quantity=norm.measure_quantity,
        measure_dimension=norm.measure_dimension,
        normalized_unit_price=norm.normalized_unit_price,
        measure_status=norm.measure_status,
    )


def _receipt(store, day, line):
    """Build a PARSED receipt carrying one line at a store on a date."""
    r = Receipt(
        store=store,
        purchase_date=day,
        total=line.line_total,
        currency="USD",
        image_path="/tmp/x.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status=ReceiptStatus.PARSED,
    )
    r.line_items.append(line)
    return r


def _two_store_milk(session):
    """Seed milk at two stores (A cheaper per ml) and return (svc, a_id, b_id)."""
    milk = Product(canonical_name="milk", category=Category(name="dairy"))
    a = Store(chain_name="Acut", location="A")  # cheaper
    b = Store(chain_name="Bmart", location="B")  # pricier
    session.add(
        _receipt(a, date(2026, 1, 1), _line(milk, unit_size="1.5L", line_total="4.50"))
    )  # 0.003/ml
    session.add(
        _receipt(b, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.50"))
    )  # 0.0035/ml
    session.commit()
    return AnalyticsService(session), a.id, b.id


def test_median_price_odd_count_returns_middle():
    """Verify the median of an odd-length price list is the middle value, quantized to 6dp."""
    # Given three normalized prices
    prices = [Decimal("0.010"), Decimal("0.030"), Decimal("0.012")]

    # When taking the median
    result = _median_price(prices)

    # Then the middle value is returned at 6-decimal precision
    assert result == Decimal("0.012000")


def test_median_price_ignores_outlier_sale():
    """Verify a single low sale price does not drag the median the way a mean would."""
    # Given a sale outlier among otherwise stable prices
    prices = [Decimal("0.010"), Decimal("0.011"), Decimal("0.012"), Decimal("0.001")]

    # When taking the median
    result = _median_price(prices)

    # Then the result sits among the typical prices, not pulled toward the outlier
    assert result == Decimal("0.010500")


def test_store_label_joins_chain_and_location_with_comma():
    """Verify the store label joins chain and location with a comma, not an em-dash."""
    # Given a chain with a location
    # When building the label
    # Then chain and location are comma-joined; a missing location yields the bare chain
    assert _store_label("Costco", "Airport Rd") == "Costco, Airport Rd"
    assert _store_label("Costco", None) == "Costco"


def test_stores_by_frequency_orders_by_counted_receipts(session):
    """Verify stores_by_frequency ranks stores by their counted-receipt count, most first."""
    # Given two stores where one has more counted receipts
    busy = Store(chain_name="Busy", location="A")
    quiet = Store(chain_name="Quiet", location="B")
    for _ in range(2):
        session.add(
            Receipt(
                store=busy,
                purchase_date=date(2026, 1, 1),
                total=Decimal(1),
                currency="USD",
                image_path="/tmp/x.png",  # noqa: S108
                raw_parser_json="{}",
                source="cli",
                status=ReceiptStatus.PARSED,
            )
        )
    session.add(
        Receipt(
            store=quiet,
            purchase_date=date(2026, 1, 1),
            total=Decimal(1),
            currency="USD",
            image_path="/tmp/x.png",  # noqa: S108
            raw_parser_json="{}",
            source="cli",
            status=ReceiptStatus.PARSED,
        )
    )
    session.commit()

    # When listing stores by frequency
    result = AnalyticsService(session).stores_by_frequency()

    # Then the busier store comes first and carries its label and count
    assert [s.chain_name for s in result] == ["Busy", "Quiet"]
    assert result[0].label == "Busy, A"
    assert result[0].receipt_count == 2


def test_store_pair_comparison_builds_comparable_row(session):
    """Verify a product carried by both stores yields one comparable row with the gap and direction."""
    # Given milk at two stores, cheaper per ml at A
    svc, a_id, b_id = _two_store_milk(session)

    # When comparing the two stores
    result = svc.store_pair_comparison(a_id, b_id)

    # Then there is one comparable row, B is the pricier side, gap is ~16.67%
    assert result.store_a == "Acut, A"
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.canonical_name == "milk"
    assert row.price_a == Decimal("0.003000")
    assert row.price_b == Decimal("0.003500")
    assert row.pricier == "b"
    assert round(row.pct_diff, 2) == 16.67
    assert row.bar_fraction == 1.0  # the only/widest gap fills the percent axis


def test_store_pair_comparison_latest_basis_uses_most_recent(session):
    """Verify basis=LATEST picks the most recent in-range price, not the median."""
    # Given milk at store A twice (older cheap, newer pricey) and once at B
    milk = Product(canonical_name="milk", category=Category(name="dairy"))
    a = Store(chain_name="Acut", location="A")
    b = Store(chain_name="Bmart", location="B")
    session.add(
        _receipt(a, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.00"))
    )  # 0.003/ml
    session.add(
        _receipt(a, date(2026, 3, 1), _line(milk, unit_size="1L", line_total="4.00"))
    )  # 0.004/ml latest
    session.add(_receipt(b, date(2026, 2, 1), _line(milk, unit_size="1L", line_total="3.50")))
    session.commit()
    svc = AnalyticsService(session)

    # When comparing with the latest basis
    result = svc.store_pair_comparison(a.id, b.id, basis=PriceBasis.LATEST)

    # Then store A's representative price is the most recent (0.004/ml), not the 0.0035 median
    assert result.rows[0].price_a == Decimal("0.004000")


def test_store_pair_comparison_only_one_store_is_unmatched(session):
    """Verify a product sold at only one store is bucketed, not compared."""
    # Given milk at both stores but bread only at A
    milk = Product(canonical_name="milk", category=Category(name="dairy"))
    bread = Product(canonical_name="bread", category=Category(name="bakery"))
    a = Store(chain_name="Acut", location="A")
    b = Store(chain_name="Bmart", location="B")
    session.add(_receipt(a, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.00")))
    session.add(_receipt(b, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.50")))
    session.add(_receipt(a, date(2026, 1, 2), _line(bread, unit_size="500g", line_total="2.00")))
    session.commit()
    svc = AnalyticsService(session)

    # When comparing the two stores
    result = svc.store_pair_comparison(a.id, b.id)

    # Then milk is comparable and bread lands in only_a, counted in unmatched_count
    assert {r.canonical_name for r in result.rows} == {"milk"}
    assert [u.canonical_name for u in result.only_a] == ["bread"]
    assert result.unmatched_count == 1


def test_store_pair_comparison_dimension_mismatch_is_unmatched(session):
    """Verify a product measured by weight at one store and count at the other cannot be compared."""
    # Given "eggs" sold by weight at A and by count at B
    eggs = Product(canonical_name="eggs", category=Category(name="dairy"))
    a = Store(chain_name="Acut", location="A")
    b = Store(chain_name="Bmart", location="B")
    session.add(
        _receipt(a, date(2026, 1, 1), _line(eggs, unit_size="500g", line_total="3.00"))
    )  # weight
    session.add(
        _receipt(b, date(2026, 1, 1), _line(eggs, unit_size="12ct", line_total="3.50"))
    )  # count
    session.commit()
    svc = AnalyticsService(session)

    # When comparing the two stores
    result = svc.store_pair_comparison(a.id, b.id)

    # Then eggs are not comparable; they fall into the mismatched bucket flagged as different units
    assert result.rows == []
    assert [u.canonical_name for u in result.mismatched] == ["eggs"]
    assert result.mismatched[0].reason == "different_units"
    assert result.unmatched_count == 1


def test_store_pair_comparison_unresolved_at_both_reason_needs_unit(session):
    """Verify a product with no unit size at either store is flagged as fixable (needs_unit)."""
    # Given pasta with no parseable size at either store, so normalization does not apply
    pasta = Product(canonical_name="pasta", category=Category(name="pasta & rice"))
    a = Store(chain_name="Acut", location="A")
    b = Store(chain_name="Bmart", location="B")
    bare_a = LineItem(
        product=pasta,
        raw_description="DECECCO SPAGHETTI",
        quantity=Decimal(1),
        unit_price=Decimal("4.65"),
        line_total=Decimal("4.65"),
    )
    bare_b = LineItem(
        product=pasta,
        raw_description="BARILLA",
        quantity=Decimal(1),
        unit_price=Decimal("1.67"),
        line_total=Decimal("1.67"),
    )
    session.add(_receipt(a, date(2026, 1, 1), bare_a))
    session.add(_receipt(b, date(2026, 1, 1), bare_b))
    session.commit()
    svc = AnalyticsService(session)

    # When comparing the two stores
    result = svc.store_pair_comparison(a.id, b.id)

    # Then pasta is not comparable but is flagged as fixable by adding a unit size
    assert result.rows == []
    assert [u.canonical_name for u in result.mismatched] == ["pasta"]
    assert result.mismatched[0].reason == "needs_unit"


def test_store_pair_comparison_dollar_scale_groups_by_dimension(session):
    """Verify dollar scale fills bar_fraction per dimension group, each group's widest gap = 1.0."""
    # Given a weight product and a volume product, each comparable at both stores
    dairy = Category(name="dairy")
    butter = Product(canonical_name="butter", category=dairy)
    milk = Product(canonical_name="milk", category=dairy)
    a = Store(chain_name="Acut", location="A")
    b = Store(chain_name="Bmart", location="B")
    session.add(_receipt(a, date(2026, 1, 1), _line(butter, unit_size="500g", line_total="5.00")))
    session.add(_receipt(b, date(2026, 1, 1), _line(butter, unit_size="500g", line_total="6.00")))
    session.add(_receipt(a, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.00")))
    session.add(_receipt(b, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.50")))
    session.commit()
    svc = AnalyticsService(session)

    # When comparing on the dollar scale
    result = svc.store_pair_comparison(a.id, b.id, scale=ScaleMode.DOLLAR)

    # Then each product is the widest gap within its own dimension group, so each bar is full
    assert {r.canonical_name: r.bar_fraction for r in result.rows} == {"butter": 1.0, "milk": 1.0}
    assert set(result.dollar_group_max) == {"weight", "volume"}


def test_store_pair_comparison_sort_largest_first(session):
    """Verify sort=LARGEST orders comparable rows by widest percent gap first."""
    # Given two comparable products with different percent gaps
    milk = Product(canonical_name="milk", category=Category(name="dairy"))
    juice = Product(canonical_name="juice", category=Category(name="drinks"))
    a = Store(chain_name="Acut", location="A")
    b = Store(chain_name="Bmart", location="B")
    session.add(_receipt(a, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.00")))
    session.add(
        _receipt(b, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.15"))
    )  # +5%
    session.add(_receipt(a, date(2026, 1, 1), _line(juice, unit_size="1L", line_total="2.00")))
    session.add(
        _receipt(b, date(2026, 1, 1), _line(juice, unit_size="1L", line_total="3.00"))
    )  # +50%
    session.commit()
    svc = AnalyticsService(session)

    # When sorting by largest difference
    result = svc.store_pair_comparison(a.id, b.id, sort=StorePairSort.LARGEST)

    # Then juice (50%) precedes milk (5%)
    assert [r.canonical_name for r in result.rows] == ["juice", "milk"]


def test_stores_by_frequency_excludes_non_counted_receipts(session):
    """Verify stores_by_frequency counts only PARSED/NEEDS_REVIEW receipts, not FAILED ones."""
    # Given a store with one PARSED receipt and one FAILED receipt
    store = Store(chain_name="Mixed", location="C")
    session.add(
        Receipt(
            store=store,
            purchase_date=date(2026, 1, 1),
            total=Decimal(1),
            currency="USD",
            image_path="/tmp/x.png",  # noqa: S108
            raw_parser_json="{}",
            source="cli",
            status=ReceiptStatus.PARSED,
        )
    )
    session.add(
        Receipt(
            store=store,
            purchase_date=date(2026, 1, 2),
            total=Decimal(1),
            currency="USD",
            image_path="/tmp/y.png",  # noqa: S108
            raw_parser_json="{}",
            source="cli",
            status=ReceiptStatus.FAILED,
        )
    )
    session.commit()

    # When listing stores by frequency
    result = AnalyticsService(session).stores_by_frequency()

    # Then the store's receipt_count is 1 (the FAILED receipt is excluded)
    assert len(result) == 1
    assert result[0].chain_name == "Mixed"
    assert result[0].receipt_count == 1


def test_store_pair_comparison_sort_smallest_first(session):
    """Verify sort=SMALLEST orders comparable rows by narrowest percent gap first."""
    # Given two comparable products with different percent gaps
    milk = Product(canonical_name="milk", category=Category(name="dairy"))
    juice = Product(canonical_name="juice", category=Category(name="drinks"))
    a = Store(chain_name="Acut", location="A")
    b = Store(chain_name="Bmart", location="B")
    session.add(_receipt(a, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.00")))
    session.add(
        _receipt(b, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.15"))
    )  # +5%
    session.add(_receipt(a, date(2026, 1, 1), _line(juice, unit_size="1L", line_total="2.00")))
    session.add(
        _receipt(b, date(2026, 1, 1), _line(juice, unit_size="1L", line_total="3.00"))
    )  # +50%
    session.commit()
    svc = AnalyticsService(session)

    # When sorting by smallest difference
    result = svc.store_pair_comparison(a.id, b.id, sort=StorePairSort.SMALLEST)

    # Then milk (5%) precedes juice (50%)
    assert [r.canonical_name for r in result.rows] == ["milk", "juice"]


def test_store_pair_comparison_filters_and_options(session):
    """Verify product/category filters narrow rows while options reflect the full two-store universe."""
    # Given milk (dairy) and juice (drinks) comparable at both stores
    dairy = Category(name="dairy")
    drinks = Category(name="drinks")
    milk = Product(canonical_name="milk", category=dairy)
    juice = Product(canonical_name="juice", category=drinks)
    a = Store(chain_name="Acut", location="A")
    b = Store(chain_name="Bmart", location="B")
    session.add(_receipt(a, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.00")))
    session.add(_receipt(b, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.50")))
    session.add(_receipt(a, date(2026, 1, 1), _line(juice, unit_size="1L", line_total="2.00")))
    session.add(_receipt(b, date(2026, 1, 1), _line(juice, unit_size="1L", line_total="2.50")))
    session.commit()
    svc = AnalyticsService(session)

    # When filtering to the dairy category only
    result = svc.store_pair_comparison(a.id, b.id, category_ids=[dairy.id])

    # Then only milk is a row, but both products remain available as options
    assert [r.canonical_name for r in result.rows] == ["milk"]
    assert result.product_options == ["juice", "milk"]
    assert (dairy.id, "dairy") in result.category_options


def test_store_pair_comparison_identical_prices_does_not_crash(session):
    """Verify a product priced identically at both stores yields a zero-width same-price row."""
    # Given milk at the same normalized price at both stores (every gap is zero)
    milk = Product(canonical_name="milk", category=Category(name="dairy"))
    a = Store(chain_name="Acut", location="A")
    b = Store(chain_name="Bmart", location="B")
    session.add(_receipt(a, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.00")))
    session.add(_receipt(b, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.00")))
    session.commit()
    svc = AnalyticsService(session)

    # When comparing on the percent scale
    result = svc.store_pair_comparison(a.id, b.id, scale=ScaleMode.PERCENT)

    # Then the row is "same" with a zero-width bar and no ZeroDivisionError is raised
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.pricier == "same"
    assert row.pct_diff == 0.0
    assert row.bar_fraction == 0.0


def test_store_pair_comparison_blank_product_filter_is_ignored(session):
    """Verify a blank product filter value means 'no filter' rather than matching nothing."""
    # Given milk comparable at both stores
    milk = Product(canonical_name="milk", category=Category(name="dairy"))
    a = Store(chain_name="Acut", location="A")
    b = Store(chain_name="Bmart", location="B")
    session.add(_receipt(a, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.00")))
    session.add(_receipt(b, date(2026, 1, 1), _line(milk, unit_size="1L", line_total="3.50")))
    session.commit()
    svc = AnalyticsService(session)

    # When the product filter carries only a blank value
    result = svc.store_pair_comparison(a.id, b.id, product_names=[""])

    # Then it behaves as no filter and milk still appears
    assert [r.canonical_name for r in result.rows] == ["milk"]
