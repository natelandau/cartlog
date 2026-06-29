"""Registry of Insights analyses: the single source of truth for what the page offers.

Each analysis is an InsightView (URL key, select label, fragment template). The shell
iterates INSIGHT_VIEWS to build its <select>; the router validates the {view} path segment
against it. Adding an analysis is one entry here plus a matching fragment template.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InsightView:
    """One selectable analysis on the Insights page.

    Use this to register an analysis so the shell can list it and the router can resolve
    its fragment by URL key.

    Args:
        key: URL slug and <select> value (e.g. "price-history").
        label: Human-facing <select> option text (e.g. "Price history").
        template: Fragment template path rendered into the panel.
    """

    key: str
    label: str
    template: str


# Ordered; the first entry is the default landing analysis.
INSIGHT_VIEWS: tuple[InsightView, ...] = (
    InsightView("price-history", "Price history", "insights/_price_history.html"),
    InsightView("spend-over-time", "Spend over time", "insights/_spend_over_time.html"),
    InsightView("store-comparison", "Store comparison", "insights/_store_comparison.html"),
    InsightView("category-spend", "Category spend", "insights/_category_spend.html"),
)

DEFAULT_VIEW: InsightView = INSIGHT_VIEWS[0]

_BY_KEY: dict[str, InsightView] = {view.key: view for view in INSIGHT_VIEWS}


def get_view(key: str) -> InsightView | None:
    """Resolve a URL key to its InsightView, or None when no analysis matches."""
    return _BY_KEY.get(key)
