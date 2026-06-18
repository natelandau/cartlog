"""Read-only analytics queries over persisted receipt data."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, or_
from sqlalchemy.orm import contains_eager, selectinload

from cartlog.analytics.ranges import RangePreset, prior_range, range_label, resolve_range
from cartlog.analytics.results import (
    CategorySpend,
    CategorySpendRow,
    DashboardData,
    HeatmapCell,
    KpiCard,
    MonthComparison,
    MonthlySpend,
    ParsingCostOverview,
    ParsingCostSummary,
    PriceHistory,
    PricePoint,
    PriceTrendRow,
    SearchResult,
    StoreComparison,
    StoreComparisonRow,
    StoreRow,
    TopProduct,
)
from cartlog.analytics.search_sort import SEARCH_SORT_COLUMNS, SearchSortKey
from cartlog.categories.service import UNCATEGORIZED_NAME
from cartlog.clock import naive_utcnow
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
from cartlog.units import RESOLVED

if TYPE_CHECKING:
    from sqlalchemy.orm import Query, Session

# Receipts whose line items count toward analytics. A needs_review purchase is real
# spend; excluding it would silently understate every figure.
COUNTED_STATUSES = (ReceiptStatus.PARSED, ReceiptStatus.NEEDS_REVIEW)

# Minimum number of price points required to draw a meaningful trend line or half-split.
_MIN_PRICE_POINTS = 2


def _price_stats(prices: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    """Return (min, max, average) for a non-empty list of prices.

    Averaging is done over Decimal to avoid the float drift SQL AVG would introduce.
    """
    return min(prices), max(prices), sum(prices, Decimal(0)) / len(prices)


def _month_key(day: date) -> str:
    """Return the 'YYYY-MM' bucket key for a date."""
    return f"{day.year:04d}-{day.month:02d}"


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

        Shared by `price_history` and `store_comparison` so the latter does not pay for
        the whole-product min/max/avg it would otherwise discard.
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

    def store_comparison(
        self,
        product: str,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> StoreComparison:
        """Compare a product's price across stores, cheapest average first.

        Use this to answer "where is cereal cheapest?". Groups the product's price points
        by store.
        """
        points = self._price_points(product, start=start, end=end)

        by_store: dict[tuple[str, str | None], list[PricePoint]] = {}
        for point in points:
            by_store.setdefault((point.store_chain, point.store_location), []).append(point)

        rows: list[StoreComparisonRow] = []
        for (chain, location), store_points in by_store.items():
            min_price, max_price, avg_price = _price_stats([p.unit_price for p in store_points])
            latest = max(store_points, key=lambda p: (p.purchase_date, p.receipt_id))
            resolved = [p for p in store_points if p.measure_status == RESOLVED]
            dimension = resolved[0].measure_dimension if resolved else None
            # Average only points of the same dimension; mixing $/g and $/each is meaningless.
            same_dim = [p for p in resolved if p.measure_dimension == dimension]
            # Defend the sum below against a data-integrity violation: a RESOLVED row should
            # always carry a normalized price, but guard so a NULL can never crash the average.
            same_dim = [p for p in same_dim if p.normalized_unit_price is not None]
            avg_norm = (
                (
                    (
                        sum((p.normalized_unit_price for p in same_dim), Decimal(0)) / len(same_dim)
                    ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
                )
                if same_dim
                else None
            )
            rows.append(
                StoreComparisonRow(
                    store_chain=chain,
                    store_location=location,
                    avg_unit_price=avg_price,
                    min_unit_price=min_price,
                    max_unit_price=max_price,
                    latest_unit_price=latest.unit_price,
                    purchase_count=len(store_points),
                    avg_normalized_unit_price=avg_norm,
                    measure_dimension=dimension,
                    normalized_count=len(same_dim),
                )
            )
        # Sort by normalized price when available (honest), falling back to raw average.
        rows.sort(
            key=lambda r: (
                r.avg_normalized_unit_price is None,
                r.avg_normalized_unit_price or r.avg_unit_price,
            )
        )
        return StoreComparison(product=product, rows=rows)

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
            store_chain=store_row.chain_name,
            purchase_date=receipt.purchase_date,
            unit_price=line.unit_price,
            line_total=line.line_total,
            receipt_id=receipt.id,
            line_item_id=line.id,
            needs_review=receipt.status == ReceiptStatus.NEEDS_REVIEW,
        )
