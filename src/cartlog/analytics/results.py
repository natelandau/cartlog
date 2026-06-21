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
    unit: str | None
    unit_size: str | None
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
