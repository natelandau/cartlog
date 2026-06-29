"""Read-only analytics queries over persisted receipt data."""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, or_
from sqlalchemy.orm import contains_eager, selectinload

from cartlog.analytics.ranges import RangePreset, prior_range, range_label, resolve_range
from cartlog.analytics.results import (
    CategorySpend,
    CategorySpendRow,
    CategoryUnitComparison,
    CategoryUnitRow,
    DashboardData,
    HeatmapCell,
    KpiCard,
    LineItemExportRow,
    MonthComparison,
    MonthlySpend,
    ParsingCostOverview,
    ParsingCostSummary,
    PriceBasis,
    PriceHistory,
    PricePoint,
    PriceTrendRow,
    ScaleMode,
    SearchResult,
    SpendBucket,
    SpendCategorySeries,
    SpendGranularity,
    SpendOverTime,
    SpendSeries,
    StoreOption,
    StorePairComparison,
    StorePairRow,
    StorePairSort,
    StorePairUnmatched,
    StoreRow,
    TopProduct,
)
from cartlog.analytics.search_sort import SEARCH_SORT_COLUMNS, SearchSortKey
from cartlog.categories.service import UNCATEGORIZED_NAME
from cartlog.clock import naive_utcnow
from cartlog.constants import VOLUME, WEIGHT
from cartlog.db.models import (
    Category,
    LineItem,
    ParseCostEvent,
    Product,
    Receipt,
    ReceiptStatus,
    Store,
)
from cartlog.db.query_helpers import escape_like
from cartlog.units import MeasureStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Query, Session

# Receipts whose line items count toward analytics. A needs_review purchase is real
# spend; excluding it would silently understate every figure.
COUNTED_STATUSES = (ReceiptStatus.PARSED, ReceiptStatus.NEEDS_REVIEW)

# Minimum number of price points required to draw a meaningful trend line or half-split.
_MIN_PRICE_POINTS = 2

# How many categories the by-category stack shows individually; the rest fold into "Other".
# Six matches the theme palette so every stacked band gets a distinct on-brand color.
_SPEND_TOP_CATEGORIES = 6

# December: the month after which a monthly bucket rolls into the next year.
_DECEMBER = 12


