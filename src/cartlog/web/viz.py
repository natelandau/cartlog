"""Pure helpers that turn numeric series into SVG geometry for dashboard charts.

Kept free of Jinja and the DB so the coordinate math is unit-testable; the dashboard
macros call these via Jinja globals registered in `templating.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from cartlog.analytics.results import HeatmapCell


def sparkline_points(
    values: list[Decimal | float | int],
    *,
    width: float = 100.0,
    height: float = 24.0,
    pad: float = 2.0,
) -> str:
    """Return an SVG polyline `points` string scaling `values` into a width x height box.

    Oldest sample first. Empty series render nothing; a single value or a flat series sits
    on the vertical midline. The y axis is inverted (SVG origin is top-left) so larger
    values rise toward the top, matching a reader's intuition for a trend line.
    """
    nums = [float(v) for v in values]
    if not nums:
        return ""

    mid = height / 2
    if len(nums) == 1:
        return f"{pad:g},{mid:.2f} {width - pad:g},{mid:.2f}"

    low, high = min(nums), max(nums)
    span = high - low
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    points: list[str] = []
    for i, n in enumerate(nums):
        x = pad + inner_w * i / (len(nums) - 1)
        frac = 0.5 if span == 0 else (n - low) / span
        y = pad + inner_h * (1 - frac)
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points)


def bar_percents(values: list[Decimal | float | int]) -> list[float]:
    """Return each value as a percentage of the largest, for horizontal bar widths.

    An empty or all-zero series yields zeros rather than dividing by zero.
    """
    nums = [float(v) for v in values]
    if not nums:
        return []
    high = max(nums)
    if high <= 0:
        return [0.0 for _ in nums]
    return [max(0.0, 100.0 * n / high) for n in nums]


def heatmap_intensity(value: Decimal | float, max_value: Decimal | float) -> float:
    """Return value/max_value clamped to [0, 1] for a sequential heatmap shade.

    A non-positive max (no activity in the window) yields 0 so the cell renders empty.
    """
    mx = float(max_value)
    if mx <= 0:
        return 0.0
    return min(1.0, float(value) / mx)


# y-axis ticks for the activity calendar. Rows are Sunday-first (row 0 = Sunday), so Monday,
# Wednesday, and Friday land on rows 1, 3, and 5 — the GitHub contribution-graph convention.
_WEEKDAY_TICKS = ((1, "Mon"), (3, "Wed"), (5, "Fri"))


@dataclass(frozen=True)
class HeatmapDay:
    """One day square in the activity calendar; intensity 0 means no shopping that day."""

    day: date
    spend: float
    intensity: float


@dataclass(frozen=True)
class CalendarHeatmap:
    """A GitHub-style activity calendar: week columns plus x/y axis tick labels."""

    weeks: list[list[HeatmapDay | None]]  # columns of 7 rows (Sun..Sat); None is outside the window
    months: list[tuple[int, str]]  # (column index, short month name) where each month first appears
    weekdays: tuple[tuple[int, str], ...]  # (row index, short weekday) ticks for the y axis


def build_calendar_heatmap(
    cells: Sequence[HeatmapCell], *, start: date | None, end: date | None
) -> CalendarHeatmap | None:
    """Arrange per-day spend into Sunday-first week columns with month and weekday ticks.

    Bounded presets pass concrete `start`/`end`; the all-time preset passes None for either
    bound and we fall back to the span of actual activity. Days inside the window with no
    shopping render as empty cells; days outside it are None so the first and last weeks can
    be partial. Returns None when there is no window to draw.

    Args:
        cells: One entry per day that had spend (sparse), each with a `day` and `spend`.
        start: Inclusive window start, or None to start at the earliest active day.
        end: Inclusive window end, or None to end at the latest active day.

    Returns:
        CalendarHeatmap | None: The laid-out calendar, or None when there is nothing to show.
    """
    spend_by_day = {cell.day: float(cell.spend) for cell in cells}
    lo = start if start is not None else (min(spend_by_day) if spend_by_day else None)
    hi = end if end is not None else (max(spend_by_day) if spend_by_day else None)
    if lo is None or hi is None or hi < lo:
        return None

    max_spend = max(spend_by_day.values(), default=0.0)
    # Sunday-first row index: Python's weekday() has Monday==0, so (weekday()+1) % 7 puts
    # Sunday at row 0. Pad back to the column's Sunday and forward to its Saturday.
    grid_start = lo - timedelta(days=(lo.weekday() + 1) % 7)
    grid_end = hi + timedelta(days=6 - (hi.weekday() + 1) % 7)

    weeks: list[list[HeatmapDay | None]] = []
    months: list[tuple[int, str]] = []
    last_month: int | None = None
    day = grid_start
    while day <= grid_end:
        column: list[HeatmapDay | None] = []
        for _ in range(7):
            if lo <= day <= hi:
                spend = spend_by_day.get(day, 0.0)
                column.append(
                    HeatmapDay(day=day, spend=spend, intensity=heatmap_intensity(spend, max_spend))
                )
            else:
                column.append(None)
            day += timedelta(days=1)
        first = next((cell.day for cell in column if cell is not None), None)
        if first is not None and first.month != last_month:
            months.append((len(weeks), first.strftime("%b")))
            last_month = first.month
        weeks.append(column)

    return CalendarHeatmap(weeks=weeks, months=months, weekdays=_WEEKDAY_TICKS)
