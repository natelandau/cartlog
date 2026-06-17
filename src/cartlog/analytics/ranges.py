# src/cartlog/analytics/ranges.py
"""Preset time ranges that scope the dashboard's time-based widgets.

A preset resolves to an inclusive (start, end) date pair; ALL_TIME resolves to
(None, None) so the analytics queries simply skip their date predicates. `prior_range`
gives the equal-length window immediately before the current one, for period-over-period
deltas on the KPI cards.
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum

from cartlog.clock import naive_utcnow

_LABELS = {
    "12m": "Last 12 months",
    "ytd": "This year",
    "all": "All time",
}


class RangePreset(StrEnum):
    """Dashboard time-range presets, also used as the `?range=` query value."""

    LAST_12_MONTHS = "12m"
    THIS_YEAR = "ytd"
    ALL_TIME = "all"


def resolve_range(
    preset: RangePreset, *, today: date | None = None
) -> tuple[date | None, date | None]:
    """Resolve a preset to an inclusive (start, end) date pair.

    `today` defaults to the database clock's current date; tests pass it explicitly for
    determinism. ALL_TIME returns (None, None) so callers leave the data unbounded.
    """
    today = today or naive_utcnow().date()
    if preset is RangePreset.ALL_TIME:
        return None, None
    if preset is RangePreset.THIS_YEAR:
        return date(today.year, 1, 1), today
    return today - timedelta(days=365), today


def prior_range(start: date | None, end: date | None) -> tuple[date | None, date | None]:
    """Return the equal-length window ending the day before `start`, for deltas.

    "Equal-length" means the same `end - start` timedelta as the current window. An open
    window (start or end is None) has no comparable prior window.
    """
    if start is None or end is None:
        return None, None
    length = end - start
    prior_end = start - timedelta(days=1)
    return prior_end - length, prior_end


def range_label(preset: RangePreset) -> str:
    """Return a human-readable caption for the active range."""
    return _LABELS[preset.value]