def _price_stats(prices: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    """Return (min, max, average) for a non-empty list of prices.

    Averaging is done over Decimal to avoid the float drift SQL AVG would introduce.
    """
    return min(prices), max(prices), sum(prices, Decimal(0)) / len(prices)


def _median_price(prices: list[Decimal]) -> Decimal:
    """Return the median normalized price, quantized to the stored 6-decimal precision.

    Median (not mean) is used so a one-off sale or bulk buy does not skew a store's
    representative price.
    """
    median = statistics.median(prices)
    return Decimal(median).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _store_label(chain: str, location: str | None) -> str:
    """Render a store's display name, joining chain and location with a comma."""
    return f"{chain}, {location}" if location else chain


def _unmatched(
    name: str, chosen: tuple[str, Decimal] | None, *, reason: str = "only_store"
) -> StorePairUnmatched:
    """Build an unmatched-product entry from a store's representative (dimension, price), if any."""
    return StorePairUnmatched(
        canonical_name=name,
        measure_dimension=chosen[0] if chosen else None,
        price=chosen[1] if chosen else None,
        reason=reason,
    )


def _pct_more(*, base: Decimal, other: Decimal) -> float | None:
    """Return how much pricier `other` is than `base`, in signed percent.

    None when the base price is zero (a free item), so the caller renders a max-width
    'cannot compute a ratio' bar instead of dividing by zero.
    """
    if base == 0:
        return None
    return float((other - base) / base * Decimal(100))


def _sort_comparable(rows: list[StorePairRow], *, sort: StorePairSort) -> None:
    """Order comparable rows in place.

    Largest/smallest use absolute percent difference as the universal, dimension-free
    magnitude; a free-item None magnitude sorts as the widest gap.
    """
    if sort == StorePairSort.ALPHABETICAL:
        rows.sort(key=lambda r: r.canonical_name.lower())
        return

    def magnitude(row: StorePairRow) -> float:
        return float("inf") if row.pct_diff is None else abs(row.pct_diff)

    rows.sort(key=magnitude, reverse=sort == StorePairSort.LARGEST)


def _month_key(day: date) -> str:
    """Return the 'YYYY-MM' bucket key for a date."""
    return f"{day.year:04d}-{day.month:02d}"


def _bucket_start(day: date, granularity: SpendGranularity) -> date:
    """Return the start date of the time bucket `day` falls in.

    Weekly buckets start on Monday (weekday()==0); monthly on the first of the month; yearly
    on January 1st.
    """
    if granularity == SpendGranularity.WEEKLY:
        return day - timedelta(days=day.weekday())
    if granularity == SpendGranularity.YEARLY:
        return date(day.year, 1, 1)
    return date(day.year, day.month, 1)


def _bucket_label(start: date, granularity: SpendGranularity) -> str:
    """Render a bucket's axis/hover label, year-qualified so labels are unique across years.

    The chart uses these labels as categorical x-values, so two buckets sharing a label would
    collapse onto one bar; the year keeps every weekly/monthly/yearly bucket distinct.
    """
    if granularity == SpendGranularity.WEEKLY:
        return f"{start:%b} {start.day}, {start.year}"
    if granularity == SpendGranularity.YEARLY:
        return f"{start.year}"
    return f"{start:%b %Y}"


def _next_bucket(start: date, granularity: SpendGranularity) -> date:
    """Advance one bucket: seven days for weekly, one calendar month for monthly, a year for yearly."""
    if granularity == SpendGranularity.WEEKLY:
        return start + timedelta(days=7)
    if granularity == SpendGranularity.YEARLY:
        return date(start.year + 1, 1, 1)
    if start.month == _DECEMBER:
        return date(start.year + 1, 1, 1)
    return date(start.year, start.month + 1, 1)


def _bucket_sequence(lo: date, hi: date, granularity: SpendGranularity) -> list[date]:
    """Return every bucket start from `lo`'s bucket through `hi`'s, inclusive and gap-free.

    Empty in-between buckets are kept (not collapsed) so the chart shows a zero month/week
    rather than silently skipping it, which would distort the trend line and the time axis.
    """
    cur = _bucket_start(lo, granularity)
    end = _bucket_start(hi, granularity)
    out: list[date] = []
    while cur <= end:
        out.append(cur)
        cur = _next_bucket(cur, granularity)
    return out


def _pct_change(old: Decimal, new: Decimal) -> float | None:
    """Return percent change from old to new, or None when there is no baseline.

    A zero baseline returns None because it means "no prior data", not a literal
    zero spend, so a percentage would be meaningless to the caller.
    """
    if old == 0:
        return None
    return float((new - old) / old * 100)


def _apply_date_range(query: Query, *, start: date | None, end: date | None) -> Query:
    """Filter a Receipt-joined query to an optional inclusive purchase-date range."""
    if start is not None:
        query = query.filter(Receipt.purchase_date >= start)
    if end is not None:
        query = query.filter(Receipt.purchase_date <= end)
    return query


def _apply_store_filter(query: Query, store: str | None) -> Query:
    """Filter a Store-joined query to one chain name (case-insensitive), if given."""
    if store is not None:
        query = query.filter(func.lower(Store.chain_name) == store.lower())
    return query


def _apply_category_filter(query: Query, category: str | None) -> Query:
    """Filter a Category-joined query to one category name (case-insensitive), if given."""
    if category is not None:
        query = query.filter(func.lower(Category.name) == category.lower())
    return query


class AnalyticsService:
    """Run headline household queries and free-text search against a session."""

    def __init__(self, session: Session) -> None:
        """Store the session all queries run against."""
        self._session = session

    def _counted_receipts(self, *, start: date | None, end: date | None) -> Query[Receipt]:
        """Return a base query of counted receipts within an optional date range."""
        query = self._session.query(Receipt).filter(Receipt.status.in_(COUNTED_STATUSES))
        return _apply_date_range(query, start=start, end=end)

    def _bucket_monthly(self, receipts: list[Receipt]) -> list[MonthlySpend]:
        """Group receipts into per-calendar-month buckets, oldest first.

        Extracted so callers that already hold a receipt list can derive the monthly
        series without issuing a second database query.
        """
        totals: dict[str, Decimal] = {}
        counts: dict[str, int] = {}
        for r in receipts:
            key = _month_key(r.purchase_date)
            totals[key] = totals.get(key, Decimal(0)) + r.total
            counts[key] = counts.get(key, 0) + 1
        return [
            MonthlySpend(month=key, total=totals[key], receipt_count=counts[key])
            for key in sorted(totals)
        ]

    def parsing_cost(
        self, *, start: date | None = None, end: date | None = None
    ) -> ParsingCostSummary:
        """Total estimated parsing cost and per-parse average over an optional range.

        Helps a self-hoster see their LLM bill and judge their model choice. Sums the durable
        parse cost ledger (not ingestion_jobs, which are deleted on receipt delete/reparse),
        counting only events with a non-null estimated_cost_usd. `start`/`end` are optional;
        each is applied only when given (`end` exclusive), so omitting both yields all time.
        """
        query = self._session.query(
            func.coalesce(func.sum(ParseCostEvent.estimated_cost_usd), 0),
            func.count(ParseCostEvent.id),
        ).filter(ParseCostEvent.estimated_cost_usd.is_not(None))
        if start is not None:
            query = query.filter(ParseCostEvent.created_at >= datetime.combine(start, time.min))
        if end is not None:
            query = query.filter(ParseCostEvent.created_at < datetime.combine(end, time.min))
        total, count = query.one()
        total_cost = Decimal(str(total))
        avg = total_cost / count if count else Decimal(0)
        return ParsingCostSummary(total=total_cost, receipt_count=count, avg_per_receipt=avg)

    def parsing_cost_overview(self, *, today: date | None = None) -> ParsingCostOverview:
        """Assemble the admin page's three LLM-cost figures in one call.

        Uses a rolling 30-day window (not a calendar month) so the figure is always a
        comparable span. The average is all-time per-parse cost.
        """
        today_date = today or naive_utcnow().date()
        all_time = self.parsing_cost()
        last_30 = self.parsing_cost(
            start=today_date - timedelta(days=30), end=today_date + timedelta(days=1)
        )
        return ParsingCostOverview(
            total_all_time=all_time.total,
            total_last_30_days=last_30.total,
            avg_per_receipt=all_time.avg_per_receipt,
        )

    def monthly_spend(self, *, start: date | None, end: date | None) -> list[MonthlySpend]:
        """Return total spend and receipt count per calendar month, oldest first.

        Spend is summed over Decimal in Python to avoid SQLite float drift, matching the
        rest of the analytics service.
        """
        rows = self._counted_receipts(start=start, end=end).all()
        return self._bucket_monthly(rows)

    def _spend_rows(self, *, start: date | None, end: date | None, store_id: int | None) -> list:
        """Return labeled (receipt, date, line_total, category) rows for the spend-over-time view.

        One row per counted line item so every measure (total, trips, by-category) derives from
        the same itemized universe, which keeps the store and category filters well-defined.
        The category filter is applied by the caller in Python so the toolbar's option list can
        still reflect every category in the store/date window regardless of the active filter.
        """
        query = (
            self._session.query(
                Receipt.id.label("receipt_id"),
                Receipt.purchase_date.label("purchase_date"),
                LineItem.line_total.label("line_total"),
                Product.category_id.label("category_id"),
                Category.name.label("category_name"),
            )
            .join(Receipt, LineItem.receipt_id == Receipt.id)
            .join(Product, LineItem.product_id == Product.id)
            .outerjoin(Category, Product.category_id == Category.id)
            .filter(Receipt.status.in_(COUNTED_STATUSES))
        )
        query = _apply_date_range(query, start=start, end=end)
        if store_id is not None:
            query = query.filter(Receipt.store_id == store_id)
        return query.all()

    def spend_over_time(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        store_id: int | None = None,
        category_ids: list[int] | None = None,
        granularity: SpendGranularity = SpendGranularity.MONTHLY,
        series: SpendSeries = SpendSeries.TOTAL,
    ) -> SpendOverTime:
        """Bucket itemized spend over time, ready for a bar chart with an optional category stack.

        Use this to answer "how is my grocery spending trending, and what's driving it?". Spend is
        summed from line items (so it excludes tax/fees and is filterable by store and category);
        each bucket also carries trip count and average basket so the renderer can switch measures
        without a re-query. Empty buckets in the span are kept as zeros so the trend is not
        distorted. The by-category series keeps the top categories and folds the rest into "Other".

        Args:
            start: Optional earliest purchase date (inclusive).
            end: Optional latest purchase date (inclusive).
            store_id: Optional store to restrict to; None spans every store.
            category_ids: Optional categories to restrict the spend to; None spans every category.
            granularity: Bucket width: weekly, monthly, or yearly.
            series: Which measure the caller intends to plot (only affects whether the
                by-category stack is computed).

        Returns:
            SpendOverTime: Gap-free buckets, the stacked-category series, and the toolbar options.
        """
        universe = self._spend_rows(start=start, end=end, store_id=store_id)
        category_options = sorted(
            {
                (r.category_id, r.category_name)
                for r in universe
                if r.category_id is not None and r.category_name != UNCATEGORIZED_NAME
            },
            key=lambda c: c[1].lower(),
        )
        cat_filter = set(category_ids) if category_ids else None
        rows = [r for r in universe if cat_filter is None or r.category_id in cat_filter]

        store_label = self._store_label_for(store_id) if store_id is not None else None
        if not rows:
            return SpendOverTime(
                granularity=granularity,
                series=series,
                store_id=store_id,
                store_label=store_label,
                buckets=[],
                category_series=[],
                category_options=category_options,
                other_category_count=0,
                total_spend=Decimal(0),
                uncategorized_spend=Decimal(0),
            )

        days = [r.purchase_date for r in rows]
        lo = start if start is not None else min(days)
        hi = end if end is not None else max(days)
        sequence = _bucket_sequence(lo, hi, granularity)
        index = {bucket_start: i for i, bucket_start in enumerate(sequence)}

        totals = [Decimal(0)] * len(sequence)
        receipts_seen: list[set[int]] = [set() for _ in sequence]
        for r in rows:
            i = index[_bucket_start(r.purchase_date, granularity)]
            totals[i] += r.line_total
            receipts_seen[i].add(r.receipt_id)

        buckets = [
            SpendBucket(
                start=bucket_start,
                label=_bucket_label(bucket_start, granularity),
                total=totals[i],
                trips=len(receipts_seen[i]),
                avg_basket=(totals[i] / len(receipts_seen[i])) if receipts_seen[i] else Decimal(0),
            )
            for i, bucket_start in enumerate(sequence)
        ]

        category_series, other_count = (
            self._stack_categories(rows, sequence, index, granularity)
            if series == SpendSeries.BY_CATEGORY
            else ([], 0)
        )

        # Spend the by-category stack drops (no category, or the reserved Uncategorized bucket),
        # surfaced so the template can disclose why the stack sums below the headline total.
        uncategorized = sum(
            (
                r.line_total
                for r in rows
                if r.category_id is None or r.category_name == UNCATEGORIZED_NAME
            ),
            Decimal(0),
        )

        return SpendOverTime(
            granularity=granularity,
            series=series,
            store_id=store_id,
            store_label=store_label,
            buckets=buckets,
            category_series=category_series,
            category_options=category_options,
            other_category_count=other_count,
            total_spend=sum(totals, Decimal(0)),
            uncategorized_spend=uncategorized,
        )

    @staticmethod
    def _stack_categories(
        rows: list,
        sequence: list[date],
        index: dict[date, int],
        granularity: SpendGranularity,
    ) -> tuple[list[SpendCategorySeries], int]:
        """Build the per-bucket spend stack: the top categories, then a folded "Other" band.

        Uncategorized and unclassified lines are excluded (they are a review to-do, not a real
        category), matching `category_spend`. Returns the ordered series plus the count of
        categories rolled into "Other" so the template can disclose what was folded.
        """
        per_bucket: dict[str, list[Decimal]] = {}
        for r in rows:
            if r.category_id is None or r.category_name == UNCATEGORIZED_NAME:
                continue
            vec = per_bucket.setdefault(r.category_name, [Decimal(0)] * len(sequence))
            vec[index[_bucket_start(r.purchase_date, granularity)]] += r.line_total
        # Rank by each category's total, derived from its bucket vector (no parallel running sum).
        ranked = sorted(per_bucket, key=lambda n: sum(per_bucket[n]), reverse=True)
        top = ranked[:_SPEND_TOP_CATEGORIES]
        rest = ranked[_SPEND_TOP_CATEGORIES:]

        series = [SpendCategorySeries(category=name, values=per_bucket[name]) for name in top]
        if rest:
            other = [Decimal(0)] * len(sequence)
            for name in rest:
                for i, value in enumerate(per_bucket[name]):
                    other[i] += value
            series.append(SpendCategorySeries(category="Other", values=other))
        return series, len(rest)

    def month_comparison(self, *, today: date | None = None) -> MonthComparison:
        """Contrast the current calendar month with the previous one.

        Independent of the dashboard range preset: this is always a month-over-month pulse.
        """
        today = today or naive_utcnow().date()
        this_start = date(today.year, today.month, 1)
        prev_end = this_start - timedelta(days=1)
        prev_start = date(prev_end.year, prev_end.month, 1)

        def _stats(period_start: date, period_end: date) -> tuple[Decimal, int, int]:
            receipts = (
                self._counted_receipts(start=period_start, end=period_end)
                .options(selectinload(Receipt.line_items))
                .all()
            )
            spend = sum((r.total for r in receipts), Decimal(0))
            items = sum(len(r.line_items) for r in receipts)
            return spend, len(receipts), items

        spend_this, trips_this, items_this = _stats(this_start, today)
        spend_prev, trips_prev, items_prev = _stats(prev_start, prev_end)
        return MonthComparison(
            spend_this=spend_this,
            spend_prev=spend_prev,
            spend_delta_pct=_pct_change(spend_prev, spend_this),
            trips_this=trips_this,
            trips_prev=trips_prev,
            items_this=items_this,
            items_prev=items_prev,
        )

    def kpis(self, preset: RangePreset, *, today: date | None = None) -> list[KpiCard]:
        """Return headline KPI cards with sparkline series and prior-period deltas."""
        start, end = resolve_range(preset, today=today)
        p_start, p_end = prior_range(start, end)

        # Fetch once with line_items eager-loaded to avoid N+1 on item/product counts.
        receipts = (
            self._counted_receipts(start=start, end=end)
            .options(selectinload(Receipt.line_items))
            .all()
        )
        monthly = self._bucket_monthly(receipts)

        receipt_count = len(receipts)
        item_count = sum(len(r.line_items) for r in receipts)
        total_spend = sum((r.total for r in receipts), Decimal(0))
        store_count = len({r.store_id for r in receipts})
        product_count = len({li.product_id for r in receipts for li in r.line_items})

        # Prior-period totals for deltas (skipped for an open ALL_TIME window).
        if p_start is None:
            prior_receipts = []
        else:
            prior_receipts = (
                self._counted_receipts(start=p_start, end=p_end)
                .options(selectinload(Receipt.line_items))
                .all()
            )
        prior_count = len(prior_receipts)
        prior_spend = sum((r.total for r in prior_receipts), Decimal(0))
        prior_items = sum(len(r.line_items) for r in prior_receipts)

        receipt_series = [m.receipt_count for m in monthly]
        spend_series = [m.total for m in monthly]
        avg_series = [m.total / m.receipt_count for m in monthly]
        avg_receipt = total_spend / receipt_count if receipt_count else Decimal(0)
        prior_avg = prior_spend / prior_count if prior_count else Decimal(0)

        return [
            KpiCard(
                label="Receipts",
                value=str(receipt_count),
                points=[Decimal(c) for c in receipt_series],
                delta_pct=_pct_change(Decimal(prior_count), Decimal(receipt_count)),
            ),
            KpiCard(
                label="Total spend",
                value=f"${total_spend:,.2f}",
                points=spend_series,
                delta_pct=_pct_change(prior_spend, total_spend),
            ),
            KpiCard(
                label="Avg receipt",
                value=f"${avg_receipt:,.2f}",
                points=avg_series,
                delta_pct=_pct_change(prior_avg, avg_receipt),
            ),
            KpiCard(
                label="Items",
                value=str(item_count),
                points=[],
                delta_pct=_pct_change(Decimal(prior_items), Decimal(item_count)),
            ),
            KpiCard(label="Stores", value=str(store_count), points=[], delta_pct=None),
            KpiCard(label="Products", value=str(product_count), points=[], delta_pct=None),
        ]

    def activity_heatmap(self, *, start: date | None, end: date | None) -> list[HeatmapCell]:
        """Return one cell per counted shopping day, shaded by that day's total spend.

        Spend (not receipt count) is the shade so sparse grocery trips still show real
        dynamic range across the calendar.
        """
        rows = self._counted_receipts(start=start, end=end).all()
        spend: dict[date, Decimal] = {}
        for r in rows:
            spend[r.purchase_date] = spend.get(r.purchase_date, Decimal(0)) + r.total
        return [HeatmapCell(day=day, spend=spend[day]) for day in sorted(spend)]

    def top_products(
        self,
        *,
        start: date | None,
        end: date | None,
        limit: int = 8,
        by: str = "count",
    ) -> list[TopProduct]:
        """Return the most-bought products, ranked by 'count' or by 'spend'."""
        query = (
            self._session.query(Product.canonical_name, LineItem.line_total)
            .join(Receipt, LineItem.receipt_id == Receipt.id)
            .join(Product, LineItem.product_id == Product.id)
            .filter(Receipt.status.in_(COUNTED_STATUSES))
        )
        query = _apply_date_range(query, start=start, end=end)

        counts: dict[str, int] = {}
        total_spend: dict[str, Decimal] = {}
        for name, line_total in query.all():
            counts[name] = counts.get(name, 0) + 1
            total_spend[name] = total_spend.get(name, Decimal(0)) + line_total

        result = [
            TopProduct(name=name, purchase_count=counts[name], total_spend=total_spend[name])
            for name in counts
        ]
        key = (lambda r: r.total_spend) if by == "spend" else (lambda r: r.purchase_count)
        result.sort(key=key, reverse=True)
        return result[:limit]

    def store_breakdown(self, *, start: date | None, end: date | None) -> list[StoreRow]:
        """Return per-store spend, visit count, and average spend per trip, top spend first."""
        rows = (
            self._counted_receipts(start=start, end=end)
            .join(Receipt.store)
            .options(contains_eager(Receipt.store))
            .all()
        )
        visits: dict[int, int] = {}
        spend: dict[int, Decimal] = {}
        meta: dict[int, Store] = {}
        for r in rows:
            visits[r.store_id] = visits.get(r.store_id, 0) + 1
            spend[r.store_id] = spend.get(r.store_id, Decimal(0)) + r.total
            meta[r.store_id] = r.store

        out = [
            StoreRow(
                store_chain=meta[sid].chain_name,
                store_location=meta[sid].location,
                visits=visits[sid],
                total_spend=spend[sid],
                avg_per_trip=spend[sid] / visits[sid],
            )
            for sid in visits
        ]
        out.sort(key=lambda s: s.total_spend, reverse=True)
        return out

    def stores_by_frequency(self) -> list[StoreOption]:
        """Return stores ranked by counted-receipt count, most-shopped first.

        Feeds the comparison toolbar's two store selectors and supplies the default pair
        (the two busiest stores) when the user has not chosen any.
        """
        rows = (
            self._session.query(Store.id, Store.chain_name, Store.location, func.count(Receipt.id))
            .join(Receipt, Receipt.store_id == Store.id)
            .filter(Receipt.status.in_(COUNTED_STATUSES))
            .group_by(Store.id, Store.chain_name, Store.location)
            .order_by(func.count(Receipt.id).desc(), Store.chain_name.asc())
            .all()
        )
        return [
            StoreOption(
                id=store_id,
                chain_name=chain,
                location=location,
                label=_store_label(chain, location),
                receipt_count=count,
            )
            for (store_id, chain, location, count) in rows
        ]

    def _pair_rows(self, store_ids: list[int], *, start: date | None, end: date | None) -> list:
        """Return labeled (product, store, dimension, normalized price, date) rows for two stores."""
        query = (
            self._session.query(
                Product.canonical_name.label("canonical_name"),
                Receipt.store_id.label("store_id"),
                Product.category_id.label("category_id"),
                Category.name.label("category_name"),
                LineItem.measure_dimension.label("measure_dimension"),
                LineItem.normalized_unit_price.label("normalized_unit_price"),
                LineItem.measure_status.label("measure_status"),
                Receipt.purchase_date.label("purchase_date"),
                Receipt.id.label("receipt_id"),
            )
            .join(Receipt, LineItem.receipt_id == Receipt.id)
            .join(Product, LineItem.product_id == Product.id)
            .outerjoin(Category, Product.category_id == Category.id)
            .filter(Receipt.status.in_(COUNTED_STATUSES))
            .filter(Receipt.store_id.in_(store_ids))
        )
        query = _apply_date_range(query, start=start, end=end)
        return query.all()

    @staticmethod
    def _representative(points: list, *, basis: PriceBasis) -> tuple[str, Decimal] | None:
        """Return (dimension, price) for one store's purchases of a product, or None if unresolved.

        Picks the dominant dimension (the one with the most resolved purchases) so a stray
        differently-measured line never mixes $/g with $/each, then reduces to a single price
        per the basis.
        """
        resolved = [
            p
            for p in points
            if p.measure_status == MeasureStatus.RESOLVED
            and p.normalized_unit_price is not None
            and p.measure_dimension is not None
        ]
        if not resolved:
            return None
        dimension = Counter(p.measure_dimension for p in resolved).most_common(1)[0][0]
        same_dim = [p for p in resolved if p.measure_dimension == dimension]
        if basis == PriceBasis.LATEST:
            latest = max(same_dim, key=lambda p: (p.purchase_date, p.receipt_id))
            return dimension, latest.normalized_unit_price
        return dimension, _median_price([p.normalized_unit_price for p in same_dim])

    @staticmethod
    def _fill_bar_fractions(
        rows: list[StorePairRow], *, scale: ScaleMode
    ) -> tuple[float, dict[str, Decimal]]:
        """Set each row's bar_fraction (0..1) for the active scale; return the axis maxima.

        Percent mode shares one axis across all rows; dollar mode scales each dimension
        group to its own widest gap, because $/g and $/each deltas cannot share a length.
        """
        if scale == ScaleMode.PERCENT:
            magnitudes = [abs(r.pct_diff) for r in rows if r.pct_diff is not None]
            # A row set whose gaps are all zero (a product priced identically at both
            # stores) yields a zero axis max; fall back to 1 so the zero-width bars do
            # not divide by zero.
            axis_max = max(magnitudes) if magnitudes else 1.0
            if axis_max == 0:
                axis_max = 1.0
            for row in rows:
                row.bar_fraction = (
                    1.0 if row.pct_diff is None else min(1.0, abs(row.pct_diff) / axis_max)
                )
            return axis_max, {}
        group_max: dict[str, Decimal] = {}
        for row in rows:
            group_max[row.measure_dimension] = max(
                group_max.get(row.measure_dimension, Decimal(0)), row.abs_diff
            )
        for row in rows:
            gmax = group_max.get(row.measure_dimension, Decimal(0))
            row.bar_fraction = float(row.abs_diff / gmax) if gmax > 0 else 0.0
        return 0.0, group_max

    def store_pair_comparison(  # noqa: PLR0913
        self,
        store_a_id: int,
        store_b_id: int,
        *,
        product_names: list[str] | None = None,
        category_ids: list[int] | None = None,
        start: date | None = None,
        end: date | None = None,
        basis: PriceBasis = PriceBasis.TYPICAL,
        scale: ScaleMode = ScaleMode.PERCENT,
        sort: StorePairSort = StorePairSort.ALPHABETICAL,
    ) -> StorePairComparison:
        """Compare normalized prices of products carried by two stores.

        Use this to answer "for the things I buy at both stores, where are the price gaps?".
        Only products resolved at both stores in a shared measure dimension are comparable;
        everything else is bucketed into only_a/only_b/mismatched so nothing is silently dropped.
        Product and category filters narrow the comparable rows, while the option lists reflect
        what the two stores carry within the selected date range (so a date filter narrows them).
        """
        rows = self._pair_rows([store_a_id, store_b_id], start=start, end=end)

        product_options = sorted({r.canonical_name for r in rows}, key=str.lower)
        category_options = sorted(
            {(r.category_id, r.category_name) for r in rows if r.category_id is not None},
            key=lambda c: c[1].lower(),
        )

        # Drop blank names so a stray empty `?product=` value means "no filter" rather
        # than an impossible match that would hide every row.
        cleaned_names = {n.lower() for n in product_names if n.strip()} if product_names else set()
        name_filter = cleaned_names or None
        cat_filter = set(category_ids) if category_ids else None
        kept = [
            r
            for r in rows
            if (name_filter is None or r.canonical_name.lower() in name_filter)
            and (cat_filter is None or r.category_id in cat_filter)
        ]

        by_product: dict[str, dict[int, list]] = {}
        for r in kept:
            by_product.setdefault(r.canonical_name, {}).setdefault(r.store_id, []).append(r)

        comparable: list[StorePairRow] = []
        only_a: list[StorePairUnmatched] = []
        only_b: list[StorePairUnmatched] = []
        mismatched: list[StorePairUnmatched] = []

        for name, stores in by_product.items():
            a_points = stores.get(store_a_id, [])
            b_points = stores.get(store_b_id, [])
            a_repr = self._representative(a_points, basis=basis)
            b_repr = self._representative(b_points, basis=basis)

            if a_repr is not None and b_repr is not None and a_repr[0] == b_repr[0]:
                dimension, price_a = a_repr
                _, price_b = b_repr
                pricier = "same" if price_a == price_b else ("a" if price_a > price_b else "b")
                comparable.append(
                    StorePairRow(
                        canonical_name=name,
                        measure_dimension=dimension,
                        price_a=price_a,
                        price_b=price_b,
                        abs_diff=abs(price_a - price_b),
                        pct_diff=_pct_more(base=price_a, other=price_b),
                        pricier=pricier,
                        bar_fraction=0.0,  # set by _fill_bar_fractions below
                    )
                )
            elif a_points and b_points:
                # Carried by both but not comparable. If both stores have a resolved price the
                # dimensions must differ (the comparable branch above already ruled out a match);
                # otherwise at least one store is missing a unit size and is fixable by editing.
                reason = (
                    "different_units" if a_repr is not None and b_repr is not None else "needs_unit"
                )
                mismatched.append(_unmatched(name, a_repr or b_repr, reason=reason))
            elif a_points:
                only_a.append(_unmatched(name, a_repr))
            else:
                only_b.append(_unmatched(name, b_repr))

        axis_max_pct, dollar_group_max = self._fill_bar_fractions(comparable, scale=scale)
        _sort_comparable(comparable, sort=sort)
        for bucket in (only_a, only_b, mismatched):
            bucket.sort(key=lambda u: u.canonical_name.lower())

        return StorePairComparison(
            store_a=self._store_label_for(store_a_id),
            store_b=self._store_label_for(store_b_id),
            store_a_id=store_a_id,
            store_b_id=store_b_id,
            scale=scale,
            basis=basis,
            sort=sort,
            rows=comparable,
            only_a=only_a,
            only_b=only_b,
            mismatched=mismatched,
            unmatched_count=len(only_a) + len(only_b) + len(mismatched),
            product_options=product_options,
            category_options=category_options,
            axis_max_pct=float(axis_max_pct),
            dollar_group_max=dollar_group_max,
        )

    def _store_label_for(self, store_id: int) -> str:
        """Return the comma-joined display label for a store id, or a fallback if missing."""
        store = self._session.get(Store, store_id)
        return _store_label(store.chain_name, store.location) if store else "Unknown store"

    def _tracked_products(self, *, start: date | None, end: date | None) -> list[str]:
        """Return canonical names of products bought at least twice in the window.

        Two purchases is the minimum for a meaningful price trend; single-buy products are
        omitted from both price tables rather than drawn as a one-point line.
        """
        return [
            p.name
            for p in self.top_products(start=start, end=end, limit=1000, by="count")
            if p.purchase_count >= _MIN_PRICE_POINTS
        ]

    def price_watch(
        self, *, start: date | None, end: date | None, limit: int = 5
    ) -> list[PriceTrendRow]:
        """Return the top `limit` staples by frequency, each with its unit-price trend."""
        names = self._tracked_products(start=start, end=end)[:limit]
        rows: list[PriceTrendRow] = []
        for name in names:
            points = self._price_points(name, start=start, end=end)
            prices = [p.unit_price for p in points]
            if len(prices) < _MIN_PRICE_POINTS:
                continue
            rows.append(
                PriceTrendRow(
                    product=name,
                    points=prices,
                    current_price=prices[-1],
                    change_pct=_pct_change(prices[0], prices[-1]),
                )
            )
        return rows

    def price_movers(
        self, *, start: date | None, end: date | None, limit: int = 5
    ) -> list[PriceTrendRow]:
        """Return products whose average unit price moved most, first half vs second half.

        Largest absolute percent change first. Requires >=2 purchases so a half-split exists.
        """
        rows: list[PriceTrendRow] = []
        for name in self._tracked_products(start=start, end=end):
            points = self._price_points(name, start=start, end=end)
            prices = [p.unit_price for p in points]
            if len(prices) < _MIN_PRICE_POINTS:
                continue
            # >=2 points (gated above) so mid >= 1 and both halves are non-empty.
            mid = len(prices) // 2
            first = prices[:mid]
            second = prices[mid:]
            first_avg = sum(first, Decimal(0)) / len(first)
            second_avg = sum(second, Decimal(0)) / len(second)
            change = _pct_change(first_avg, second_avg)
            if change is None:
                continue
            rows.append(
                PriceTrendRow(
                    product=name,
                    points=prices,
                    current_price=prices[-1],
                    change_pct=change,
                )
            )
        rows.sort(key=lambda r: abs(r.change_pct or 0), reverse=True)
        return rows[:limit]

    def dashboard(self, preset: RangePreset, *, today: date | None = None) -> DashboardData:
        """Assemble every dashboard section for the chosen preset range in one call.

        Time-based sections honor the resolved range; needs_review is status-based and
        deliberately range-independent. Recent receipts are loaded by the route (they need
        ORM rows for the shared sortable table), not here.
        """
        start, end = resolve_range(preset, today=today)
        needs_review = (
            self._session.query(Receipt)
            .filter(Receipt.status == ReceiptStatus.NEEDS_REVIEW)
            .count()
        )
        monthly = self.monthly_spend(start=start, end=end)
        # Compute category spend once so both the row list and the unclassified total come
        # from the same query result rather than paying for two identical queries.
        cat_spend = self.category_spend(start=start, end=end)
        return DashboardData(
            range_label=range_label(preset),
            kpis=self.kpis(preset, today=today),
            needs_review=needs_review,
            monthly_spend=monthly,
            heatmap=self.activity_heatmap(start=start, end=end),
            categories=cat_spend.rows,
            unclassified_spend=cat_spend.unclassified_spend,
            top_by_count=self.top_products(start=start, end=end, by="count"),
            top_by_spend=self.top_products(start=start, end=end, by="spend"),
            stores=self.store_breakdown(start=start, end=end),
            price_movers=self.price_movers(start=start, end=end),
            price_watch=self.price_watch(start=start, end=end),
            month_comparison=self.month_comparison(today=today),
        )

    def _price_points(
        self,
        product: str,
        *,
        store: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[PricePoint]:
        """Return one PricePoint per counted purchase of `product`, oldest first.

        Shared by `price_history` and related methods so callers do not duplicate the
        receipt/store join or the counted-status filter.
        """
        query = (
            self._session.query(LineItem, Receipt, Store)
            .join(Receipt, LineItem.receipt_id == Receipt.id)
            .join(Store, Receipt.store_id == Store.id)
            .join(Product, LineItem.product_id == Product.id)
            .filter(Receipt.status.in_(COUNTED_STATUSES))
            .filter(func.lower(Product.canonical_name) == product.lower())
        )
        query = _apply_store_filter(query, store)
        query = _apply_date_range(query, start=start, end=end)
        query = query.order_by(Receipt.purchase_date.asc(), Receipt.id.asc())

        return [
            PricePoint(
                purchase_date=receipt.purchase_date,
                store_chain=store_row.chain_name,
                store_location=store_row.location,
                unit_price=line.unit_price,
                quantity=line.quantity,
                line_total=line.line_total,
                receipt_id=receipt.id,
                needs_review=receipt.status == ReceiptStatus.NEEDS_REVIEW,
                normalized_unit_price=line.normalized_unit_price,
                measure_dimension=line.measure_dimension,
                measure_status=line.measure_status,
            )
            for line, receipt, store_row in query.all()
        ]

    def price_history(
        self,
        product: str,
        *,
        store: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> PriceHistory:
        """Return a product's unit price across counted purchases, oldest first.

        Use this to answer "how has the price of eggs changed over time?". `product`
        matches a canonical name exactly (case-insensitive); unknown products return an
        empty-but-valid result rather than raising.
        """
        points = self._price_points(product, store=store, start=start, end=end)
        prices = [p.unit_price for p in points]
        if prices:
            min_price, max_price, avg_price = _price_stats(prices)
        else:
            min_price = max_price = avg_price = None
        return PriceHistory(
            product=product,
            points=points,
            min_unit_price=min_price,
            max_unit_price=max_price,
            avg_unit_price=avg_price,
        )

    def _category_ids_for(self, name: str) -> list[int]:
        """Return ids of categories whose name matches `name` case-insensitively."""
        return [
            cid
            for (cid,) in self._session.query(Category.id).filter(
                func.lower(Category.name) == name.strip().lower()
            )
        ]

    def category_spend(
        self,
        category: str | None = None,
        *,
        store: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> CategorySpend:
        """Total spend per category, optionally filtered to one category/store/date range.

        Use this to answer "how much did I spend on dairy in January?". With no `category`,
        returns one row per category ordered by spend descending. When `category` is provided
        (matched case-insensitively by name), returns the aggregate row for just that category.
        """
        # Resolve the optional category filter to the matching category ids (by name).
        filter_cat_ids: set[int] | None = None
        if category is not None:
            ids = self._category_ids_for(category)
            if not ids:
                # Unrecognized category name - return an empty result immediately.
                return CategorySpend(rows=[], total_spend=Decimal(0), unclassified_spend=Decimal(0))
            filter_cat_ids = set(ids)

        query = (
            self._session.query(Category.name, LineItem.line_total)
            .join(Product, LineItem.product_id == Product.id)
            # Inner join: line items with a NULL category_id are excluded intentionally;
            # uncategorized spend is not attributed to any category.
            .join(Category, Product.category_id == Category.id)
            .join(Receipt, LineItem.receipt_id == Receipt.id)
            .join(Store, Receipt.store_id == Store.id)
            .filter(Receipt.status.in_(COUNTED_STATUSES))
        )
        if filter_cat_ids is not None:
            query = query.filter(Category.id.in_(filter_cat_ids))
        query = _apply_store_filter(query, store)
        query = _apply_date_range(query, start=start, end=end)

        # Aggregate in Python over Decimal to avoid SQLite float drift on NUMERIC sums.
        running: dict[str, Decimal] = {}
        counts: dict[str, int] = {}
        for name, line_total in query.all():
            running[name] = running.get(name, Decimal(0)) + line_total
            counts[name] = counts.get(name, 0) + 1

        # The reserved Uncategorized bucket is a review to-do, not a real spend category, so
        # strip it from the default breakdown and report it separately as unclassified spend.
        # An explicit single-category filter for it must still return its row, so only strip
        # when no specific category was requested.
        unclassified = Decimal(0)
        if category is None:
            unclassified = running.pop(UNCATEGORIZED_NAME, Decimal(0))
            counts.pop(UNCATEGORIZED_NAME, None)

        rows: list[CategorySpendRow] = [
            CategorySpendRow(category=name, total_spend=running[name], line_item_count=counts[name])
            for name in running
        ]
        rows.sort(key=lambda r: r.total_spend, reverse=True)
        total_spend = sum(running.values(), Decimal(0))
        return CategorySpend(rows=rows, total_spend=total_spend, unclassified_spend=unclassified)

    def category_unit_comparison(
        self,
        category: str,
        *,
        store: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> CategoryUnitComparison:
        """Rank products in a category by average normalized price, cheapest first.

        Weight and volume are ranked in separate lists; the two dimensions are not
        commensurable. Count items are excluded because $/each is only same-product
        comparable. Only resolved lines contribute.

        Args:
            category: Category name to filter on (case-insensitive).
            store: Optional store chain name to filter on (case-insensitive).
            start: Optional earliest purchase date (inclusive).
            end: Optional latest purchase date (inclusive).

        Returns:
            CategoryUnitComparison: Weight and volume rows each sorted cheapest-first.
        """
        query = (
            self._session.query(
                Product.canonical_name, LineItem.measure_dimension, LineItem.normalized_unit_price
            )
            .join(Product, LineItem.product_id == Product.id)
            .join(Category, Product.category_id == Category.id)
            .join(Receipt, LineItem.receipt_id == Receipt.id)
            .join(Store, Receipt.store_id == Store.id)
            .filter(Receipt.status.in_(COUNTED_STATUSES))
            .filter(LineItem.measure_status == MeasureStatus.RESOLVED)
            # A RESOLVED row should always carry a normalized price; guard so a NULL from a
            # data-integrity violation cannot reach the Decimal sum below and crash it.
            .filter(LineItem.normalized_unit_price.isnot(None))
            .filter(LineItem.measure_dimension.in_((WEIGHT, VOLUME)))
            .filter(func.lower(Category.name) == category.lower())
        )
        query = _apply_store_filter(query, store)
        query = _apply_date_range(query, start=start, end=end)

        # Average per (product, dimension) in Python over Decimal to avoid SQLite float drift.
        sums: dict[tuple[str, str], Decimal] = {}
        counts: dict[tuple[str, str], int] = {}
        for name, dimension, price in query.all():
            key = (name, dimension)
            sums[key] = sums.get(key, Decimal(0)) + price
            counts[key] = counts.get(key, 0) + 1

        rows = [
            CategoryUnitRow(
                canonical_name=name,
                measure_dimension=dimension,
                avg_normalized_unit_price=(
                    sums[(name, dimension)] / counts[(name, dimension)]
                ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
                line_count=counts[(name, dimension)],
            )
            for (name, dimension) in sums
        ]
        weight_rows = sorted(
            (r for r in rows if r.measure_dimension == WEIGHT),
            key=lambda r: r.avg_normalized_unit_price,
        )
        volume_rows = sorted(
            (r for r in rows if r.measure_dimension == VOLUME),
            key=lambda r: r.avg_normalized_unit_price,
        )
        return CategoryUnitComparison(
            category=category, weight_rows=weight_rows, volume_rows=volume_rows
        )

    def export_line_items(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        store: str | None = None,
        category: str | None = None,
    ) -> list[LineItemExportRow]:
        """Return every line item as a flat export row, filtered by date/store/category.

        Unlike the dashboard queries this applies no receipt-status filter: any receipt that
        has line items is included, so an exported file is a faithful dump of the user's data
        rather than the counted-only subset the analytics figures report. Rows are ordered
        oldest purchase first for stable, diff-friendly output.

        Args:
            start: Optional earliest purchase date (inclusive).
            end: Optional latest purchase date (inclusive).
            store: Optional store chain name to filter on (case-insensitive).
            category: Optional category name to filter on (case-insensitive).

        Returns:
            list[LineItemExportRow]: Flat export rows ordered oldest purchase first.
        """
        query = (
            self._session.query(LineItem)
            .join(LineItem.receipt)
            .join(Receipt.store)
            .join(LineItem.product)
            .outerjoin(Product.category)
            .options(
                contains_eager(LineItem.receipt).contains_eager(Receipt.store),
                contains_eager(LineItem.product).contains_eager(Product.category),
            )
        )
        query = _apply_date_range(query, start=start, end=end)
        query = _apply_store_filter(query, store)
        query = _apply_category_filter(query, category)
        query = query.order_by(Receipt.purchase_date, Receipt.id, LineItem.id)

        return [
            LineItemExportRow(
                purchase_date=li.receipt.purchase_date,
                store_chain=li.receipt.store.chain_name,
                store_location=li.receipt.store.location,
                receipt_id=li.receipt_id,
                receipt_status=li.receipt.status,
                currency=li.receipt.currency,
                raw_description=li.raw_description,
                canonical_name=li.product.canonical_name,
                category=li.product.category.name if li.product.category else None,
                quantity=li.quantity,
                sold_by=li.sold_by,
                measure_unit=li.measure_unit,
                size_amount=li.size_amount,
                size_unit=li.size_unit,
                unit_price=li.unit_price,
                line_total=li.line_total,
                measure_quantity=li.measure_quantity,
                measure_dimension=li.measure_dimension,
                normalized_unit_price=li.normalized_unit_price,
                measure_status=li.measure_status,
            )
            for li in query.all()
        ]

    def search(
        self,
        text: str,
        *,
        sort: SearchSortKey = SearchSortKey.DATE,
        descending: bool = True,
        limit: int = 50,
    ) -> list[SearchResult]:
        """Free-text search over line items, defaulting to date-descending order.

        Matches `text` as a case-insensitive substring of the raw description, canonical
        product name, store chain, or category name. Use this to discover canonical names
        for the other queries. The order is configurable via `sort` and `descending`.
        """
        pattern = f"%{escape_like(text.lower())}%"
        sort_col = SEARCH_SORT_COLUMNS[sort]
        query = (
            self._session.query(LineItem, Receipt, Store, Product, Category)
            .join(Receipt, LineItem.receipt_id == Receipt.id)
            .join(Store, Receipt.store_id == Store.id)
            .join(Product, LineItem.product_id == Product.id)
            .outerjoin(Category, Product.category_id == Category.id)
            .filter(Receipt.status.in_(COUNTED_STATUSES))
            .filter(
                or_(
                    func.lower(LineItem.raw_description).like(pattern, escape="\\"),
                    func.lower(Product.canonical_name).like(pattern, escape="\\"),
                    func.lower(Store.chain_name).like(pattern, escape="\\"),
                    func.lower(Category.name).like(pattern, escape="\\"),
                )
            )
            .order_by(
                sort_col.desc() if descending else sort_col.asc(),
                # id desc is a stable tiebreaker so equal keys keep a deterministic order.
                Receipt.id.desc(),
            )
            .limit(limit)
        )
        return [
            self._to_search_result(line, receipt, store_row, product, category)
            for line, receipt, store_row, product, category in query.all()
        ]

    def line_item_row(self, line_item_id: int) -> SearchResult | None:
        """Project a single counted line item into a SearchResult, or None.

        Backs the search view's inline-edit cancel/save responses, which re-render one row.
        Mirrors search()'s COUNTED_STATUSES filter so a hand-crafted id cannot surface a line
        that search would never show (e.g. one on a failed receipt).

        Args:
            line_item_id: Primary key of the line item to project.

        Returns:
            SearchResult | None: The projected row, or None when no counted line matches.
        """
        row = (
            self._session.query(LineItem, Receipt, Store, Product, Category)
            .join(Receipt, LineItem.receipt_id == Receipt.id)
            .join(Store, Receipt.store_id == Store.id)
            .join(Product, LineItem.product_id == Product.id)
            .outerjoin(Category, Product.category_id == Category.id)
            .filter(LineItem.id == line_item_id)
            .filter(Receipt.status.in_(COUNTED_STATUSES))
            .one_or_none()
        )
        if row is None:
            return None
        return self._to_search_result(*row)

    def product_names(self) -> list[str]:
        """Return every canonical product name in case-insensitive alphabetical order.

        Feeds the search view's product datalist so a line can be reassigned to an existing
        product or a newly typed one.

        Returns:
            list[str]: Canonical product names, sorted case-insensitively.
        """
        return [
            name
            for (name,) in self._session.query(Product.canonical_name).order_by(
                func.lower(Product.canonical_name)
            )
        ]

    @staticmethod
    def _to_search_result(
        line: LineItem,
        receipt: Receipt,
        store_row: Store,
        product: Product,
        category: Category | None,
    ) -> SearchResult:
        """Project one joined search row into a SearchResult.

        Args:
            line: The LineItem being projected.
            receipt: The parent receipt; provides purchase_date and status.
            store_row: The receipt's store; provides chain_name.
            product: The normalized product; provides canonical_name and category_id.
            category: The product's category, or None when uncategorized.

        Returns:
            SearchResult: A populated result object for the matched line item.
        """
        return SearchResult(
            raw_description=line.raw_description,
            canonical_name=product.canonical_name,
            category=category.name if category is not None else None,
            category_id=product.category_id,
            sold_by=line.sold_by,
            measure_unit=line.measure_unit,
            size_amount=line.size_amount,
            size_unit=line.size_unit,
            quantity=line.quantity,
            store_chain=store_row.chain_name,
            purchase_date=receipt.purchase_date,
            unit_price=line.unit_price,
            line_total=line.line_total,
            receipt_id=receipt.id,
            line_item_id=line.id,
            needs_review=receipt.status == ReceiptStatus.NEEDS_REVIEW,
            normalized_unit_price=line.normalized_unit_price,
            measure_dimension=line.measure_dimension,
            measure_status=MeasureStatus(line.measure_status),
        )
