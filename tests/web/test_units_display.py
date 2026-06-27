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
        # Lossless: the receipt-editor quantity input re-saves this value, so a 3-decimal
        # weighed quantity must keep all places rather than truncating to 1.48.
        (Decimal("1.475"), "1.475"),
        (None, ""),  # a missing quantity renders empty
    ],
)
def test_format_quantity(quantity, expected):
    """Verify quantities render without trailing zeros and keep full precision for edit fields."""
    assert format_quantity(quantity) == expected


@pytest.mark.parametrize(
    ("sold_by", "quantity", "measure_unit", "size_amount", "size_unit", "system", "expected"),
    [
        # MEASURE mode in the reader's own system keeps its unit, only trimming zeros
        ("measure", Decimal("1.47"), "lb", None, None, "imperial", "1.47 lb"),
        # MEASURE mode converts a wrong-system unit to the reader's (lb -> g for metric)
        ("measure", Decimal("1.47"), "lb", None, None, "metric", "666.78 g"),
        # A large wrong-system weight rebases to the bigger unit (2 kg -> lb, not 70.55 oz)
        ("measure", Decimal(2), "kg", None, None, "imperial", "4.41 lb"),
        # A large wrong-system weight for a metric reader rebases lb -> kg
        ("measure", Decimal(5), "lb", None, None, "metric", "2.27 kg"),
        # A large wrong-system volume rebases floz -> gal once it fills a gallon
        ("measure", Decimal(4), "l", None, None, "imperial", "1.06 gal"),
        # ITEM mode with quantity > 1 returns 'N x size unit', unit already in system
        ("item", Decimal(2), None, Decimal(16), "oz", "imperial", "2 x 16 oz"),
        # ITEM mode with quantity == 1 drops the count prefix
        ("item", Decimal(1), None, Decimal(12), "ct", "imperial", "12 ct"),
        # An inferred base-unit gram size is shown to a US reader in ounces, rounded
        ("item", Decimal(1), None, Decimal("382.7183"), "g", "imperial", "13.5 oz"),
        # The same inferred gram size stays grams for a metric reader, only rounded
        ("item", Decimal(1), None, Decimal("382.7183"), "g", "metric", "382.72 g"),
        # A tiny cross-system size keeps finer precision rather than collapsing to "0 oz"
        ("item", Decimal(1), None, Decimal(100), "mg", "imperial", "0.0035 oz"),
        # ITEM mode without a size returns empty so callers can fall back
        ("item", Decimal(3), None, None, None, "imperial", ""),
    ],
)
def test_format_measure(sold_by, quantity, measure_unit, size_amount, size_unit, system, expected):
    """Verify the measure string converts to the reader's unit system and rounds to two decimals."""
    assert (
        format_measure(
            sold_by=sold_by,
            quantity=quantity,
            measure_unit=measure_unit,
            size_amount=size_amount,
            size_unit=size_unit,
            system=system,
        )
        == expected
    )
