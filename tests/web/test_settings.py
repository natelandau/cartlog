"""Tests for the settings page shell."""

from __future__ import annotations

from cartlog.config import Settings, get_settings


def test_settings_page_renders_ios_panel(app_client):
    """Verify the settings page renders the iOS panel with the upload endpoint URL."""
    # Given a running app with the settings route registered
    # When loading the settings page
    response = app_client.get("/admin/settings")

    # Then it renders the iOS instructions and the /receipts endpoint URL
    assert response.status_code == 200
    assert "iOS" in response.text
    assert "http://testserver/receipts" in response.text


def test_admin_index_links_to_settings(app_client):
    """Verify the admin index exposes the settings page as a tile."""
    # Given a running app with the admin index rendered
    # When loading the admin page
    response = app_client.get("/admin")

    # Then it links to the settings route
    assert response.status_code == 200
    assert 'href="/admin/settings"' in response.text


def test_nav_omits_top_level_settings_link(app_client):
    """Verify the global nav no longer exposes a top-level settings link."""
    # Given a running app with the nav template rendered
    # When loading any page
    response = app_client.get("/")

    # Then the nav does not carry a standalone settings link
    assert response.status_code == 200
    assert 'href="/settings"' not in response.text


def test_settings_page_shows_shortcut_button_when_configured(app_client):
    """Verify the settings page links to the iOS Shortcut when a URL is configured."""
    # Given a configured iOS shortcut URL
    shortcut_url = "https://www.icloud.com/shortcuts/abc123"
    app_client.app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite://", ios_shortcut_url=shortcut_url
    )

    # When loading the settings page
    response = app_client.get("/admin/settings")

    # Then the page renders the Add to iOS button pointing at the configured link
    assert response.status_code == 200
    assert "Add to iOS" in response.text
    assert shortcut_url in response.text


def test_settings_page_hides_shortcut_button_when_unset(app_client):
    """Verify the settings page omits the Add to iOS button when no URL is configured."""
    # Given the default settings, which carry no iOS shortcut URL
    # When loading the settings page
    response = app_client.get("/admin/settings")

    # Then no Add to iOS button is rendered
    assert response.status_code == 200
    assert "Add to iOS" not in response.text
