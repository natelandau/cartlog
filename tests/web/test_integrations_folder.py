"""Tests for the watch-folder panel on the integrations page."""

from __future__ import annotations

from cartlog.ingest.folder_watcher import get_folder_config


def test_integrations_renders_folder_panel(app_client):
    """Verify the integrations page shows the watch-folder panel."""
    # When loading the integrations page
    response = app_client.get("/admin/integrations")

    # Then the folder panel is present
    assert response.status_code == 200
    assert "Watch folder" in response.text


def test_post_folder_persists_valid_config(app_client, tmp_path):
    """Verify posting a valid, writable directory enables and stores the config."""
    # Given an existing writable directory
    watch = tmp_path / "inbox"
    watch.mkdir()

    # When saving the folder config
    response = app_client.post(
        "/admin/integrations/folder",
        data={
            "enabled": "on",
            "watch_dir": str(watch),
            "poll_interval": "10",
            "settle_seconds": "5",
        },
    )

    # Then it is stored
    assert response.status_code == 200
    with app_client.app.state.session_factory() as session:
        config = get_folder_config(session)
        assert config.enabled is True
        assert config.watch_dir == str(watch)


def test_post_folder_rejects_missing_dir(app_client, tmp_path):
    """Verify posting a non-existent directory is rejected without persisting."""
    # When saving a path that does not exist
    missing = tmp_path / "nope"
    response = app_client.post(
        "/admin/integrations/folder",
        data={
            "enabled": "on",
            "watch_dir": str(missing),
            "poll_interval": "10",
            "settle_seconds": "5",
        },
    )

    # Then an error is shown and nothing is persisted as enabled
    assert "does not exist" in response.text.lower() or "not a writable" in response.text.lower()
    with app_client.app.state.session_factory() as session:
        config = get_folder_config(session)
        assert config.watch_dir != str(missing)
