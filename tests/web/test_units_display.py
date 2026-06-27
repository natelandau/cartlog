"""Tests for the normalized-price display helper."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cartlog.web.units_display import format_measure, format_normalized, format_quantity

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("value", "dimension", "status", "system", "expected"),
    [
        # $0.003836/g -> /oz = *28.3495, 3 decimals
        (Decimal("0.003836"), "weight", "resolved", "imperial", "$0.109/oz"),
        # metric weight multiplies by 100, 2 decimals
        (Decimal("0.003836"), "weight", "resolved", "metric", "$0.38/100g"),
        # imperial volume converts $/ml to $/fl oz, 3 decimals
        (Decimal("0.003000"), "volume", "resolved", "imperial", "$0.089/fl oz"),
        # count formats as $/ea in both systems
        (Decimal("0.290000"), "count", "resolved", "imperial", "$0.29/ea"),
        (Decimal("0.290000"), "count", "resolved", "metric", "$0.29/ea"),
        # non-resolved statuses return n/a regardless of other arguments
        (None, None, "not_applicable", "imperial", "n/a"),
        (None, None, "needs_review", "metric", "n/a"),
    ],
)
def test_format_normalized(value, dimension, status, system, expected):
    """Verify the normalized $/unit is converted and formatted per dimension and unit system."""
    assert format_normalized(value, dimension, status, system) == expected


def test_toggle_unit_system_flips_to_metric(app_client: TestClient):
    """Verify POST /preferences/unit-system flips the cookie from imperial to metric."""
    # Given no existing cookie (defaults to imperial)
    # When posting the toggle
    response = app_client.post(
        "/preferences/unit-system",
        cookies={},
        follow_redirects=False,
    )

    # Then the response is 204, sets the metric cookie, and asks htmx to refresh
    assert response.status_code == 204
    assert response.cookies.get("unit_system") == "metric"
    assert response.headers.get("hx-refresh") == "true"


def test_toggle_unit_system_flips_back_to_imperial(app_client: TestClient):
    """Verify POST /preferences/unit-system flips the cookie from metric back to imperial."""
    # Given an existing metric cookie
    # When posting the toggle
    response = app_client.post(
        "/preferences/unit-system",
        cookies={"unit_system": "metric"},
        follow_redirects=False,
    )

    # Then the cookie is set back to imperial
    assert response.status_code == 204
    assert response.cookies.get("unit_system") == "imperial"


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        (Decimal("2.000"), "2"),  # a whole count shows as an integer, not 2.000
        (Decimal("1.50"), "1.5"),  # a real fraction keeps its significant digits
        (Decimal("1.470"), "1.47"),  # a weighed quantity trims only trailing zeros
        (Decimal(200), "200"),  # round powers of ten stay fixed-point, not 2E+2
        (None, ""),  # a missing quantity renders empty
    ],
)
def test_format_quantity(quantity, expected):
    """Verify quantities render without trailing zeros so whole counts read as integers."""
    assert format_quantity(quantity) == expected


@pytest.mark.parametrize(
    ("sold_by", "quantity", "measure_unit", "size_amount", "size_unit", "expected"),
    [
        # MEASURE mode returns quantity + measure_unit as a trimmed string
        ("measure", Decimal("1.47"), "lb", None, None, "1.47 lb"),
        # ITEM mode with quantity > 1 returns 'N x size unit'
        ("item", Decimal(2), None, Decimal(16), "oz", "2 x 16 oz"),
        # ITEM mode with quantity == 1 drops the count prefix
        ("item", Decimal(1), None, Decimal(12), "ct", "12 ct"),
        # ITEM mode without a size returns empty so callers can fall back
        ("item", Decimal(3), None, None, None, ""),
    ],
)
def test_format_measure(sold_by, quantity, measure_unit, size_amount, size_unit, expected):
    """Verify the human-readable measure string per sold-by mode, quantity, and size."""
    assert (
        format_measure(
            sold_by=sold_by,
            quantity=quantity,
            measure_unit=measure_unit,
            size_amount=size_amount,
            size_unit=size_unit,
        )
        == expected
    )
