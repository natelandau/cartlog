"""Unit tests for SVG-coordinate viz helpers."""

from __future__ import annotations

from decimal import Decimal

from cartlog.web.viz import bar_percents, heatmap_intensity, sparkline_points


def test_sparkline_points_empty_is_blank():
    """Verify an empty series yields an empty points string."""
    # Given no data / When building a sparkline / Then nothing is drawn
    assert sparkline_points([]) == ""


def test_sparkline_points_single_value_is_midline():
    """Verify one value renders a flat line across the vertical middle."""
    # Given a single value
    pts = sparkline_points([Decimal(5)], width=100, height=20, pad=2)

    # Then both endpoints sit at the vertical midpoint
    assert pts == "2,10.00 98,10.00"


def test_sparkline_points_maps_min_to_bottom_and_max_to_top():
    """Verify the lowest value maps to the bottom edge and the highest to the top."""
    # Given an ascending series
    pts = sparkline_points([0, 10], width=100, height=20, pad=2).split(" ")

    # Then x spans the padded box and y inverts (min low, max high)
    assert pts[0] == "2.00,18.00"  # min -> bottom (height-pad)
    assert pts[1] == "98.00,2.00"  # max -> top (pad)


def test_bar_percents_scales_to_largest():
    """Verify bar widths are expressed as a percentage of the largest value."""
    # Given values / When scaling / Then the max is 100%
    assert bar_percents([Decimal(2), Decimal(4), Decimal(1)]) == [50.0, 100.0, 25.0]


def test_bar_percents_all_zero_is_zero():
    """Verify an all-zero series produces zero-width bars, not a divide error."""
    assert bar_percents([Decimal(0), Decimal(0)]) == [0.0, 0.0]


def test_bar_percents_clamps_negative_values_to_zero():
    """Verify a negative value yields a zero-width bar, never a negative width."""
    assert bar_percents([Decimal(-5), Decimal(10)]) == [0.0, 100.0]


def test_bar_percents_empty_is_empty():
    """Verify an empty series yields an empty list."""
    assert bar_percents([]) == []


def test_heatmap_intensity_clamps_to_unit_interval():
    """Verify intensity is value/max, clamped to [0, 1], safe when max is zero."""
    assert heatmap_intensity(Decimal(5), Decimal(10)) == 0.5
    assert heatmap_intensity(Decimal(20), Decimal(10)) == 1.0
    assert heatmap_intensity(Decimal(5), Decimal(0)) == 0.0
