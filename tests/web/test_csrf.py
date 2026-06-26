"""Tests for the web-layer CSRF signing helpers."""

from cartlog.web import security


def test_csrf_round_trip():
    """Verify a CSRF token verifies with its key and fails with any other key."""
    # Given a token signed with a key
    key = "s" * 32
    token = security.make_csrf_token(key)
    # Then it verifies with that key and fails for any other key or garbage
    assert security.verify_csrf_token(token, key) is True
    assert security.verify_csrf_token(token, "other-key") is False
    assert security.verify_csrf_token("garbage", key) is False
