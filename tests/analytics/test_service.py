"""Unit tests for the AnalyticsService query methods."""

from datetime import date
from decimal import Decimal

from cartlog.analytics.search_sort import SearchSortKey
from cartlog.analytics.service import AnalyticsService
from cartlog.categories.service import UNCATEGORIZED_NAME
from cartlog.db.models import (
    Category,
    LineItem,
    Product,
    Receipt,
    ReceiptStatus,
    Store,
)


def test_price_history_orders_by_date_and_includes_needs_review(analytics_session):
    """Verify price_history returns every counted purchase in date order with a summary."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When querying egg prices over time
    result = svc.price_history("eggs")

    # Then the failed receipt is excluded and the rest are ordered oldest-first
    assert result.product == "eggs"
    assert [p.unit_price for p in result.points] == [
        Decimal("3.00"),
        Decimal("2.50"),
        Decimal("3.20"),
    ]
    # And the needs_review purchase is flagged but still counted
    assert result.points[-1].needs_review is True
    assert result.points[0].needs_review is False
    # And the summary reflects the three counted points
    assert result.min_unit_price == Decimal("2.50")
    assert result.max_unit_price == Decimal("3.20")
    assert result.avg_unit_price == Decimal("2.9")


def test_price_history_filters_by_store(analytics_session):
    """Verify the store filter matches the chain name case-insensitively."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When filtering eggs to the Safeway chain
    result = svc.price_history("eggs", store="safeway")

    # Then only Safeway purchases are returned
    assert [p.unit_price for p in result.points] == [Decimal("3.00"), Decimal("3.20")]


def test_price_history_filters_by_date_range_inclusive(analytics_session):
    """Verify start/end bounds are inclusive."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When restricting to February
    result = svc.price_history("eggs", start=date(2026, 2, 1), end=date(2026, 2, 28))

    # Then only the February purchase remains
    assert [p.unit_price for p in result.points] == [Decimal("2.50")]


def test_price_history_unknown_product_is_empty(analytics_session):
    """Verify an unmatched product yields an empty-but-valid result, not an error."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When querying a product that does not exist
    result = svc.price_history("caviar")

    # Then the result is empty with null summaries
    assert result.points == []
    assert result.min_unit_price is None
    assert result.avg_unit_price is None


def test_price_history_product_name_is_case_insensitive(analytics_session):
    """Verify product lookup matches the canonical_name regardless of input case."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When querying with an uppercased product name
    result_upper = svc.price_history("EGGS")

    # Then the same three price points are returned as for the lowercase name
    result_lower = svc.price_history("eggs")
    assert [p.unit_price for p in result_upper.points] == [
        p.unit_price for p in result_lower.points
    ]
    assert len(result_upper.points) == 3


def test_store_comparison_aggregates_per_store_cheapest_first(analytics_session):
    """Verify store_comparison groups by store and orders by average price ascending."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When comparing egg prices across stores
    result = svc.store_comparison("eggs")

    # Then Costco (cheaper average) comes before Safeway
    assert [r.store_chain for r in result.rows] == ["Costco", "Safeway"]

    costco, safeway = result.rows
    assert costco.avg_unit_price == Decimal("2.50")
    assert costco.purchase_count == 1
    # Safeway has two egg purchases: 3.00 (Jan) and 3.20 (Mar)
    assert safeway.avg_unit_price == Decimal("3.1")
    assert safeway.min_unit_price == Decimal("3.00")
    assert safeway.max_unit_price == Decimal("3.20")
    assert safeway.latest_unit_price == Decimal("3.20")
    assert safeway.purchase_count == 2


def test_store_comparison_unknown_product_is_empty(analytics_session):
    """Verify an unmatched product yields no rows."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When comparing a product that does not exist
    result = svc.store_comparison("caviar")

    # Then there are no rows
    assert result.rows == []


def test_category_spend_full_breakdown_highest_first(analytics_session):
    """Verify category_spend sums line totals per category, biggest spend first."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When totaling spend across all categories
    result = svc.category_spend()

    # Then dairy (10.70) leads produce (4.00) and the failed receipt is excluded
    assert [(r.category, r.total_spend) for r in result.rows] == [
        ("dairy", Decimal("10.70")),
        ("produce", Decimal("4.00")),
    ]
    assert result.total_spend == Decimal("14.70")


def test_category_spend_single_category_with_store_filter(analytics_session):
    """Verify filtering to one category and store narrows the total."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When totaling dairy spend at Costco only
    result = svc.category_spend("dairy", store="costco")

    # Then only Costco's eggs (2.50) count
    assert [(r.category, r.total_spend) for r in result.rows] == [("dairy", Decimal("2.50"))]
    assert result.total_spend == Decimal("2.50")


def test_category_spend_unknown_category_is_empty(analytics_session):
    """Verify an unmatched category yields no rows and a zero total."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When totaling a category that does not exist
    result = svc.category_spend("electronics")

    # Then the result is empty
    assert result.rows == []
    assert result.total_spend == Decimal(0)


