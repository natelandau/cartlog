"""Tests for runtime bootstrap."""

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, inspect

from cartlog.bootstrap import prepare_runtime
from cartlog.config import Settings
from cartlog.db.models import Category, LineItem, Product, Receipt, Store
from cartlog.db.session import create_session_factory


def test_prepare_runtime_creates_storage_and_schema(tmp_path, monkeypatch):
    """Verify prepare_runtime creates the storage dir and migrates a fresh database."""
    # Given settings pointing at a not-yet-existing db parent dir and storage dir
    db_path = tmp_path / "data" / "cartlog.db"
    storage_dir = tmp_path / "imgs"
    settings = Settings(
        database_url=f"sqlite:///{db_path}",
        image_storage_dir=storage_dir,
    )
    # alembic/env.py calls get_settings() itself; point it at the same temp settings
    monkeypatch.setattr("cartlog.config.get_settings", lambda: settings)

    # When preparing the runtime
    prepare_runtime(settings)

    # Then the storage dir exists and the database has the migrated schema
    assert storage_dir.is_dir()
    assert db_path.is_file()
    engine = create_engine(f"sqlite:///{db_path}")
    assert "ingestion_jobs" in inspect(engine).get_table_names()
    # And the default category taxonomy has been seeded (Uncategorized plus fixture entries)
    with engine.connect() as conn:
        count = conn.exec_driver_sql("SELECT COUNT(*) FROM categories").scalar()
    assert count is not None
    assert count > 1
    engine.dispose()


def test_prepare_runtime_normalizes_existing_measures(tmp_path, monkeypatch):
    """Verify startup recomputes normalization columns left stale on existing line items."""
    # Given a prepared database with one line item whose normalization was never computed
    db_path = tmp_path / "data" / "cartlog.db"
    settings = Settings(
        database_url=f"sqlite:///{db_path}",
        image_storage_dir=tmp_path / "imgs",
    )
    monkeypatch.setattr("cartlog.config.get_settings", lambda: settings)
    prepare_runtime(settings)

    session_factory = create_session_factory(settings.database_url)
    try:
        with session_factory() as session:
            receipt = Receipt(
                store=Store(chain_name="Safeway", location="Main St"),
                purchase_date=date(2026, 3, 1),
                total=Decimal("4.50"),
                currency="USD",
                image_path="/tmp/x.png",  # noqa: S108
                raw_parser_json="{}",
                source="web",
                status="parsed",
            )
            receipt.line_items.append(
                LineItem(
                    product=Product(canonical_name="milk", category=Category(name="dairy")),
                    raw_description="MILK",
                    quantity=Decimal(1),
                    sold_by="item",
                    size_amount=Decimal("1.5"),
                    size_unit="L",
                    unit_price=Decimal("4.50"),
                    line_total=Decimal("4.50"),
                )
            )
            session.add(receipt)
            session.commit()
            line_id = receipt.line_items[0].id

        # When the runtime is prepared again (e.g. the next startup)
        prepare_runtime(settings)

        # Then the normalization columns are recomputed from the stored unit_size
        with session_factory() as session:
            line = session.get(LineItem, line_id)
            assert line is not None
            assert line.measure_status == "resolved"
            assert line.normalized_unit_price == Decimal("0.003000")
    finally:
        session_factory.kw["bind"].dispose()
