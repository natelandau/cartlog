"""Tests for the `cartlog query` CLI sub-app."""

import json

from typer.testing import CliRunner

from cartlog import cli as cli_module
from cartlog.analytics import cli as query_module
from cartlog.config import Settings

runner = CliRunner()


def _settings(seeded_db_url: str) -> Settings:
    return Settings(database_url=seeded_db_url)


def test_query_price_history_table(seeded_db_url, monkeypatch):
    """Verify the price-history command prints each purchase and a summary."""
    # Given settings pointing at the seeded DB
    monkeypatch.setattr(query_module, "get_settings", lambda: _settings(seeded_db_url))

    # When running price-history for eggs
    result = runner.invoke(cli_module.app, ["query", "price-history", "eggs"])

    # Then it exits cleanly and shows prices and the average
    assert result.exit_code == 0, result.output
    assert "2.50" in result.output
    assert "3.20" in result.output
    assert "avg=2.9" in result.output


def test_query_price_history_json(seeded_db_url, monkeypatch):
    """Verify --json emits a parseable PriceHistory payload."""
    # Given settings pointing at the seeded DB
    monkeypatch.setattr(query_module, "get_settings", lambda: _settings(seeded_db_url))

    # When requesting JSON output
    result = runner.invoke(cli_module.app, ["query", "price-history", "eggs", "--json"])

    # Then the payload parses and carries three points
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["product"] == "eggs"
    assert len(payload["points"]) == 3


def test_query_price_history_no_match(seeded_db_url, monkeypatch):
    """Verify an unknown product prints a friendly no-data message."""
    # Given settings pointing at the seeded DB
    monkeypatch.setattr(query_module, "get_settings", lambda: _settings(seeded_db_url))

    # When querying a product with no data
    result = runner.invoke(cli_module.app, ["query", "price-history", "caviar"])

    # Then it reports no data and still exits cleanly
    assert result.exit_code == 0, result.output
    assert "No matching data" in result.output


def test_query_price_history_store_filter_and_needs_review(seeded_db_url, monkeypatch):
    """Verify --store narrows results to that chain and needs-review rows are flagged."""
    # Given settings pointing at the seeded DB
    monkeypatch.setattr(query_module, "get_settings", lambda: _settings(seeded_db_url))

    # When filtering to Safeway only (r1=3.00 PARSED, r3=3.20 NEEDS_REVIEW; r2=2.50 Costco excluded)
    result = runner.invoke(cli_module.app, ["query", "price-history", "eggs", "--store", "Safeway"])

    # Then only Safeway rows appear, Costco price is absent, and the NEEDS_REVIEW row is flagged
    assert result.exit_code == 0, result.output
    assert "3.00" in result.output
    assert "3.20" in result.output
    assert "2.50" not in result.output
    assert "(needs review)" in result.output


def test_query_price_history_date_filter(seeded_db_url, monkeypatch):
    """Verify --from and --to narrow price-history to only purchases in the given window."""
    # Given settings pointing at the seeded DB
    monkeypatch.setattr(query_module, "get_settings", lambda: _settings(seeded_db_url))

    # When filtering to February 2026 only (r2=2.50 Costco; r1=3.00 Jan and r3=3.20 Mar excluded)
    result = runner.invoke(
        cli_module.app,
        ["query", "price-history", "eggs", "--from", "2026-02-01", "--to", "2026-02-28"],
    )

    # Then only the February purchase appears and the January and March prices are absent
    assert result.exit_code == 0, result.output
    assert "2.50" in result.output
    assert "3.00" not in result.output
    assert "3.20" not in result.output


def test_query_store_comparison_table(seeded_db_url, monkeypatch):
    """Verify store-comparison lists each store with its average price."""
    # Given settings pointing at the seeded DB
    monkeypatch.setattr(query_module, "get_settings", lambda: _settings(seeded_db_url))

    # When comparing egg prices across stores
    result = runner.invoke(cli_module.app, ["query", "store-comparison", "eggs"])

    # Then both stores appear, Costco (cheapest) before Safeway
    assert result.exit_code == 0, result.output
    assert result.output.index("Costco") < result.output.index("Safeway")


def test_query_category_spend_table(seeded_db_url, monkeypatch):
    """Verify category-spend prints per-category totals and an overall total."""
    # Given settings pointing at the seeded DB
    monkeypatch.setattr(query_module, "get_settings", lambda: _settings(seeded_db_url))

    # When totaling spend across categories
    result = runner.invoke(cli_module.app, ["query", "category-spend"])

    # Then both categories and the grand total appear
    assert result.exit_code == 0, result.output
    assert "dairy" in result.output
    assert "produce" in result.output
    assert "14.70" in result.output


def test_query_category_spend_filtered(seeded_db_url, monkeypatch):
    """Verify a single-category filter narrows the output."""
    # Given settings pointing at the seeded DB
    monkeypatch.setattr(query_module, "get_settings", lambda: _settings(seeded_db_url))

    # When totaling only produce spend
    result = runner.invoke(cli_module.app, ["query", "category-spend", "produce"])

    # Then produce appears and dairy does not
    assert result.exit_code == 0, result.output
    assert "produce" in result.output
    assert "dairy" not in result.output


def test_query_search_table(seeded_db_url, monkeypatch):
    """Verify search prints matching line items with their store and price."""
    # Given settings pointing at the seeded DB
    monkeypatch.setattr(query_module, "get_settings", lambda: _settings(seeded_db_url))

    # When searching for eggs
    result = runner.invoke(cli_module.app, ["query", "search", "egg"])

    # Then matching raw descriptions appear
    assert result.exit_code == 0, result.output
    assert "EGGS LARGE" in result.output


def test_query_search_no_match(seeded_db_url, monkeypatch):
    """Verify an unmatched search term prints a friendly message."""
    # Given settings pointing at the seeded DB
    monkeypatch.setattr(query_module, "get_settings", lambda: _settings(seeded_db_url))

    # When searching for something absent
    result = runner.invoke(cli_module.app, ["query", "search", "zzzzz"])

    # Then it reports no results
    assert result.exit_code == 0, result.output
    assert "No results" in result.output
