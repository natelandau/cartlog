"""Tests for the structured measure columns added to LineItem."""

from decimal import Decimal

from cartlog.db.models import LineItem
from cartlog.parsing.structuring import structure_line
from cartlog.units import MeasureSource, SoldBy


def test_line_item_has_structured_columns():
    """Verify LineItem table has all required structured measure columns."""
    cols = set(LineItem.__table__.columns.keys())
    assert {"sold_by", "measure_unit", "size_amount", "size_unit"} <= cols


def test_sold_by_defaults_to_item():
    """Verify the sold_by column has a configured default value."""
    # Check the column schema's configured default value
    assert LineItem.__table__.c.sold_by.default.arg == SoldBy.ITEM.value


def test_structuring_maps_representative_legacy_rows():
    """Verify structure_line correctly maps representative legacy unit/unit_size values."""
    # by weight
    assert (
        structure_line(
            quantity=Decimal("1.5"),
            unit="lb",
            unit_size=None,
            raw_description=None,
            canonical_name=None,
        ).sold_by
        == SoldBy.MEASURE
    )
    # packaged printed size
    pkg = structure_line(
        quantity=Decimal(2),
        unit="ea",
        unit_size="16oz",
        raw_description=None,
        canonical_name=None,
    )
    assert (pkg.sold_by, pkg.size_amount, pkg.size_unit) == (SoldBy.ITEM, Decimal(16), "oz")
    # counted multipack
    eggs = structure_line(
        quantity=Decimal(1),
        unit=None,
        unit_size="12ct",
        raw_description=None,
        canonical_name=None,
    )
    assert (eggs.size_amount, eggs.size_unit) == (Decimal(12), "ct")
    # unmeasured single item
    bread = structure_line(
        quantity=Decimal(1),
        unit=None,
        unit_size=None,
        raw_description="bread",
        canonical_name="bread",
    )
    assert (bread.sold_by, bread.size_amount, bread.source) == (
        SoldBy.ITEM,
        None,
        MeasureSource.NONE,
    )


def test_legacy_columns_removed():
    """Verify LineItem no longer contains the legacy unit and unit_size columns."""
    cols = set(LineItem.__table__.columns.keys())
    assert "unit" not in cols and "unit_size" not in cols
