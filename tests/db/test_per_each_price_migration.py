"""Tests for the one-time fix that re-derives per-each prices misread as count sizes."""

from decimal import Decimal

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from cartlog.config import Settings

# The revision immediately before the fix; rows are seeded at this point, then upgraded to head.
_PARENT_REVISION = "79cdbf39248c"


def _seed_row(conn, **overrides):
    """Insert one line_items row at the parent revision, returning its id."""
    values = {
        "receipt_id": 1,
        "product_id": 1,
        "raw_description": "item",
        "quantity": Decimal(1),
        "unit_price": Decimal(1),
        "line_total": Decimal(1),
        "sold_by": "item",
        "measure_unit": None,
        "size_amount": None,
        "size_unit": None,
        "measure_quantity": None,
        "measure_dimension": None,
        "normalized_unit_price": None,
        "measure_status": "resolved",
        "measure_source": "extracted",
    }
    values.update(overrides)
    # The raw sqlite3 driver cannot bind Decimal; store as text (read back via Decimal(str(...))).
    bound = {k: str(v) if isinstance(v, Decimal) else v for k, v in values.items()}
    cols = ", ".join(values)
    params = ", ".join(f":{c}" for c in values)
    # Column names come from the fixed dict above, never user input, so the f-string is safe.
    sql = sa.text(f"INSERT INTO line_items ({cols}) VALUES ({params})")  # noqa: S608
    result = conn.execute(sql, bound)
    return result.lastrowid


def test_migration_corrects_each_size_and_leaves_others_untouched(tmp_path, monkeypatch):
    """Verify the fix re-derives each/ea misreads while sparing real, manual, and inferred sizes."""
    # Given a database migrated to the revision just before the fix
    db = tmp_path / "m.db"
    settings = Settings(
        database_url=f"sqlite:///{db}",
        image_storage_dir=tmp_path / "imgs",
        secret_key="x" * 32,
    )
    monkeypatch.setattr("cartlog.config.get_settings", lambda: settings)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, _PARENT_REVISION)

    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO products (id, canonical_name) VALUES (1, 'avocados')"))
        # A per-each priced count sale whose price ($1.39) was misread as a 1.39-each size: the
        # bug under repair. measure_quantity = quantity * size = 2.78 = line_total -> $1.00/ea.
        bug_id = _seed_row(
            conn,
            raw_description="2 Avocados, OG, Per Count $1.39 each",
            quantity=Decimal(2),
            line_total=Decimal("2.78"),
            size_amount=Decimal("1.39"),
            size_unit="each",
            measure_quantity=Decimal("2.78"),
            measure_dimension="count",
            normalized_unit_price=Decimal(1),
            measure_source="extracted",
        )
        # A genuine count pack size (ct, not each/ea) must be left alone.
        ct_id = _seed_row(
            conn,
            raw_description="Eggs 12ct",
            line_total=Decimal("4.00"),
            size_amount=Decimal(12),
            size_unit="ct",
            measure_quantity=Decimal(12),
            measure_dimension="count",
            normalized_unit_price=Decimal("0.333333"),
        )
        # A human-pinned each size and an inferred ea size are both out of scope and untouched.
        manual_id = _seed_row(
            conn, size_amount=Decimal(2), size_unit="each", measure_source="manual"
        )
        inferred_id = _seed_row(
            conn, size_amount=Decimal("1.5"), size_unit="ea", measure_source="inferred"
        )

    # When the fix migration runs
    command.upgrade(cfg, "head")

    # Then the misread row is re-derived to a size-less $/each count sale
    with engine.connect() as conn:
        rows = {
            r["id"]: r
            for r in conn.execute(
                sa.text(
                    "SELECT id, size_amount, size_unit, normalized_unit_price, "
                    "measure_quantity, measure_dimension, measure_source FROM line_items"
                )
            ).mappings()
        }
    engine.dispose()

    fixed = rows[bug_id]
    assert fixed["size_amount"] is None
    assert fixed["size_unit"] is None
    assert Decimal(str(fixed["normalized_unit_price"])) == Decimal("1.39")  # 2.78 / 2
    assert Decimal(str(fixed["measure_quantity"])) == Decimal(2)
    assert fixed["measure_dimension"] == "count"

    # And the genuine ct size, the manual each, and the inferred ea are all unchanged
    assert rows[ct_id]["size_unit"] == "ct"
    assert Decimal(str(rows[ct_id]["size_amount"])) == Decimal(12)
    assert rows[manual_id]["size_unit"] == "each"
    assert Decimal(str(rows[manual_id]["size_amount"])) == Decimal(2)
    assert rows[inferred_id]["size_unit"] == "ea"
    assert Decimal(str(rows[inferred_id]["size_amount"])) == Decimal("1.5")
