"""In-process login throttle. Single-process only; sufficient for a self-hosted instance."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class LoginRateLimiter:
    """Track failed login attempts per key and lock out after a threshold.

    State lives in memory, so it resets on restart and is not shared across processes. That
    is acceptable for a single-household self-hosted deployment; document this if scaled out.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        lockout_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure the attempt threshold and lockout window.

        Args:
            max_attempts: Number of failures allowed before the key is locked out.
            lockout_seconds: Duration in seconds that failures remain counted.
            clock: Callable returning the current time; injectable for deterministic tests.
        """
        self._max = max_attempts
        self._window = lockout_seconds
        self._now = clock
        self._failures: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        """Return True if another attempt is allowed for this key right now.

        Args:
            key: Opaque string identifying the subject (e.g. "username|ip").

        Returns:
            True when recent failure count is below max_attempts, False when locked out.
        """
        recent = self._recent(key)
        return len(recent) < self._max

    def record_failure(self, key: str) -> None:
        """Record a failed attempt timestamp for this key.

        Args:
            key: Opaque string identifying the subject.
        """
        # Compact the list to only recent failures before appending, keeping memory bounded.
        self._failures[key] = [*self._recent(key), self._now()]

    def reset(self, key: str) -> None:
        """Clear recorded failures for this key (call on successful login).

        Args:
            key: Opaque string identifying the subject.
        """
        self._failures.pop(key, None)

    def _recent(self, key: str) -> list[float]:
        """Return failure timestamps that fall within the current lockout window.

        Args:
            key: Opaque string identifying the subject.

        Returns:
            List of timestamps newer than (now - lockout_seconds).
        """
        cutoff = self._now() - self._window
        return [t for t in self._failures.get(key, []) if t > cutoff]
