# tests/web/test_dashboard.py
"""End-to-end tests for the redesigned dashboard route."""

from __future__ import annotations


def test_dashboard_renders_all_bands(app_client):
    """Verify the dashboard page renders the KPI strip and each widget heading."""
    # When loading the dashboard
    response = app_client.get("/")

    # Then the headline sections are present
    assert response.status_code == 200
    body = response.text
    for heading in (
        "Spend over time",
        "Category spend",
        "Top products",
        "Stores",
        "Activity",
        "Recent receipts",
    ):
        assert heading in body
    # And the provenance line documents the active range
    assert "Showing Last 12 months" in body


def test_dashboard_range_chip_returns_body_fragment(app_client):
    """Verify an htmx range request returns just the dashboard body, not the full page."""
    # When htmx requests a different range targeting the body
    response = app_client.get(
        "/?range=all",
        headers={"HX-Request": "true", "HX-Target": "dashboard-body"},
    )

    # Then the swappable body fragment comes back without the page chrome
    assert response.status_code == 200
    assert 'id="dashboard-body"' in response.text
    assert "<html" not in response.text
    assert "Showing All time" in response.text


def test_dashboard_recent_sort_returns_table_fragment(app_client):
    """Verify sorting the recent table returns only the table fragment."""
    # When htmx requests a sort targeting the recent table
    response = app_client.get(
        "/?sort=total&direction=asc",
        headers={"HX-Request": "true", "HX-Target": "recent-receipts-table"},
    )

    # Then only the table fragment returns
    assert response.status_code == 200
    assert 'id="recent-receipts-table"' in response.text
    assert "Spend over time" not in response.text


def test_dashboard_rejects_unknown_range(app_client):
    """Verify an invalid range value is a 422, not a silent default."""
    # When an unknown range is requested
    response = app_client.get("/?range=decade")

    # Then FastAPI rejects it
    assert response.status_code == 422
