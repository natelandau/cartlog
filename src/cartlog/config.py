"""Runtime configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (prefix CARTLOG_)."""

    model_config = SettingsConfigDict(env_prefix="CARTLOG_", env_file=".env.secret", extra="ignore")

    # Provider-prefixed model id for the receipt vision parser (e.g. "openai:gpt-5.2").
    parse_model: str = "anthropic:claude-opus-4-8"
    # Provider-prefixed model id for the focused category reclassification pass.
    classify_model: str = "anthropic:claude-haiku-4-5"
    # Max times the LLM reclassifier is spent on a still-Uncategorized product before it is
    # left as-is for manual review (prevents unbounded retries on products it cannot place).
    max_reclassify_attempts: int = 2

    # Accepts a bare SQLite file path (e.g. /app/data.db); the sqlite:/// prefix is added
    # automatically by the validator so users never have to remember it.
    database_url: str = "cartlog.db"
    # Where ingested receipt images are copied and retained.
    image_storage_dir: Path = Path("receipt_images")
    # Parses with confidence below this are flagged needs_review instead of parsed.
    review_confidence_threshold: float = 0.7
    # Line-item totals may differ from the grand total by at most this before flagging review.
    total_mismatch_tolerance: float = 0.05
    # Seconds the background worker sleeps between polls when the job queue is empty.
    worker_poll_interval: float = 2.0
    # Transient-failure retry budget before a job is permanently marked 'failed'.
    max_retries: int = 3
    # Base seconds for exponential retry backoff (delay = base * 2^(retry_count - 1)).
    retry_backoff_base_seconds: float = 30.0
    # A job left in 'parsing' longer than this (e.g. a crashed worker) is re-queued by the reaper.
    parsing_stale_timeout_seconds: float = 300.0
    # Reject web uploads larger than this many bytes (default 10 MiB).
    max_upload_bytes: int = 10 * 1024 * 1024

    @field_validator("database_url", mode="after")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Turn a bare SQLite file path into a SQLAlchemy URL, validating its directory.

        A value that already carries a scheme (anything containing '://') is returned
        unchanged, so explicit 'sqlite:///...' URLs and other backends keep working. A
        bare filesystem path is treated as a SQLite database file: its parent directory
        must already exist, and the 'sqlite:///' prefix is added so end users never have
        to remember it.

        Raises:
            ValueError: If the bare path's parent directory does not exist.
        """
        if "://" in value:
            return value
        path = Path(value).expanduser()
        if not path.parent.is_dir():
            msg = (
                f"CARTLOG_DATABASE_URL is '{value}', but its directory '{path.parent}' "
                "does not exist. Create it or point at a path inside an existing directory."
            )
            raise ValueError(msg)
        return f"sqlite:///{path}"


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    return Settings()
