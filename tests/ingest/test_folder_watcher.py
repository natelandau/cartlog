"""Tests for the watch-folder ingestion poller."""

from __future__ import annotations

from cartlog.ingest.folder_watcher import get_folder_config


def test_get_folder_config_creates_singleton_with_defaults(session):
    """Verify the accessor creates a single default-valued config row."""
    # When fetching the config for the first time
    config = get_folder_config(session)

    # Then a default row exists with id 1 and the expected defaults
    assert config.id == 1
    assert config.enabled is False
    assert config.processed_subdir == "processed"
    assert config.failed_subdir == "failed"
    assert config.poll_interval == 10.0
    assert config.settle_seconds == 5.0


def test_get_folder_config_returns_same_row(session):
    """Verify repeated calls return the one singleton row, not new rows."""
    # Given an existing config with a watch dir set
    first = get_folder_config(session)
    first.watch_dir = "/data/inbox"
    session.commit()

    # When fetching again
    second = get_folder_config(session)

    # Then it is the same row
    assert second.id == 1
    assert second.watch_dir == "/data/inbox"
