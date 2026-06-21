"""Unit tests for the Insights analysis registry."""

from __future__ import annotations

from cartlog.web.insights import DEFAULT_VIEW, INSIGHT_VIEWS, get_view


def test_registry_lists_the_three_ported_analyses():
    """The three existing charts are registered with stable URL keys."""
    keys = [v.key for v in INSIGHT_VIEWS]
    assert keys == ["price-history", "store-comparison", "category-spend"]


def test_registry_keys_are_unique():
    """Duplicate keys would make /insights/{view} ambiguous."""
    keys = [v.key for v in INSIGHT_VIEWS]
    assert len(keys) == len(set(keys))


def test_default_view_is_first_entry():
    """The landing analysis is the first registered view."""
    assert DEFAULT_VIEW is INSIGHT_VIEWS[0]
    assert DEFAULT_VIEW.key == "price-history"


def test_get_view_returns_match_by_key():
    """get_view resolves a known key to its InsightView."""
    view = get_view("store-comparison")
    assert view is not None
    assert view.label == "Store comparison"
    assert view.template == "insights/_store_comparison.html"


def test_get_view_returns_none_for_unknown_key():
    """An unknown key yields None so the route can 404."""
    assert get_view("not-a-view") is None
