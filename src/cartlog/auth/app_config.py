"""Read and update the singleton app_config row (admin-editable runtime config, not the app Settings in cartlog.config)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cartlog.db.models import AppConfig

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class AppConfigService:
    """Access the runtime, admin-editable application configuration."""

    def __init__(self, session: Session) -> None:
        """Bind the service to a database session."""
        self._db = session

    def get(self) -> AppConfig:
        """Return the singleton config row, creating it if missing."""
        config = self._db.get(AppConfig, 1)
        if config is None:
            config = AppConfig(id=1)
            self._db.add(config)
            self._db.flush()
        return config

    def allow_anonymous_read(self) -> bool:
        """Return whether unauthenticated visitors may browse read-only pages."""
        return self.get().allow_anonymous_read

    def set_allow_anonymous_read(self, *, value: bool) -> None:
        """Set the public-read posture (caller commits)."""
        self.get().allow_anonymous_read = value
