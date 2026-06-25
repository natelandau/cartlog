"""Tests for runtime settings loading and caching."""

import os

import pytest
from pydantic import ValidationError

from cartlog.config import Settings, get_settings


def test_settings_read_from_env(monkeypatch):
    """Verify settings are read from CARTLOG_-prefixed environment variables."""
    # Given CARTLOG_-prefixed overrides in the environment
    monkeypatch.setenv("CARTLOG_PARSE_MODEL", "openai:gpt-5.2")
    monkeypatch.setenv("CARTLOG_DATABASE_URL", "sqlite:///custom.db")

    # When loading settings
    settings = Settings()

    # Then the values come from the environment
    assert settings.parse_model == "openai:gpt-5.2"
    assert settings.database_url == "sqlite:///custom.db"


def test_settings_defaults(monkeypatch):
    """Verify settings fall back to their declared defaults when env vars are unset."""
    monkeypatch.delenv("CARTLOG_DATABASE_URL", raising=False)

    settings = Settings()

    assert settings.database_url == "sqlite:///cartlog.db"
    assert settings.review_confidence_threshold == 0.7


def test_settings_worker_defaults(monkeypatch):
    """Verify worker poll interval and retry budget have sensible defaults."""
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
    db_path = tmp_path / "data.db"
    monkeypatch.setenv("CARTLOG_DATABASE_URL", str(db_path))

    # When loading settings
    settings = Settings()

    # Then the sqlite:/// prefix is added so SQLAlchemy receives a valid URL
    assert settings.database_url == f"sqlite:///{db_path}"


def test_settings_database_url_full_url_passes_through(monkeypatch):
    """Verify a value that already carries a scheme is used unchanged."""
    # Given a full SQLAlchemy URL
    monkeypatch.setenv("CARTLOG_DATABASE_URL", "sqlite:///already.db")

    # When loading settings
    settings = Settings()

    # Then it is left exactly as provided, so non-default URLs keep working
    assert settings.database_url == "sqlite:///already.db"


def test_settings_database_url_missing_directory_raises(tmp_path, monkeypatch):
    """Verify a bare path whose parent directory is missing fails fast with a clear error."""
    # Given a path inside a directory that does not exist
    missing = tmp_path / "nope" / "data.db"
    monkeypatch.setenv("CARTLOG_DATABASE_URL", str(missing))

    # When loading settings, then validation rejects the unavailable directory
    with pytest.raises(ValidationError, match="does not exist"):
        Settings()


def test_get_settings_loads_env_file_without_overriding_exported(tmp_path, monkeypatch):
    """Verify .env.secret populates the environment for all options while exported vars win."""
    # Given an env file that sets a provider key and a cartlog model, plus an exported override
    env_file = tmp_path / ".env.secret"
    env_file.write_text("ANTHROPIC_API_KEY=from-file\nCARTLOG_PARSE_MODEL=openai:from-file\n")
    monkeypatch.setattr("cartlog.config._ENV_FILE", str(env_file))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CARTLOG_PARSE_MODEL", "anthropic:exported")
    get_settings.cache_clear()

    # When loading settings
    settings = get_settings()

    # Then a file-only value reaches the environment so the provider SDK can read it
    assert os.environ["ANTHROPIC_API_KEY"] == "from-file"
    # And an exported value overrides the file value
    assert settings.parse_model == "anthropic:exported"

    get_settings.cache_clear()


def test_get_settings_returns_cached_instance():
    """Verify get_settings returns the same instance on repeated calls."""
    # When get_settings is called twice after clearing the cache
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()

    # Then both calls return the same cached Settings instance
    assert isinstance(first, Settings)
    assert first is second


def test_settings_model_defaults(monkeypatch):
    """Verify the provider-neutral model settings fall back to their declared defaults."""
    # Given no model overrides in the environment
    monkeypatch.delenv("CARTLOG_PARSE_MODEL", raising=False)
    monkeypatch.delenv("CARTLOG_ASSIST_MODEL", raising=False)

    # When loading settings
    settings = Settings()

    # Then the provider-prefixed defaults are used
    assert settings.parse_model == "anthropic:claude-opus-4-8"
    assert settings.assist_model == "anthropic:claude-haiku-4-5"


def test_assist_model_default_and_env(monkeypatch):
    """Verify the secondary model is configured via CARTLOG_ASSIST_MODEL and defaults to Haiku."""
    assert Settings().assist_model == "anthropic:claude-haiku-4-5"
    monkeypatch.setenv("CARTLOG_ASSIST_MODEL", "openai:gpt-4o-mini")
    assert Settings().assist_model == "openai:gpt-4o-mini"
    assert not hasattr(Settings(), "classify_model")


def test_settings_parse_model_overrides_default_from_env(monkeypatch):
    """Verify a configured parse model overrides the default."""
    # Given a parse model set in the environment
    monkeypatch.setenv("CARTLOG_PARSE_MODEL", "openai:gpt-5.2")

    # When loading settings
    settings = Settings()

    # Then the configured value wins
    assert settings.parse_model == "openai:gpt-5.2"


def test_secret_key_required(monkeypatch):
    """Verify Settings rejects construction when secret_key is empty or unset."""
    # Empty string passed directly must be rejected
    monkeypatch.delenv("CARTLOG_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError, match="CARTLOG_SECRET_KEY is required"):
        Settings(secret_key="")

    # Whitespace-only value is equally invalid
    with pytest.raises(ValidationError, match="CARTLOG_SECRET_KEY is required"):
        Settings(secret_key="   ")


def test_settings_backup_dir_defaults_to_none(monkeypatch):
    """Verify backup_dir is unset by default so backups fall back to the working directory."""
    # Given no backup directory configured
    monkeypatch.delenv("CARTLOG_BACKUP_DIR", raising=False)

    # When loading settings
    settings = Settings()

    # Then no default destination is imposed
    assert settings.backup_dir is None


def test_settings_backup_dir_accepts_existing_directory(tmp_path, monkeypatch):
    """Verify an existing directory is accepted and expanded into a Path."""
    # Given a path to an existing directory
    monkeypatch.setenv("CARTLOG_BACKUP_DIR", str(tmp_path))

    # When loading settings
    settings = Settings()

    # Then it is stored as the resolved backup destination
    assert settings.backup_dir == tmp_path


def test_settings_backup_dir_missing_directory_accepted(tmp_path, monkeypatch):
    """Verify a backup_dir that does not exist yet is accepted and provisioned on demand."""
    # Given a path to a directory that does not exist
    missing = tmp_path / "nope"
    monkeypatch.setenv("CARTLOG_BACKUP_DIR", str(missing))

    # When loading settings
    settings = Settings()

    # Then the resolved path is kept without requiring it to exist at config load
    assert settings.backup_dir == missing


def test_settings_backup_dir_rejects_non_directory(tmp_path, monkeypatch):
    """Verify a backup_dir pointing at a file (not a directory) is rejected."""
    # Given a path to a regular file
    a_file = tmp_path / "not-a-dir"
    a_file.write_text("x")
    monkeypatch.setenv("CARTLOG_BACKUP_DIR", str(a_file))

    # When loading settings, then validation rejects the non-directory path
    with pytest.raises(ValidationError, match="not a directory"):
        Settings()


def test_session_defaults():
    """Verify session and cookie settings carry secure defaults."""
    # Given a minimal Settings with only the required secret_key
    settings = Settings(secret_key="x" * 32)

    # Then the session/cookie defaults match the documented secure values
    assert settings.cookie_secure is True
    assert settings.session_lifetime_days == 14
    assert settings.session_idle_timeout_days == 7
