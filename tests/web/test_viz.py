"""Unit tests for SVG-coordinate viz helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cartlog.analytics.results import HeatmapCell
from cartlog.web.viz import (
    bar_percents,
    build_calendar_heatmap,
    heatmap_intensity,
    sparkline_points,
)


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


def test_build_calendar_heatmap_lays_out_weeks_and_ticks():
    """Verify a bounded window becomes Sunday-first week columns with month and weekday ticks."""
    # Given two Monday shopping days inside a two-week window (2025-06-01 is a Sunday)
    cells = [
        HeatmapCell(day=date(2025, 6, 2), spend=Decimal(10)),
        HeatmapCell(day=date(2025, 6, 9), spend=Decimal(20)),
    ]

    # When building the calendar over the aligned window
    cal = build_calendar_heatmap(cells, start=date(2025, 6, 1), end=date(2025, 6, 14))

    # Then it is two full columns with the expected axis ticks
    assert cal is not None
    assert len(cal.weeks) == 2
    assert all(day is not None for week in cal.weeks for day in week)
    assert cal.months == [(0, "Jun")]
    assert cal.weekdays == ((1, "Mon"), (3, "Wed"), (5, "Fri"))
    # And spend shades each Monday relative to the window's max, leaving quiet days empty
    week0_mon, week1_mon, week0_sun = cal.weeks[0][1], cal.weeks[1][1], cal.weeks[0][0]
    assert week0_mon is not None and week1_mon is not None and week0_sun is not None
    assert (week0_mon.spend, week0_mon.intensity) == (10.0, 0.5)
    assert week1_mon.intensity == 1.0
    assert (week0_sun.spend, week0_sun.intensity) == (0.0, 0.0)


def test_build_calendar_heatmap_pads_partial_first_week():
    """Verify days before the window start render as None so the first column can be partial."""
    # Given a window starting on a Tuesday (2025-06-03)
    cells = [HeatmapCell(day=date(2025, 6, 3), spend=Decimal(5))]

    # When building the calendar
    cal = build_calendar_heatmap(cells, start=date(2025, 6, 3), end=date(2025, 6, 7))

    # Then the Sunday and Monday rows of the first column are padding
    assert cal is not None
    assert cal.weeks[0][0] is None  # Sunday, before the window
    assert cal.weeks[0][1] is None  # Monday, before the window
    assert cal.weeks[0][2] is not None  # Tuesday, the window start


def test_build_calendar_heatmap_empty_window_is_none():
    """Verify an all-time window with no activity returns None rather than an empty grid."""
    # Given no cells and an unbounded window
    # When building the calendar
    # Then there is nothing to draw
    assert build_calendar_heatmap([], start=None, end=None) is None


def test_heatmap_intensity_clamps_to_unit_interval():
    """Verify intensity is value/max, clamped to [0, 1], safe when max is zero."""
    assert heatmap_intensity(Decimal(5), Decimal(10)) == 0.5
    assert heatmap_intensity(Decimal(20), Decimal(10)) == 1.0
    assert heatmap_intensity(Decimal(5), Decimal(0)) == 0.0
