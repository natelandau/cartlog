"""Tests for runtime settings loading and caching."""

import pytest
from pydantic import ValidationError

from cartlog.config import Settings, get_settings


def test_settings_read_from_env(monkeypatch):
    """Verify settings are read from CARTLOG_-prefixed environment variables."""
    monkeypatch.setenv("CARTLOG_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CARTLOG_DATABASE_URL", "sqlite:///custom.db")

    settings = Settings()

    assert settings.anthropic_api_key == "test-key"
    assert settings.database_url == "sqlite:///custom.db"


def test_settings_defaults(monkeypatch):
    """Verify settings fall back to their declared defaults when env vars are unset."""
    monkeypatch.setenv("CARTLOG_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("CARTLOG_DATABASE_URL", raising=False)

    settings = Settings()

    assert settings.database_url == "sqlite:///cartlog.db"
    assert settings.anthropic_model == "claude-opus-4-8"
    assert settings.reclassify_model == "claude-haiku-4-5"
    assert settings.review_confidence_threshold == 0.7


def test_reclassify_model_overrides_default_from_env(monkeypatch):
    """Verify a configured reclassify model overrides the default (env file uses this path)."""
    # Given the reclassify model set in the environment (as the .env.secret file would)
    monkeypatch.setenv("CARTLOG_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CARTLOG_RECLASSIFY_MODEL", "claude-sonnet-4-6")

    # When loading settings
    settings = Settings()

    # Then the configured value wins over the default
    assert settings.reclassify_model == "claude-sonnet-4-6"


def test_settings_worker_defaults(monkeypatch):
    """Verify worker poll interval and retry budget have sensible defaults."""
    monkeypatch.setenv("CARTLOG_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("CARTLOG_WORKER_POLL_INTERVAL", raising=False)
    monkeypatch.delenv("CARTLOG_MAX_RETRIES", raising=False)

    # Given default settings
    settings = Settings()

    # Then the worker tuning values are present with documented defaults
    assert settings.worker_poll_interval == 2.0
    assert settings.max_retries == 3


def test_settings_database_url_bare_path_gets_sqlite_prefix(tmp_path, monkeypatch):
    """Verify a bare filesystem path is normalized into a sqlite:/// URL automatically."""
    # Given a bare path to a database file inside an existing directory
    monkeypatch.setenv("CARTLOG_ANTHROPIC_API_KEY", "test-key")
    db_path = tmp_path / "data.db"
    monkeypatch.setenv("CARTLOG_DATABASE_URL", str(db_path))

    # When loading settings
    settings = Settings()

    # Then the sqlite:/// prefix is added so SQLAlchemy receives a valid URL
    assert settings.database_url == f"sqlite:///{db_path}"


def test_settings_database_url_full_url_passes_through(monkeypatch):
    """Verify a value that already carries a scheme is used unchanged."""
    # Given a full SQLAlchemy URL
    monkeypatch.setenv("CARTLOG_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CARTLOG_DATABASE_URL", "sqlite:///already.db")

    # When loading settings
    settings = Settings()

    # Then it is left exactly as provided, so non-default URLs keep working
    assert settings.database_url == "sqlite:///already.db"


def test_settings_database_url_missing_directory_raises(tmp_path, monkeypatch):
    """Verify a bare path whose parent directory is missing fails fast with a clear error."""
    # Given a path inside a directory that does not exist
    monkeypatch.setenv("CARTLOG_ANTHROPIC_API_KEY", "test-key")
    missing = tmp_path / "nope" / "data.db"
    monkeypatch.setenv("CARTLOG_DATABASE_URL", str(missing))

    # When loading settings, then validation rejects the unavailable directory
    with pytest.raises(ValidationError, match="does not exist"):
        Settings()


def test_get_settings_returns_cached_instance(monkeypatch):
    """Verify get_settings returns the same instance on repeated calls."""
    # Given a valid API key in the environment
    monkeypatch.setenv("CARTLOG_ANTHROPIC_API_KEY", "test-key")

    # When get_settings is called twice after clearing the cache
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()

    # Then both calls return the same cached Settings instance
    assert isinstance(first, Settings)
    assert first is second
