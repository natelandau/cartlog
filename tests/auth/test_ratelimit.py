"""Tests for the login rate limiter."""

from cartlog.auth.ratelimit import LoginRateLimiter


def test_locks_out_after_max_attempts():
    """Verify that check returns False after max_attempts failures, and True after the window elapses."""
    now = [1000.0]
    rl = LoginRateLimiter(max_attempts=3, lockout_seconds=60, clock=lambda: now[0])
    for _ in range(3):
        assert rl.check("dad|1.1.1.1") is True
        rl.record_failure("dad|1.1.1.1")
    assert rl.check("dad|1.1.1.1") is False
    now[0] += 61  # lockout window elapses
    assert rl.check("dad|1.1.1.1") is True


def test_reset_clears_failures():
    """Verify that reset removes all recorded failures so check returns True immediately."""
    rl = LoginRateLimiter(max_attempts=2, lockout_seconds=60, clock=lambda: 0.0)
    rl.record_failure("k")
    rl.reset("k")
    assert rl.check("k") is True
