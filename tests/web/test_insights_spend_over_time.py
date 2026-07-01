"""Tests for the spend-over-time insight route and its rendered toolbar."""

from __future__ import annotations

from tests.web.helpers import read_json_script


def test_spend_over_time_renders_toolbar_and_payload(app_client):
    """Verify the fragment renders the toolbar and embeds the bucket series as JSON."""
    # When loading the spend-over-time insight as an htmx fragment
    response = app_client.get("/insights/spend-over-time", headers={"HX-Request": "true"})

    # Then it renders the fragment with the measure selector and a chart container
    assert response.status_code == 200
    assert 'data-insight-view="spend-over-time"' in response.text
    assert 'name="series"' in response.text
    assert 'name="granularity"' in response.text
    assert "spend-over-time-chart" in response.text
    # And the embedded payload carries the three seeded monthly buckets
    payload = _read_payload(response.text)
    assert [b["label"] for b in payload["buckets"]] == ["Jan 2026", "Feb 2026", "Mar 2026"]


def test_spend_over_time_lists_all_stores_option(app_client):
    """Verify the store filter offers an All stores default plus each seeded store."""
    # When loading the fragment
    response = app_client.get("/insights/spend-over-time", headers={"HX-Request": "true"})

    # Then both seeded stores are selectable alongside the All stores default
    assert "All stores" in response.text
    assert "Safeway" in response.text
    assert "Costco" in response.text


def test_spend_over_time_empty_date_params_do_not_422(app_client):
    """Verify blank from/to values (always sent by the toolbar form) are treated as absent."""
    # When the form submits empty date inputs alongside other filters
    response = app_client.get(
        "/insights/spend-over-time",
        params={"from": "", "to": "", "store": "", "series": "total"},
        headers={"HX-Request": "true"},
    )

    # Then the request succeeds rather than failing to parse the empty values
    assert response.status_code == 200


def test_spend_over_time_yearly_granularity_buckets_by_year(app_client):
    """Verify the yearly granularity is accepted and buckets the seeded data by year."""
    # When requesting the yearly granularity
    response = app_client.get(
        "/insights/spend-over-time",
        params={"granularity": "yearly"},
        headers={"HX-Request": "true"},
    )

    # Then the payload buckets collapse to a single 2026 year label
    assert response.status_code == 200
    payload = _read_payload(response.text)
    assert [b["label"] for b in payload["buckets"]] == ["2026"]


def test_spend_over_time_rejects_bad_series(app_client):
    """Verify an unknown measure value is a 422, not a silently wrong render."""
    # When requesting an invalid series
    response = app_client.get(
        "/insights/spend-over-time", params={"series": "bogus"}, headers={"HX-Request": "true"}
    )

    # Then validation fails
    assert response.status_code == 422


def test_spend_over_time_by_category_returns_stacked_series(app_client):
    """Verify the by-category measure embeds one stacked series per seeded category."""
    # When requesting the by-category measure
    response = app_client.get(
        "/insights/spend-over-time",
        params={"series": "category"},
        headers={"HX-Request": "true"},
    )

    # Then the payload carries a stacked series for each real category
    assert response.status_code == 200
    payload = _read_payload(response.text)
    categories = {s["category"] for s in payload["categorySeries"]}
    assert categories == {"dairy", "produce"}


def _read_payload(html: str) -> dict:
    """Parse the embedded spend-data chart payload from the rendered fragment."""
    return read_json_script(html, "spend-data")
