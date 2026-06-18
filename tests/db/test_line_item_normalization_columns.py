"""Tests for normalized measure columns on LineItem."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cartlog.db.base import Base
from cartlog.db.models import Category, LineItem, Product, Receipt, Store


@pytest.fixture
def session():
    """Yield a session bound to a fresh in-memory SQLite database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _make_receipt(store: Store) -> Receipt:
    """Build a minimal Receipt attached to the given store."""
    return Receipt(
        store=store,
        purchase_date=date(2026, 1, 1),
        total=Decimal("0.00"),
        currency="USD",
        image_path="test.png",
        raw_parser_json="{}",
        source="test",
        status="parsed",
    )


def test_line_item_has_normalization_columns(session):
    """Verify that LineItem accepts and persists all four normalization columns."""
    # Given a receipt with a product
    store = Store(chain_name="TestMart", location="HQ")
    product = Product(canonical_name="milk", category=Category(name="dairy"))
    receipt = _make_receipt(store)
    session.add(receipt)
    session.flush()

    # When a line item is created with all normalization columns
    line = LineItem(
        receipt=receipt,
        product=product,
        raw_description="MILK 1.5L",
        quantity=Decimal(1),
        unit_price=Decimal("4.50"),
        line_total=Decimal("4.50"),
        measure_quantity=Decimal("1500.0000"),
        measure_dimension="volume",
        normalized_unit_price=Decimal("0.003000"),
        measure_status="resolved",
    )
    session.add(line)
    session.commit()
    session.refresh(line)

    # Then the normalization values are persisted correctly
    assert line.measure_status == "resolved"
    assert line.normalized_unit_price == Decimal("0.003000")


def test_measure_status_defaults_to_not_applicable(session):
    """Verify that measure_status defaults to 'not_applicable' when not provided."""
    # Given a receipt with a product
    store = Store(chain_name="TestMart", location="HQ")
    product = Product(canonical_name="bread", category=Category(name="bakery"))
    receipt = _make_receipt(store)
    session.add(receipt)
    session.flush()

    # When a line item is created without normalization columns
    line = LineItem(
        receipt=receipt,
        product=product,
        raw_description="BREAD",
        quantity=Decimal(1),
        unit_price=Decimal("0.99"),
        line_total=Decimal("0.99"),
    )
    session.add(line)
    session.commit()
    session.refresh(line)

    # Then measure_status defaults to 'not_applicable' and numeric columns are null
    assert line.measure_status == "not_applicable"
    assert line.normalized_unit_price is None
