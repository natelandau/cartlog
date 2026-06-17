"""Pure helpers that turn numeric series into SVG geometry for dashboard charts.

Kept free of Jinja and the DB so the coordinate math is unit-testable; the dashboard
macros call these via Jinja globals registered in `templating.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal


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
