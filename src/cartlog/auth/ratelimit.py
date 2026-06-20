"""In-process login throttle. Single-process only; sufficient for a self-hosted instance."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class LoginRateLimiter:
    """Track failed login attempts per key and lock out after a threshold.

    State lives in memory, so it resets on restart and is not shared across processes. That
    is acceptable for a single-household self-hosted deployment; document this if scaled out.

    The login endpoint is unauthenticated, so a flood of distinct keys (e.g. sprayed
    usernames) would otherwise leave a dict entry per key forever. A periodic sweep drops
    keys whose failures have all aged out, bounding the table to keys that failed within
    roughly the last two windows rather than every key ever seen.
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
        self._failures: dict[str, list[float]] = {}
        # Sweep at most once per window so the cost is amortized rather than paid per attempt.
        self._next_sweep = clock() + lockout_seconds

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
        self._maybe_sweep()
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

    def _maybe_sweep(self) -> None:
        """Drop keys whose failures have all aged out, at most once per window.

        Without this the table would retain an entry for every key ever seen, since reads
        filter expired timestamps but never delete the key. Rebuilding only the still-recent
        keys bounds the table to roughly the keys that failed within the last two windows.
        """
        now = self._now()
        if now < self._next_sweep:
            return
        self._next_sweep = now + self._window
        cutoff = now - self._window
        self._failures = {
            key: recent
            for key, timestamps in self._failures.items()
            if (recent := [t for t in timestamps if t > cutoff])
        }
