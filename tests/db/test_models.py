"""Tests for the ORM models and their relationships."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cartlog.db.base import Base
from cartlog.db.models import (
    Category,
    IngestionJob,
    JobStatus,
    JobStep,
    LineItem,
    Product,
    Receipt,
    Store,
)


@pytest.fixture
def session():
    """Yield a session bound to a fresh in-memory SQLite database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    # Close the in-memory connection so it does not trigger a ResourceWarning at GC time.
    engine.dispose()


def test_receipt_with_line_items_persists(session):
    """Verify a receipt and its line items persist and reload with relationships intact."""
    store = Store(chain_name="Safeway", location="Main St")
    category = Category(name="dairy/eggs")
    product = Product(canonical_name="eggs", category=category)
    receipt = Receipt(
        store=store,
        purchase_date=date(2026, 3, 1),
        total=Decimal("3.48"),
        currency="USD",
        image_path="receipt_images/a.png",
        raw_parser_json="{}",
        source="cli",
        status="parsed",
    )
    receipt.line_items.append(
        LineItem(
            product=product,
            raw_description="GV LRG EGGS 12CT",
            quantity=Decimal(1),
            unit_price=Decimal("3.48"),
            line_total=Decimal("3.48"),
        )
    )
    session.add(receipt)
    session.commit()

    loaded = session.query(Receipt).one()
    assert loaded.store.chain_name == "Safeway"
    assert loaded.line_items[0].product.canonical_name == "eggs"
    assert loaded.line_items[0].product.category.name == "dairy/eggs"
    assert loaded.total == Decimal("3.48")


def test_ingestion_job_defaults(session):
    """Verify a new ingestion job defaults to pending with zero retries."""
    # Given a freshly created job referencing a stored file
    job = IngestionJob(source="cli", image_path="/storage/scan-abc.png")
    session.add(job)
    session.commit()

    # Then it is pending, has no retries, no error, and no receipt yet
    assert job.id is not None
    assert job.status == JobStatus.PENDING
    assert job.retry_count == 0
    assert job.last_error is None
    assert job.receipt_id is None
    assert job.created_at is not None
    assert job.updated_at is not None


def test_product_canonical_name_is_unique(session):
    """Verify a duplicate canonical product name violates the unique constraint."""
    session.add(Product(canonical_name="eggs"))
    session.commit()
    session.add(Product(canonical_name="eggs"))
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        session.commit()


def test_ingestion_job_step_defaults_to_none(session):
    """Verify a new ingestion job has no sub-step until parsing sets one."""
    # Given a freshly created pending job
    job = IngestionJob(source="web", image_path="/storage/x.png", status=JobStatus.PENDING)
    session.add(job)
    session.commit()

    # Then the step is null and the enum exposes its two parsing sub-steps
    assert job.step is None
    assert JobStep.EXTRACTING == "extracting"
    assert JobStep.SAVING == "saving"
