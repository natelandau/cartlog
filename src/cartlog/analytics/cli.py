"""`cartlog query` sub-app: run analytics queries against stored receipts."""

from __future__ import annotations

import json
from collections.abc import Callable  # noqa: TC003  # used in _run's runtime signature
from datetime import date, datetime  # noqa: TC003  # Typer resolves annotations at runtime

import typer

from cartlog.analytics.service import AnalyticsService
from cartlog.config import get_settings
from cartlog.db.session import create_session_factory

query_app = typer.Typer(
    help="Query stored receipts: price history, comparisons, spend, and search.",
    no_args_is_help=True,
)

# Reused option definitions so every command parses dates identically.
_FROM = typer.Option(
    None, "--from", formats=["%Y-%m-%d"], help="Earliest purchase date (inclusive)."
)
_TO = typer.Option(None, "--to", formats=["%Y-%m-%d"], help="Latest purchase date (inclusive).")
_JSON = typer.Option(False, "--json", help="Emit JSON instead of a table.")  # noqa: FBT003


def _run[T](query_fn: Callable[[AnalyticsService], T]) -> T:
    """Open a one-shot session, run `query_fn`, and dispose the engine afterward."""
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    try:
        with session_factory() as session:
            return query_fn(AnalyticsService(session))
    finally:
        session_factory.kw["bind"].dispose()


def _as_date(value: datetime | None) -> date | None:
    """Reduce a Typer-parsed datetime to a plain date."""
    return value.date() if value is not None else None


def _store_label(chain: str, location: str | None) -> str:
    """Render a store as 'chain (location)', or just the chain when location is unknown."""
    return chain if location is None else f"{chain} ({location})"


@query_app.command("price-history")
def price_history_command(
    product: str = typer.Argument(..., help="Canonical product name, e.g. 'eggs'."),
    store: str | None = typer.Option(None, "--store", help="Filter to a store chain."),
    start: datetime | None = _FROM,
    end: datetime | None = _TO,
    as_json: bool = _JSON,  # noqa: FBT001
) -> None:
    """Show a product's price over time."""
    result = _run(
        lambda svc: svc.price_history(
            product, store=store, start=_as_date(start), end=_as_date(end)
        )
    )
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    if not result.points:
        typer.echo(f"No matching data for product '{product}'.")
        return
    for point in result.points:
        flag = "  (needs review)" if point.needs_review else ""
        store = _store_label(point.store_chain, point.store_location)
        typer.echo(f"{point.purchase_date}  {store:<32}{point.unit_price:>8}{flag}")
    typer.echo(
        f"min={result.min_unit_price} max={result.max_unit_price} avg={result.avg_unit_price}"
    )


@query_app.command("store-comparison")
def store_comparison_command(
    product: str = typer.Argument(..., help="Canonical product name, e.g. 'cereal'."),
    start: datetime | None = _FROM,
    end: datetime | None = _TO,
    as_json: bool = _JSON,  # noqa: FBT001
) -> None:
    """Compare a product's price across stores, cheapest average first."""
    result = _run(
        lambda svc: svc.store_comparison(product, start=_as_date(start), end=_as_date(end))
    )
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    if not result.rows:
        typer.echo(f"No matching data for product '{product}'.")
        return
    for row in result.rows:
        store = _store_label(row.store_chain, row.store_location)
        norm = row.avg_normalized_unit_price
        if norm is not None:
            suffix = {"weight": "/g", "volume": "/ml", "count": "/ea"}.get(
                row.measure_dimension or "", ""
            )
            norm_txt = f" norm={norm}{suffix}"
        else:
            norm_txt = " norm=n/a"
        typer.echo(
            f"{store:<32} avg={row.avg_unit_price:>8} "
            f"min={row.min_unit_price} max={row.max_unit_price} n={row.purchase_count}{norm_txt}"
        )


@query_app.command("category-spend")
def category_spend_command(
    category: str | None = typer.Argument(None, help="Category name; omit for a full breakdown."),
    store: str | None = typer.Option(None, "--store", help="Filter to a store chain."),
    start: datetime | None = _FROM,
    end: datetime | None = _TO,
    as_json: bool = _JSON,  # noqa: FBT001
) -> None:
    """Total spend by category, optionally filtered to one category/store/date range."""
    result = _run(
        lambda svc: svc.category_spend(
            category, store=store, start=_as_date(start), end=_as_date(end)
        )
    )
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    if not result.rows:
        typer.echo("No matching data.")
        return
    for row in result.rows:
        typer.echo(f"{row.category:<24}{row.total_spend:>10}  ({row.line_item_count} items)")
    typer.echo(f"total={result.total_spend}")


@query_app.command("search")
def search_command(
    text: str = typer.Argument(..., help="Substring to search for across receipts."),
    limit: int = typer.Option(50, "--limit", help="Maximum rows to return."),
    as_json: bool = _JSON,  # noqa: FBT001
) -> None:
    """Search line items by raw text, product, store, or category."""
    results = _run(lambda svc: svc.search(text, limit=limit))
    if as_json:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
        return
    if not results:
        typer.echo(f"No results for '{text}'.")
        return
    for row in results:
        flag = "  (needs review)" if row.needs_review else ""
        typer.echo(
            f"{row.purchase_date}  {row.store_chain:<20}{row.raw_description:<24}"
            f"{row.unit_price:>8}{flag}"
        )
