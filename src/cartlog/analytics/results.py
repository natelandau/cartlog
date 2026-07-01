"""Result types returned by the analytics service."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from cartlog.units import MeasureStatus


class PricePoint(BaseModel):
    """One purchase of a product on a specific receipt."""

    purchase_date: date
    store_chain: str
    store_location: str | None
    unit_price: Decimal
    quantity: Decimal
    line_total: Decimal
    receipt_id: int
    needs_review: bool
    normalized_unit_price: Decimal | None = None
    measure_dimension: str | None = None
    measure_status: MeasureStatus = MeasureStatus.NOT_APPLICABLE


class PriceHistory(BaseModel):
    """A product's price across all counted purchases, with a summary."""

    product: str
    points: list[PricePoint]
    min_unit_price: Decimal | None
    max_unit_price: Decimal | None
    avg_unit_price: Decimal | None


class CategorySpendRow(BaseModel):
    """Total spend within a single category."""

    category: str
    total_spend: Decimal
    line_item_count: int


class CategorySpend(BaseModel):
    """Spend broken down by category, plus the overall total."""

    rows: list[CategorySpendRow]
    total_spend: Decimal
    unclassified_spend: Decimal = Decimal(0)


class SearchResult(BaseModel):
    """One line item matching a free-text search, with context."""

    raw_description: str
    canonical_name: str
    category: str | None
    category_id: int | None
    sold_by: str = "item"
    measure_unit: str | None = None
    size_amount: Decimal | None = None
    size_unit: str | None = None
    quantity: Decimal | None = None
    store_chain: str
    purchase_date: date
    unit_price: Decimal
    line_total: Decimal
    receipt_id: int
    line_item_id: int
    needs_review: bool
    normalized_unit_price: Decimal | None = None
    measure_dimension: str | None = None
    measure_status: MeasureStatus = MeasureStatus.NOT_APPLICABLE


class ParsingCostSummary(BaseModel):
    """Estimated LLM parsing cost over a range: total, priced-event count, and per-parse average."""

    total: Decimal
    receipt_count: int
    avg_per_receipt: Decimal


class ParsingCostOverview(BaseModel):
    """The three LLM-cost figures shown on the admin page."""

    total_all_time: Decimal
    total_last_30_days: Decimal
    avg_per_receipt: Decimal


class KpiCard(BaseModel):
    """A headline metric: preformatted value, optional sparkline series, optional delta."""

    label: str
    value: str
    points: list[Decimal]  # monthly series for the sparkline; empty hides it
    delta_pct: float | None  # vs the prior equivalent period; None when no prior data


class MonthlySpend(BaseModel):
    """Total spend and receipt count for one calendar month."""

    month: str  # "YYYY-MM"
    total: Decimal
    receipt_count: int


class HeatmapCell(BaseModel):
    """One day's spend, for the activity calendar."""

    day: date
    spend: Decimal


class TopProduct(BaseModel):
    """A product ranked by how often it is bought and how much is spent on it."""

    name: str
    purchase_count: int
    total_spend: Decimal


class StoreRow(BaseModel):
    """Per-store spend, visit count, and average spend per trip."""

    store_chain: str
    store_location: str | None
    visits: int
    total_spend: Decimal
    avg_per_trip: Decimal


class PriceTrendRow(BaseModel):
    """A product's unit-price trend for the price tables (movers and watch)."""

    product: str
    points: list[Decimal]  # unit price over time, oldest first
    current_price: Decimal
    change_pct: float | None


class CategoryUnitRow(BaseModel):
    """A product's average normalized price within a category, for one dimension."""

    canonical_name: str
    measure_dimension: str
    avg_normalized_unit_price: Decimal
    line_count: int


class CategoryUnitComparison(BaseModel):
    """Products in a category ranked by normalized price, weight and volume kept separate."""

    category: str
    weight_rows: list[CategoryUnitRow]
    volume_rows: list[CategoryUnitRow]


class MonthComparison(BaseModel):
    """This calendar month versus the previous one across three measures."""

    spend_this: Decimal
    spend_prev: Decimal
    spend_delta_pct: float | None  # vs the previous month; None when last month had no spend
    trips_this: int
    trips_prev: int
    items_this: int
    items_prev: int


class PriceBasis(StrEnum):
    """Which single price represents a store for a product over a date range."""

    TYPICAL = "typical"  # median normalized price, robust to sales
    LATEST = "latest"  # most recent normalized price in range


class ScaleMode(StrEnum):
    """How the comparison bar length is scaled."""

    PERCENT = "percent"  # one shared dimensionless axis across all rows
    DOLLAR = "dollar"  # per-dimension-group dollar axis


class StorePairSort(StrEnum):
    """Row ordering for the comparison table."""

    ALPHABETICAL = "alphabetical"
    LARGEST = "largest"  # widest percent gap first
    SMALLEST = "smallest"  # narrowest percent gap first


class StoreOption(BaseModel):
    """One selectable store for the comparison toolbar, ranked by receipt count."""

    id: int
    chain_name: str
    location: str | None
    label: str
    receipt_count: int


class StorePairRow(BaseModel):
    """One product comparable at both stores, with the gap and its bar geometry."""

    canonical_name: str
    measure_dimension: str
    price_a: Decimal  # metric-base normalized price at store A
    price_b: Decimal
    abs_diff: Decimal  # |price_a - price_b|, metric base
    pct_diff: float | None  # signed: positive means B is pricier; None when A is free
    pricier: str  # "a", "b", or "same"
    bar_fraction: float  # 0..1 width of the bar for the active scale