def test_category_spend_filters_by_date_range(analytics_session):
    """Verify start date filter restricts results to matching receipts only."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When totaling spend from March 2026 onward (r3: eggs 3.20 + milk 2.00; r4 is FAILED)
    result = svc.category_spend(start=date(2026, 3, 1))

    # Then only dairy from the March receipt appears; produce had no March lines
    assert [(r.category, r.total_spend) for r in result.rows] == [
        ("dairy", Decimal("5.20")),
    ]
    assert result.total_spend == Decimal("5.20")


def test_category_spend_category_filter_is_case_insensitive(analytics_session):
    """Verify category lookup matches regardless of input case."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When querying with an uppercased category name
    result_upper = svc.category_spend("DAIRY")

    # Then the same rows are returned as for the lowercase name
    result_lower = svc.category_spend("dairy")
    assert [(r.category, r.total_spend) for r in result_upper.rows] == [
        (r.category, r.total_spend) for r in result_lower.rows
    ]
    assert len(result_upper.rows) == 1


def test_category_spend_excludes_uncategorized_but_reports_it(session):
    """Verify Uncategorized is excluded from the breakdown but reported as unclassified spend."""
    # Given a counted receipt with one produce line and one Uncategorized line
    produce = Category(name="produce")
    uncategorized = Category(name=UNCATEGORIZED_NAME, is_system=True)
    bananas = Product(canonical_name="bananas", category=produce)
    mystery = Product(canonical_name="mystery item", category=uncategorized)
    store = Store(chain_name="Safeway", location="Main St")
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 1, 15),
        total=Decimal("6.00"),
        currency="USD",
        image_path="/tmp/x.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status=ReceiptStatus.PARSED,
    )
    receipt.line_items.extend(
        [
            LineItem(
                product=bananas,
                raw_description="BANANAS",
                quantity=Decimal(2),
                unit_price=Decimal("1.00"),
                line_total=Decimal("2.00"),
            ),
            LineItem(
                product=mystery,
                raw_description="???",
                quantity=Decimal(1),
                unit_price=Decimal("4.00"),
                line_total=Decimal("4.00"),
            ),
        ]
    )
    session.add_all([produce, uncategorized, store, receipt])
    session.commit()
    svc = AnalyticsService(session)

    # When totaling spend across all categories
    result = svc.category_spend()

    # Then the Uncategorized bucket is absent from the breakdown but its spend is reported
    categories = {r.category for r in result.rows}
    assert UNCATEGORIZED_NAME not in categories
    assert result.unclassified_spend == Decimal("4.00")
    # And the breakdown total covers only the classified rows
    assert result.total_spend == Decimal("2.00")


def test_category_spend_explicit_uncategorized_filter_returns_it(session):
    """Verify filtering explicitly to Uncategorized still returns its spend row."""
    # Given a counted receipt with a single Uncategorized line
    uncategorized = Category(name=UNCATEGORIZED_NAME, is_system=True)
    mystery = Product(canonical_name="mystery item", category=uncategorized)
    store = Store(chain_name="Safeway", location="Main St")
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 1, 15),
        total=Decimal("4.00"),
        currency="USD",
        image_path="/tmp/x.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status=ReceiptStatus.PARSED,
    )
    receipt.line_items.append(
        LineItem(
            product=mystery,
            raw_description="???",
            quantity=Decimal(1),
            unit_price=Decimal("4.00"),
            line_total=Decimal("4.00"),
        )
    )
    session.add_all([uncategorized, store, receipt])
    session.commit()
    svc = AnalyticsService(session)

    # When filtering explicitly to the Uncategorized category
    result = svc.category_spend(UNCATEGORIZED_NAME)

    # Then the Uncategorized row is returned and not stripped as unclassified
    assert [(r.category, r.total_spend) for r in result.rows] == [
        (UNCATEGORIZED_NAME, Decimal("4.00"))
    ]
    assert result.unclassified_spend == Decimal(0)


def test_search_matches_raw_and_canonical_case_insensitive(analytics_session):
    """Verify search matches raw description and canonical name, most recent first."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When searching for "egg"
    results = svc.search("egg")

    # Then all three counted egg line items return, newest first, failed excluded
    assert [r.purchase_date for r in results] == [
        date(2026, 3, 5),
        date(2026, 2, 10),
        date(2026, 1, 15),
    ]
    assert all(r.canonical_name == "eggs" for r in results)
    assert results[0].needs_review is True


def test_search_matches_store_name(analytics_session):
    """Verify search also matches the store chain name."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When searching for a store
    results = svc.search("costco")

    # Then both Costco line items return
    assert {r.canonical_name for r in results} == {"eggs", "apples"}


