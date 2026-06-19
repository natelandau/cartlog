"""Tests for the watch-folder panel on the settings page."""

from __future__ import annotations

from cartlog.ingest.folder_watcher import get_folder_config


def test_settings_renders_folder_panel(app_client):
    """Verify the settings page shows the watch-folder panel."""
    # When loading the settings page
    response = app_client.get("/admin/settings")

    # Then the folder panel is present
    assert response.status_code == 200
    assert "Watch folder" in response.text
    assert "Settle window" in response.text


def test_admin_index_links_to_settings(app_client):
    """Verify the admin index exposes the settings page as a tile."""
    # When loading the admin page
    response = app_client.get("/admin")

    # Then it links to the settings route
    assert response.status_code == 200
    assert 'href="/admin/settings"' in response.text


def test_integrations_no_longer_shows_folder(app_client):
    """Verify the watch-folder panel was moved off the integrations page."""
    # When loading the integrations page
    response = app_client.get("/admin/integrations")

    # Then it no longer carries the watch-folder panel
    assert response.status_code == 200
    assert "Watch folder" not in response.text


def test_post_folder_persists_valid_config(app_client, tmp_path):
    """Verify posting a valid, writable directory enables and stores the config."""
    # Given an existing writable directory
    watch = tmp_path / "inbox"
    watch.mkdir()

    # When saving the folder config
    response = app_client.post(
        "/admin/settings/folder",
        data={
            "enabled": "on",
            "watch_dir": str(watch),
            "poll_interval": "10",
            "settle_seconds": "5",
        },
    )

    # Then it is stored and the panel confirms the save
    assert response.status_code == 200
    assert "saved" in response.text.lower()
    with app_client.app.state.session_factory() as session:
        config = get_folder_config(session)
        assert config.enabled is True
        assert config.watch_dir == str(watch)


def test_post_folder_rejects_missing_dir(app_client, tmp_path):
    """Verify a non-existent directory is rejected inline without persisting the bad path."""
    # When saving a path that does not exist
    missing = tmp_path / "nope"
    response = app_client.post(
        "/admin/settings/folder",
        data={
            "enabled": "on",
            "watch_dir": str(missing),
            "poll_interval": "10",
            "settle_seconds": "5",
        },
    )

    # Then a field-level error is shown (422) and the bad path is not persisted, but the
    # user's input is preserved in the field so they can correct it
    assert response.status_code == 422
    assert "no such directory" in response.text.lower()
    assert str(missing) in response.text
    with app_client.app.state.session_factory() as session:
        config = get_folder_config(session)
        assert config.watch_dir != str(missing)


def test_post_folder_rejects_non_positive_poll_interval(app_client, tmp_path):
    """Verify a zero poll interval is rejected so the poller cannot busy-loop."""
    # Given an otherwise valid, writable directory
    watch = tmp_path / "inbox"
    watch.mkdir()

    # When saving with a poll interval of zero
    response = app_client.post(
        "/admin/settings/folder",
        data={
            "enabled": "on",
            "watch_dir": str(watch),
            "poll_interval": "0",
            "settle_seconds": "5",
        },
    )

    # Then a field-level error is shown and the bad interval is not persisted
    assert response.status_code == 422
    assert "at least 1 second" in response.text.lower()
    with app_client.app.state.session_factory() as session:
        config = get_folder_config(session)
        assert config.poll_interval != 0