class StorePairUnmatched(BaseModel):
    """A product that cannot be compared: carried by one store only, or unresolved/mismatched.

    `reason` drives how the disclosure presents it and what fix it suggests:
    "only_store" (sold at one store), "needs_unit" (missing a unit size, so it can be made
    comparable by editing the line item), or "different_units" (resolved but in different
    dimensions, e.g. weight vs count).
    """

    canonical_name: str
    measure_dimension: str | None
    price: Decimal | None  # representative normalized price where one exists
    reason: str = "only_store"


class StorePairComparison(BaseModel):
    """A two-store normalized-price comparison: comparable rows plus the unmatched buckets."""

    store_a: str
    store_b: str
    store_a_id: int
    store_b_id: int
    scale: ScaleMode
    basis: PriceBasis
    sort: StorePairSort
    rows: list[StorePairRow]
    only_a: list[StorePairUnmatched]
    only_b: list[StorePairUnmatched]
    mismatched: list[StorePairUnmatched]
    unmatched_count: int
    product_options: list[str]
    category_options: list[tuple[int, str]]
    axis_max_pct: float  # widest percent gap in view, for the % caption
    dollar_group_max: dict[str, Decimal]  # dimension -> widest dollar gap, for the $ caption


class LineItemExportRow(BaseModel):
    """One line item flattened across its receipt, store, product, and category for export."""

    purchase_date: date
    store_chain: str
    store_location: str | None
    receipt_id: int
    receipt_status: str
    currency: str
    raw_description: str
    canonical_name: str
    category: str | None
    quantity: Decimal
    sold_by: str
    measure_unit: str | None
    size_amount: Decimal | None
    size_unit: str | None
    unit_price: Decimal
    line_total: Decimal
    measure_quantity: Decimal | None
    measure_dimension: str | None
    normalized_unit_price: Decimal | None
    measure_status: str


class DashboardData(BaseModel):
    """Everything the dashboard renders for a chosen time range (excluding recent table)."""

    range_label: str
    kpis: list[KpiCard]
    needs_review: int
    monthly_spend: list[MonthlySpend]
    heatmap: list[HeatmapCell]
    categories: list[CategorySpendRow]
    unclassified_spend: Decimal
    top_by_count: list[TopProduct]
    top_by_spend: list[TopProduct]
    stores: list[StoreRow]
    price_movers: list[PriceTrendRow]
    price_watch: list[PriceTrendRow]
    month_comparison: MonthComparison


class SpendGranularity(StrEnum):
    """Time bucket width for the spend-over-time analysis."""

    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class SpendSeries(StrEnum):
    """Which measure the spend-over-time chart plots."""

    TOTAL = "total"  # total itemized spend per bucket
    BY_CATEGORY = "category"  # spend per bucket stacked by category
    TRIPS = "trips"  # distinct receipt count per bucket
    AVG_BASKET = "avg"  # total spend / trips per bucket


class SpendBucket(BaseModel):
    """One time bucket of spend, with every measure precomputed so the renderer just picks one."""

    start: date  # bucket start; drives the date x-axis
    label: str  # human label for the axis/hover ("Jan 2026" or "Jan 5")
    total: Decimal  # summed line_total in the bucket
    trips: int  # distinct counted receipts in the bucket
    avg_basket: Decimal  # total / trips, or 0 when there were no trips


class SpendCategorySeries(BaseModel):
    """One category's spend across every bucket, aligned to the bucket order for stacking."""

    category: str
    values: list[Decimal]


class SpendOverTime(BaseModel):
    """Spend bucketed over time, with the toolbar's option lists and the stacked-category series."""

    granularity: SpendGranularity
    series: SpendSeries
    store_id: int | None
    store_label: str | None
    buckets: list[SpendBucket]
    category_series: list[SpendCategorySeries]  # populated only for the by-category series
    category_options: list[tuple[int, str]]  # (id, name) for the toolbar pills
    other_category_count: int  # categories folded into the "Other" stack, for an honest caption
    total_spend: Decimal
    uncategorized_spend: Decimal  # spend the by-category stack omits, disclosed so it still adds up


class ProductParetoMetric(StrEnum):
    """Which measure ranks the products in the top-products Pareto view."""

    SPEND = "spend"  # total itemized spend per product
    TRIPS = "trips"  # distinct receipts a product appears on


class ProductParetoRow(BaseModel):
    """One ranked product in the top-products view."""

    name: str
    value: Decimal  # the active metric; trips is carried as an integer-valued Decimal
    share_pct: float  # this product's share of the metric total, 0..100


class ProductPareto(BaseModel):
    """Products ranked by spend or trips, with each product's share and the toolbar's options."""

    metric: ProductParetoMetric
    rows: list[ProductParetoRow]  # the top-N products, ranked highest first
    category_options: list[tuple[int, str]]  # (id, name) for the toolbar pills
    product_total: int  # distinct products in range (rows is capped at the top N)
    pareto_count: int  # fewest products whose cumulative reaches >= 80%, for the headline
    grand_total: Decimal  # total of the metric across all products in range
    total_receipts: int  # distinct counted receipts in range; the real trip count for the headline
