"""Tests for the integrations page."""

from __future__ import annotations

from cartlog.constants import SHORTCUT_URL


def test_integrations_page_renders_shortcut_card(app_client):
    """Verify the integrations page renders the Shortcut card with the upload endpoint URL."""
    # Given a running app with the integrations route registered
    # When loading the integrations page
    response = app_client.get("/admin/integrations")

    # Then it renders the Apple Shortcuts card and the /receipts endpoint URL
    assert response.status_code == 200
    assert "Apple Shortcuts" in response.text
    assert "http://testserver/receipts" in response.text


def test_integrations_page_install_button_points_at_shortcut(app_client):
    """Verify the install button links to the packaged iCloud Shortcut URL."""
    # Given a running app with the integrations route registered
    # When loading the integrations page
    response = app_client.get("/admin/integrations")

    # Then the install button targets the package-level Shortcut URL
    assert response.status_code == 200
    assert f'href="{SHORTCUT_URL}"' in response.text


def test_integrations_page_links_to_api_token_creation(app_client):
    """Verify the page tells users how to get an API token without exposing HTTP header details."""
    # Given a running app with the integrations route registered
    # When loading the integrations page
    response = app_client.get("/admin/integrations")

    # Then users are pointed at token creation, but the low-level header mechanics (which the
    # Shortcut handles for them) are not surfaced.
    assert response.status_code == 200
    assert "/account/tokens" in response.text
    assert "X-Cartlog-Token" not in response.text
    assert "Bearer" not in response.text


def test_admin_index_links_to_integrations(app_client):
    """Verify the admin index exposes the integrations page as a tile."""
    # Given a running app with the admin index rendered
    # When loading the admin page
    response = app_client.get("/admin")

    # Then it links to the integrations route
    assert response.status_code == 200
    assert 'href="/admin/integrations"' in response.text
