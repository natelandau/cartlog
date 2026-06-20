"""Tests for the backup panel and download route on the settings page."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cartlog.backup import BackupResult

if TYPE_CHECKING:
    from pathlib import Path


def test_settings_renders_backup_panel(app_client):
    """Verify the settings page shows the backup panel with a download link."""
    # When loading the settings page
    response = app_client.get("/admin/settings")

    # Then the backup panel and its download form are present
    assert response.status_code == 200
    assert "Download backup" in response.text
    assert 'action="/admin/settings/backup"' in response.text


def test_backup_download_streams_archive(app_client, mocker):
    """Verify the route streams a built archive as a file download and cleans up after."""
    # Given a backup builder that writes a stand-in archive into the staging dir it is handed
    captured: dict[str, Path] = {}

    def fake_backup(_settings, output: Path) -> BackupResult:
        captured["staging"] = output
        archive = output / "cartlog-backup-20260620-120000.tar.gz"
        archive.write_bytes(b"FAKE-ARCHIVE-BYTES")
        return BackupResult(path=archive, database_bytes=18, image_count=0)

    mocker.patch(
        "cartlog.web.routers.settings.create_backup", autospec=True, side_effect=fake_backup
    )

    # When downloading a backup
    response = app_client.post("/admin/settings/backup")

    # Then the archive is streamed as a gzip attachment with the timestamped filename
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/gzip"
    assert "attachment" in response.headers["content-disposition"]
    assert "cartlog-backup-20260620-120000.tar.gz" in response.headers["content-disposition"]
    assert response.content == b"FAKE-ARCHIVE-BYTES"

    # And the staging directory is removed once the response is fully sent
    assert not captured["staging"].exists()


def test_backup_download_error_redirects_with_reason(app_client):
    """Verify a backup failure redirects to settings with the reason shown inline."""
    # Given the in-memory test database, which cannot be snapshot to a file (non-file URL),
    # When downloading a backup (following the redirect back to the settings page)
    response = app_client.post("/admin/settings/backup")

    # Then the settings page reports the failure inline rather than stranding the admin
    assert response.status_code == 200
    assert "Couldn't create the backup" in response.text


def test_backup_download_forbidden_for_viewer(viewer_client):
    """Verify a non-admin cannot trigger a backup download."""
    # When a viewer requests the backup download
    response = viewer_client.post("/admin/settings/backup")

    # Then access is forbidden
    assert response.status_code == 403


def test_backup_download_redirects_anonymous_away(anon_client):
    """Verify an unauthenticated request is redirected, never handed the archive."""
    # When an anonymous visitor requests the backup download without following redirects
    response = anon_client.post("/admin/settings/backup", follow_redirects=False)

    # Then they are redirected away (the setup/login gate) instead of streaming the archive
    assert response.status_code == 303
    assert "application/gzip" not in response.headers.get("content-type", "")
