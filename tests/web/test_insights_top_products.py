"""Tests for the top-products (Pareto) insight route and its rendered toolbar."""

from __future__ import annotations

import re

from tests.web.helpers import read_json_script


def _read_payload(html: str) -> dict:
    """Parse the embedded pareto-data chart payload from the rendered fragment."""
    return read_json_script(html, "pareto-data")


def test_renders_toolbar_and_ranked_payload(app_client):
    """Verify the fragment renders the metric toolbar and embeds products ranked by spend."""
    # When loading the top-products insight as an htmx fragment
    response = app_client.get("/insights/top-products", headers={"HX-Request": "true"})

    # Then it renders the fragment with the metric selector and chart container
    assert response.status_code == 200
    assert 'data-insight-view="top-products"' in response.text
    assert 'name="metric"' in response.text
    assert "top-products-chart" in response.text
    # And the embedded payload ranks the seeded products by spend, eggs first, each carrying its
    # share of the total spend (eggs = 8.70 / 14.70)
    payload = _read_payload(response.text)
    assert payload["metric"] == "spend"
    assert [row["name"] for row in payload["rows"]] == ["eggs", "apples", "milk", "bananas"]
    assert payload["rows"][0]["value"] == "8.70"
    assert round(payload["rows"][0]["share"], 1) == 59.2


def test_trips_metric_reranks_by_trip_count(app_client):
    """Verify the metric toggle round-trips and re-ranks by trips."""
    # When selecting the trips metric
    response = app_client.get(
        "/insights/top-products", params={"metric": "trips"}, headers={"HX-Request": "true"}
    )

    # Then the payload reports the trips metric with eggs leading on trip count
    assert response.status_code == 200
    payload = _read_payload(response.text)
    assert payload["metric"] == "trips"
    assert payload["rows"][0]["name"] == "eggs"
    assert payload["rows"][0]["value"] == "3"
    # And the headline "Total trips" shows the real trip count (3 distinct counted
    # receipts), not the sum of each product's per-product trip count (6)
    match = re.search(r"Total trips</dt>\s*<dd[^>]*>\s*(\d+)\s*<", response.text)
    assert match is not None
    assert match.group(1) == "3"
    assert match.group(1) != "6"


def test_lists_all_stores_option(app_client):
    """Verify the store filter offers an All stores default plus each seeded store."""
    # When loading the fragment
    response = app_client.get("/insights/top-products", headers={"HX-Request": "true"})

    # Then both seeded stores are selectable alongside the All stores default
    assert "All stores" in response.text
    assert "Safeway" in response.text
    assert "Costco" in response.text


def test_empty_params_do_not_422(app_client):
    """Verify blank date/store values (always sent by the toolbar form) are treated as absent."""
    # When the form submits empty filters
    response = app_client.get(
        "/insights/top-products",
        params={"from": "", "to": "", "store": "", "metric": "spend"},
        headers={"HX-Request": "true"},
    )

    # Then the request succeeds rather than failing to parse the empty values
    assert response.status_code == 200


def test_rejects_bad_metric(app_client):
    """Verify an unknown metric value is a 422, not a silently wrong render."""
    # When requesting an invalid metric
    response = app_client.get(
        "/insights/top-products", params={"metric": "bogus"}, headers={"HX-Request": "true"}
    )

    # Then FastAPI rejects it
    assert response.status_code == 422
