"""Tests that the upload route labels jobs with the submitted source."""

from __future__ import annotations

from cartlog.db.models import IngestionJob


def _png_bytes() -> bytes:
    # Minimal non-empty payload; the worker never runs in these tests, so the
    # bytes only need to be stored, not parsed.
    return b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_upload_labels_job_with_submitted_source(app_client):
    """Verify a submitted source form field becomes the job's source."""
    # Given a running app with the upload route
    # When uploading with an explicit source
    response = app_client.post(
        "/receipts",
        files=[("files", ("receipt.png", _png_bytes(), "image/png"))],
        data={"source": "ios"},
    )

    # Then the enqueued job carries that source
    assert response.status_code == 202
    with app_client.app.state.session_factory() as session:
        sources = [job.source for job in session.query(IngestionJob).all()]
    assert sources == ["ios"]


def test_upload_defaults_source_to_web(app_client):
    """Verify omitting the source form field defaults the job source to web."""
    # Given a running app with the upload route
    # When uploading without a source field
    response = app_client.post(
        "/receipts",
        files=[("files", ("receipt.png", _png_bytes(), "image/png"))],
    )

    # Then the job source is the web default
    assert response.status_code == 202
    with app_client.app.state.session_factory() as session:
        sources = [job.source for job in session.query(IngestionJob).all()]
    assert sources == ["web"]


def test_upload_rejects_oversize_source(app_client):
    """Verify a source longer than the column width is rejected rather than stored."""
    # Given a running app with the upload route
    # When uploading with a source label that exceeds the 50-char column width
    response = app_client.post(
        "/receipts",
        files=[("files", ("receipt.png", _png_bytes(), "image/png"))],
        data={"source": "x" * 51},
    )

    # Then the request is rejected and no job is enqueued
    assert response.status_code == 422
    with app_client.app.state.session_factory() as session:
        assert session.query(IngestionJob).count() == 0
