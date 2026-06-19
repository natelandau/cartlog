"""Shared Jinja2 templates instance for the web layer.

Kept in its own module so routers and the app factory can both import `templates`
without creating an import cycle through `app.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.templating import Jinja2Templates

from cartlog.analytics.ranges import range_label
from cartlog.web.units_display import format_normalized
from cartlog.web.viz import bar_percents, heatmap_intensity, sparkline_points

_TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Expose the SVG-coordinate helpers to every template so the viz macros can call them.
# ty cannot resolve Jinja2's env.globals union type against arbitrary callables; the
# cast widens the dict to Any so assignments are accepted without ignoring all errors.
_globals: dict[str, Any] = cast("dict[str, Any]", templates.env.globals)
_globals["sparkline_points"] = sparkline_points
_globals["bar_percents"] = bar_percents
_globals["heatmap_intensity"] = heatmap_intensity
# Single source of truth for range captions, shared with the chips and the provenance line.
_globals["range_label"] = range_label

_filters: dict[str, Any] = cast("dict[str, Any]", templates.env.filters)
# Templates call `value | normalized_price(dimension, status, unit_system)`.
_filters["normalized_price"] = format_normalized
