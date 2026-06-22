"""Fixtures for analytics tests.

The seed datasets and builders live in `tests.factories`; this module only exposes them as
pytest fixtures bound to in-memory databases.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cartlog.analytics.service import AnalyticsService
from cartlog.db.models import LineItem, Product, Receipt, ReceiptStatus, Store
from tests.factories import seed_dashboard_dataset, seed_receipts

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture
def analytics_session(session: Session) -> Session:
    """Yield the in-memory session pre-seeded with the analytics dataset."""
    seed_receipts(session)
    return session


@pytest.fixture
def dashboard_session(session: Session) -> Session:
    """Yield the in-memory session pre-seeded with the dashboard dataset."""
    seed_dashboard_dataset(session)
    return session


@pytest.fixture
def analytics_service(session: Session):
    """Yield an AnalyticsService bound to an empty in-memory session."""
    return AnalyticsService(session)


@pytest.fixture
def seeded_line_with_size(session: Session) -> LineItem:
    """Seed a counted line item with known structured measure fields, then return it."""
    product = Product(canonical_name="oat milk")
    store = Store(chain_name="Trader Joes", location=None)
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 4, 1),
        total=Decimal("3.99"),
        currency="USD",
        image_path="/tmp/x.png",  # noqa: S108
        raw_parser_json="{}",
        source="cli",
        status=ReceiptStatus.PARSED,
    )
    line = LineItem(
        product=product,
        raw_description="OAT MILK 32OZ",
        quantity=Decimal(1),
        unit_price=Decimal("3.99"),
        line_total=Decimal("3.99"),
        sold_by="item",
        size_amount=Decimal(32),
        size_unit="fl oz",
    )
    receipt.line_items.append(line)
    session.add_all([product, store, receipt])
    session.commit()
    return line
