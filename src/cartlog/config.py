"""Runtime configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Config file holding all options. Loaded into the process environment by get_settings so both
# cartlog's CARTLOG_ settings and the LLM provider's native key (e.g. ANTHROPIC_API_KEY) resolve
# from one file, while exported environment variables still take precedence.
_ENV_FILE = ".env.secret"


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (prefix CARTLOG_)."""

    model_config = SettingsConfigDict(env_prefix="CARTLOG_", extra="ignore")

    # Provider-prefixed model id for the receipt vision parser (e.g. "openai:gpt-5.2").
    parse_model: str = "anthropic:claude-opus-4-8"
    # Provider-prefixed id for the cheap, text-only assist model used by focused secondary
    # passes (category reclassification and size extraction). Needs structured output; no vision.
    assist_model: str = "anthropic:claude-haiku-4-5"
    # Max times the LLM reclassifier is spent on a still-Uncategorized product before it is
    # left as-is for manual review (prevents unbounded retries on products it cannot place).
    max_reclassify_attempts: int = 2
    # Max times the LLM size extractor is spent on a line that still has no resolvable size
    # before it is left as-is for manual review.
    max_size_extract_attempts: int = 2

    # Accepts a bare SQLite file path (e.g. /app/data.db); the sqlite:/// prefix is added
    # automatically by the validator so users never have to remember it.
    database_url: str = "cartlog.db"
    # Where ingested receipt images are copied and retained.
    image_storage_dir: Path = Path("receipt_images")
    # Default destination directory for `cartlog backup` when no --output is given. Unset means
    # the CLI writes into the current working directory. The web download is always streamed and
    # never written here. An explicit --output always overrides this.
    backup_dir: Path | None = None
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

    # Signs CSRF tokens and any cookie payloads. No safe default exists, so the
    # app refuses to start without it. Generate one with `openssl rand -hex 32`.
    # The empty-string default exists only so pydantic-settings can construct the model
    # without requiring the field as a positional argument; the validator below rejects
    # empty or whitespace-only values at startup so production safety is preserved.
    secret_key: str = ""
    # Mark auth cookies Secure (and use the __Host- session cookie name) when serving over
    # HTTPS. The Secure flag is applied only to requests that actually arrive over HTTPS, so
    # plain-HTTP LAN/dev access keeps working even with this left at the default. Set False to
    # never mark cookies Secure (e.g. an unusual proxy setup that misreports the scheme).
    cookie_secure: bool = True
    # Absolute session lifetime and sliding idle timeout, in days.
    session_lifetime_days: int = 14
    session_idle_timeout_days: int = 7

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

    @field_validator("backup_dir", mode="after")
    @classmethod
    def _resolve_backup_dir(cls, value: Path | None) -> Path | None:
        """Resolve CARTLOG_BACKUP_DIR, rejecting only a path that exists but is not a directory.

        A missing directory is allowed: it is provisioned on demand at startup and before a
        backup is written, exactly like image_storage_dir, so a fresh volume mount (e.g.
        Docker's /data/backups) needs no manual mkdir. An unset value is left as None so the
        CLI falls back to the working directory.

        Raises:
            ValueError: If the configured path exists but is not a directory.
        """
        if value is None:
            return None
        path = value.expanduser()
        # Anchor a relative path to the current working directory now, so the resolved
        # destination cannot drift if the process's cwd changes before a backup runs.
        if not path.is_absolute():
            path = Path.cwd() / path
        if path.exists() and not path.is_dir():
            msg = f"CARTLOG_BACKUP_DIR is '{value}', but that path is not a directory."
            raise ValueError(msg)
        return path

    @field_validator("secret_key", mode="after")
    @classmethod
    def _require_secret_key(cls, value: str) -> str:
        """Reject startup when CARTLOG_SECRET_KEY is absent or blank.

        The field carries an empty-string default so pydantic-settings can
        construct Settings without requiring it as a positional argument
        (which lets all call-sites omit it while reading it from the
        environment at runtime). The validator re-enforces the invariant:
        a blank key is never accepted in production.

        Raises:
            ValueError: If the key is empty or whitespace-only.
        """
        if not value or not value.strip():
            msg = "CARTLOG_SECRET_KEY is required; generate one with: openssl rand -hex 32"
            raise ValueError(msg)
        return value


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance.

    Load `.env.secret` into the process environment first, without overriding variables that
    are already exported. This lets users set every option in the file (including each LLM
    provider's native key, which the provider SDK reads straight from the environment) while
    exported environment variables still win.
    """
    load_dotenv(_ENV_FILE, override=False)
    # pydantic-settings populates secret_key from CARTLOG_SECRET_KEY at runtime;
    # the validator in Settings rejects startup if the env var is absent or blank.
    return Settings()