def test_search_respects_limit(analytics_session):
    """Verify the limit caps the number of returned rows."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When searching broadly with a small limit
    results = svc.search("egg", limit=2)

    # Then only two rows return
    assert len(results) == 2


def test_search_matches_raw_description_arm_only(analytics_session):
    """Verify search matches raw_description even when the term is absent from canonical_name."""
    # Given the seeded dataset where "KS" appears only in raw_description "KS EGGS" (Costco
    # eggs, r2) and not in any canonical_name, store chain, or category name
    svc = AnalyticsService(analytics_session)

    # When searching for "KS"
    results = svc.search("KS")

    # Then exactly the Costco eggs line item is returned
    assert len(results) == 1
    assert results[0].raw_description == "KS EGGS"
    assert results[0].canonical_name == "eggs"


def test_search_matches_category_arm_only(analytics_session):
    """Verify search matches category name even when the term is absent from raw_description, canonical_name, and store chain."""
    # Given the seeded dataset where "dairy" is a category but does not appear in any
    # raw_description ("LRG EGGS 12CT", "KS EGGS", "EGGS LARGE", "2% MILK", "BANANAS",
    # "ORGANIC APPLES"), canonical_name, or store chain name
    svc = AnalyticsService(analytics_session)

    # When searching for "dairy"
    results = svc.search("dairy")

    # Then all four counted dairy line items are returned (r1 eggs, r2 eggs, r3 eggs, r3 milk);
    # r4 eggs is FAILED and must be excluded
    assert len(results) == 4
    assert all(r.category == "dairy" for r in results)


def test_search_escapes_like_wildcards(analytics_session):
    """Verify a percent sign in the search text matches literally, not as a SQL wildcard."""
    # Given the seeded dataset containing "2% MILK" (r3) and "LRG EGGS 12CT" (r1), both of
    # which an unescaped LIKE pattern "%2%%" would match
    svc = AnalyticsService(analytics_session)

    # When searching for the literal "2%"
    results = svc.search("2%")

    # Then only "2% MILK" matches; "LRG EGGS 12CT" (which merely contains "2") does not
    assert [r.raw_description for r in results] == ["2% MILK"]


def test_search_result_carries_line_item_and_category_ids(analytics_session):
    """Verify each search row exposes its line_item_id and the product's category_id."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When searching for an item with a known category ('eggs' is 'dairy')
    results = svc.search("eggs")

    # Then every row carries a positive line_item_id and the dairy category's id
    assert results
    assert all(r.line_item_id > 0 for r in results)
    dairy = analytics_session.query(Category).filter_by(name="dairy").one()
    assert all(r.category_id == dairy.id for r in results)


def test_search_sorts_by_unit_price_ascending(analytics_session):
    """Verify sort=unit_price ascending orders rows by unit price low to high."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When searching broadly, sorted by unit price ascending
    results = svc.search("eggs", sort=SearchSortKey.UNIT_PRICE, descending=False)

    # Then unit prices are non-decreasing
    prices = [r.unit_price for r in results]
    assert prices == sorted(prices)


def test_search_sorts_by_product_descending(analytics_session):
    """Verify sort=product descending orders rows by canonical name Z to A, case-insensitively."""
    # Given the seeded dataset
    svc = AnalyticsService(analytics_session)

    # When searching across categories (dairy matches eggs + milk), sorted by product desc
    results = svc.search("dairy", sort=SearchSortKey.PRODUCT, descending=True)

    # Then canonical names are non-increasing (lower-cased)
    names = [r.canonical_name.lower() for r in results]
    assert names == sorted(names, reverse=True)


def test_line_item_row_projects_single_line(analytics_session):
    """Verify line_item_row returns the same projection as search for one line id."""
    # Given a known eggs line from the dataset
    svc = AnalyticsService(analytics_session)
    any_row = svc.search("eggs")[0]

    # When fetching that single line by id
    row = svc.line_item_row(any_row.line_item_id)

    # Then every projected field matches the search row
    assert row == any_row


def test_line_item_row_failed_receipt_returns_none(analytics_session):
    """Verify line_item_row excludes lines on non-counted (failed) receipts, like search."""
    # Given a line item on the seeded failed receipt
    svc = AnalyticsService(analytics_session)
    failed_line = (
        analytics_session.query(LineItem)
        .join(Receipt, LineItem.receipt_id == Receipt.id)
        .filter(Receipt.status == ReceiptStatus.FAILED)
        .first()
    )
    assert failed_line is not None

    # When fetching that line by id
    # Then it is excluded, matching search()
    assert svc.line_item_row(failed_line.id) is None


def test_line_item_row_unknown_id_returns_none(analytics_session):
    """Verify line_item_row returns None for an id that does not exist."""
    # Given the dataset
    svc = AnalyticsService(analytics_session)

    # When fetching a non-existent line id
    # Then None comes back
    assert svc.line_item_row(999_999) is None


def test_product_names_are_alphabetical(analytics_session):
    """Verify product_names returns every canonical name in case-insensitive order."""
    # Given the dataset (apples, bananas, eggs, milk)
    svc = AnalyticsService(analytics_session)

    # When listing product names
    names = svc.product_names()

    # Then they are the canonical names, alphabetically
    assert names == sorted(names, key=str.lower)
    assert "eggs" in names
