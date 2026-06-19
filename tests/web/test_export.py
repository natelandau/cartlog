"""Tests for the /export download endpoint."""

from __future__ import annotations

import json


def test_export_csv_download(app_client):
    """Verify GET /export returns a CSV attachment covering every line item."""
    # Given the seeded app

    # When requesting a CSV export
    response = app_client.get("/export?format=csv")

    # Then it is a CSV file attachment with a header plus 7 rows
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in response.headers["content-disposition"]
    assert "cartlog-export-" in response.headers["content-disposition"]
    lines = response.text.splitlines()
    assert lines[0].startswith("purchase_date,")
    assert len(lines) == 8  # header + 7 line items


def test_export_json_download_with_store_filter(app_client):
    """Verify the JSON export honors the store filter."""
    # Given the seeded app

    # When requesting Safeway rows as JSON
    response = app_client.get("/export?format=json&store=safeway")

    # Then only Safeway's 5 rows are returned
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = json.loads(response.text)
    assert len(payload) == 5
    assert {row["store_chain"] for row in payload} == {"Safeway"}


def test_export_rejects_unknown_format(app_client):
    """Verify an unknown format is a 422 validation error."""
    # Given the seeded app

    # When requesting an unsupported format
    response = app_client.get("/export?format=xml")

    # Then the request is rejected
    assert response.status_code == 422
