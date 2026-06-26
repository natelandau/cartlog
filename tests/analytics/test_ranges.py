# tests/analytics/test_ranges.py
"""Unit tests for dashboard time-range presets."""

from __future__ import annotations

from datetime import date

import pytest

from cartlog.analytics.ranges import RangePreset, prior_range, range_label, resolve_range

TODAY = date(2026, 6, 14)


@pytest.mark.parametrize(
    ("preset", "expected"),
    [
        # ALL_TIME is open-ended so date filters are skipped
        (RangePreset.ALL_TIME, (None, None)),
        # THIS_YEAR spans Jan 1 to today
        (RangePreset.THIS_YEAR, (date(2026, 1, 1), TODAY)),
        # LAST_12_MONTHS is a trailing 365-day window ending today
        (RangePreset.LAST_12_MONTHS, (date(2025, 6, 14), TODAY)),
    ],
)
def test_resolve_range(preset, expected):
    """Verify each preset resolves to its (start, end) date window."""
    assert resolve_range(preset, today=TODAY) == expected


def test_prior_range_is_the_equal_length_window_before_start():
    """Verify the prior comparison window has equal length and ends the day before start."""
    # Given a 12-month window
    start, end = resolve_range(RangePreset.LAST_12_MONTHS, today=TODAY)

    # When computing the prior window
    p_start, p_end = prior_range(start, end)

    # Then it is the 365 days immediately before the current window
    assert p_end == date(2025, 6, 13)
    assert p_start == date(2024, 6, 13)
    assert start is not None
    assert end is not None
    assert p_start is not None
    assert p_end is not None
    assert (end - start) == (p_end - p_start)


def test_prior_range_of_open_window_is_none():
    """Verify an open (ALL_TIME) window has no prior comparison window."""
    assert prior_range(None, None) == (None, None)


def test_range_label_is_human_readable():
    """Verify each preset exposes a caption for the dashboard provenance line."""
    assert range_label(RangePreset.LAST_12_MONTHS) == "Last 12 months"
    assert range_label(RangePreset.THIS_YEAR) == "This year"
    assert range_label(RangePreset.ALL_TIME) == "All time"
