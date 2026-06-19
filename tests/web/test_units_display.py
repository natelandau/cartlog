"""Tests for the normalized-price display helper."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from cartlog.web.units_display import format_normalized

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def test_imperial_weight():
    """Verify imperial weight converts $/g to $/oz with 3 decimal places."""
    # $0.003836/g -> /oz = *28.3495
    assert format_normalized(Decimal("0.003836"), "weight", "resolved", "imperial") == "$0.109/oz"


def test_metric_weight_per_100g():
    """Verify metric weight multiplies by 100 and formats to 2 decimal places."""
    assert format_normalized(Decimal("0.003836"), "weight", "resolved", "metric") == "$0.38/100g"


def test_imperial_volume():
    """Verify imperial volume converts $/ml to $/fl oz with 3 decimal places."""
    assert (
        format_normalized(Decimal("0.003000"), "volume", "resolved", "imperial") == "$0.089/fl oz"
    )


def test_count_is_per_each_either_system():
    """Verify count dimension formats as $/ea with 2 decimal places in both unit systems."""
    assert format_normalized(Decimal("0.290000"), "count", "resolved", "imperial") == "$0.29/ea"
    assert format_normalized(Decimal("0.290000"), "count", "resolved", "metric") == "$0.29/ea"


def test_non_resolved_is_na():
    """Verify non-resolved statuses return 'n/a' regardless of other arguments."""
    assert format_normalized(None, None, "not_applicable", "imperial") == "n/a"
    assert format_normalized(None, None, "needs_review", "metric") == "n/a"


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
