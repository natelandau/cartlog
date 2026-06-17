"""Shared test data builders and seed datasets.

Centralizes the receipt/line-item construction and the seeded datasets so the analytics,
web, and receipt-CLI test packages all build fixtures from one source rather than importing
across each other's conftest files.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cartlog.db import models  # noqa: F401  # registers tables on Base.metadata
from cartlog.db.base import Base
from cartlog.db.models import (
    Category,
    LineItem,
    Product,
    Receipt,
    ReceiptStatus,
    Store,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def make_line(
    product: Product, *, raw: str, qty: str, unit_price: str, line_total: str
) -> LineItem:
    """Build a LineItem for seeding without needing a parent receipt."""
    return LineItem(
        product=product,
        raw_description=raw,
        quantity=Decimal(qty),
        unit_price=Decimal(unit_price),
        line_total=Decimal(line_total),
    )


def make_receipt(store: Store, day: date, status: ReceiptStatus, lines: list[LineItem]) -> Receipt:
    """Assemble a Receipt with the given lines and a total summed from those lines."""
    receipt = Receipt(
        store=store,
        purchase_date=day,
        total=Decimal(sum(li.line_total for li in lines)),
        currency="USD",
        image_path="/tmp/x.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status=status,
    )
    receipt.line_items.extend(lines)
    return receipt


def seed_receipts(session: Session) -> None:
    """Populate `session` with the shared analytics dataset and commit it.

    Spans multiple stores, dates, categories, and both counted statuses, plus one `failed`
    receipt that every query must exclude.
    """
    dairy = Category(name="dairy")
    produce = Category(name="produce")
    eggs = Product(canonical_name="eggs", category=dairy)
    milk = Product(canonical_name="milk", category=dairy)
    bananas = Product(canonical_name="bananas", category=produce)
    apples = Product(canonical_name="apples", category=produce)

    safeway = Store(chain_name="Safeway", location="Main St")
    costco = Store(chain_name="Costco", location="Airport Rd")

    r1 = make_receipt(
        safeway,
        date(2026, 1, 15),
        ReceiptStatus.PARSED,
        [
            make_line(eggs, raw="LRG EGGS 12CT", qty="1", unit_price="3.00", line_total="3.00"),
            make_line(bananas, raw="BANANAS", qty="2", unit_price="0.50", line_total="1.00"),
        ],
    )
    r2 = make_receipt(
        costco,
        date(2026, 2, 10),
        ReceiptStatus.PARSED,
        [
            make_line(eggs, raw="KS EGGS", qty="1", unit_price="2.50", line_total="2.50"),
            make_line(apples, raw="ORGANIC APPLES", qty="3", unit_price="1.00", line_total="3.00"),
        ],
    )
    r3 = make_receipt(
        safeway,
        date(2026, 3, 5),
        ReceiptStatus.NEEDS_REVIEW,
        [
            make_line(eggs, raw="EGGS LARGE", qty="1", unit_price="3.20", line_total="3.20"),
            make_line(milk, raw="2% MILK", qty="1", unit_price="2.00", line_total="2.00"),
        ],
    )
    # r4 is FAILED and must be excluded from every query.
    r4 = make_receipt(
        safeway,
        date(2026, 3, 20),
        ReceiptStatus.FAILED,
        [
            make_line(eggs, raw="EGGS BAD PARSE", qty="1", unit_price="9.99", line_total="9.99"),
        ],
    )

    session.add_all([dairy, produce, safeway, costco, r1, r2, r3, r4])
    session.commit()


def seed_dashboard_dataset(session: Session) -> None:
    """Populate `session` with a multi-month dataset for the dashboard service tests.

    Spans five counted months across two stores with a repeated, rising-price staple (eggs),
    one needs_review receipt, and one failed receipt that every query must exclude. Kept
    separate from `seed_receipts` so the dashboard tests can assert exact aggregates without
    disturbing the existing dataset's assertions.
    """
    dairy = Category(name="dairy")
    produce = Category(name="produce")
    eggs = Product(canonical_name="eggs", category=dairy)
    milk = Product(canonical_name="milk", category=dairy)
    bananas = Product(canonical_name="bananas", category=produce)

    hub = Store(chain_name="Hubmart", location="Downtown")
    depot = Store(chain_name="Depot", location=None)

    d1 = make_receipt(
        hub,
        date(2026, 1, 10),
        ReceiptStatus.PARSED,
        [
            make_line(eggs, raw="EGGS", qty="1", unit_price="2.00", line_total="2.00"),
            make_line(bananas, raw="BANANAS", qty="2", unit_price="0.50", line_total="1.00"),
        ],
    )
    d2 = make_receipt(
        depot,
        date(2026, 2, 14),
        ReceiptStatus.PARSED,
        [
            make_line(eggs, raw="EGGS", qty="1", unit_price="2.50", line_total="2.50"),
            make_line(milk, raw="MILK", qty="1", unit_price="1.50", line_total="1.50"),
        ],
    )
    d3 = make_receipt(
        hub,
        date(2026, 3, 20),
        ReceiptStatus.PARSED,
        [
            make_line(eggs, raw="EGGS", qty="1", unit_price="3.00", line_total="3.00"),
        ],
    )
    d4 = make_receipt(
        depot,
        date(2026, 4, 5),
        ReceiptStatus.NEEDS_REVIEW,
        [
            make_line(milk, raw="MILK", qty="2", unit_price="1.60", line_total="3.20"),
        ],
    )
    d5 = make_receipt(
        hub,
        date(2026, 5, 9),
        ReceiptStatus.PARSED,
        [
            make_line(eggs, raw="EGGS", qty="1", unit_price="3.50", line_total="3.50"),
            make_line(bananas, raw="BANANAS", qty="1", unit_price="0.60", line_total="0.60"),
        ],
    )
    # d6 is FAILED and must be excluded from every dashboard query.
    d6 = make_receipt(
        hub,
        date(2026, 5, 25),
        ReceiptStatus.FAILED,
        [
            make_line(eggs, raw="EGGS BAD", qty="1", unit_price="9.99", line_total="9.99"),
        ],
    )

    session.add_all([dairy, produce, hub, depot, d1, d2, d3, d4, d5, d6])
    session.commit()


def seed_temp_db(
    tmp_path: Path, filename: str, seed: Callable[[Session], None] = seed_receipts
) -> str:
    """Create a temp-file SQLite DB, seed it, and return its URL.

    Backs the CLI tests that open their own engine against a real file. The engine is disposed
    before returning so the file handle is released for the code under test to reopen.
    """
    url = f"sqlite:///{tmp_path / filename}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
    engine.dispose()
    return url
