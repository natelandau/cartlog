"""Result types returned by the analytics service."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


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
    measure_status: str = "not_applicable"


class PriceHistory(BaseModel):
    """A product's price across all counted purchases, with a summary."""

    product: str
    points: list[PricePoint]
    min_unit_price: Decimal | None
    max_unit_price: Decimal | None
    avg_unit_price: Decimal | None


class StoreComparisonRow(BaseModel):
    """Aggregated price stats for one product at one store."""

    store_chain: str
    store_location: str | None
    avg_unit_price: Decimal
    min_unit_price: Decimal
    max_unit_price: Decimal
    latest_unit_price: Decimal
    purchase_count: int
    avg_normalized_unit_price: Decimal | None = None
    measure_dimension: str | None = None
    normalized_count: int = 0


class StoreComparison(BaseModel):
    """A product's price compared across stores, cheapest average first."""

    product: str
    rows: list[StoreComparisonRow]


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
    measure_status: str = "not_applicable"


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
