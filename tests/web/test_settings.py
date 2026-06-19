"""Tests for the settings page shell."""

from __future__ import annotations


def test_settings_page_renders_ios_panel(app_client):
    """Verify the settings page renders the iOS panel with the upload endpoint URL."""
    # When loading the settings page
    response = app_client.get("/settings")

    # Then it renders the iOS instructions and the /receipts endpoint URL
    assert response.status_code == 200
    assert "iOS" in response.text
    assert "/receipts" in response.text


def test_nav_links_to_settings(app_client):
    """Verify the global nav includes a link to the settings page."""
    # When loading any page
    response = app_client.get("/")

    # Then the nav exposes the settings route
    assert response.status_code == 200
    assert 'href="/settings"' in response.text
