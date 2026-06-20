"""Tests for stateless security primitives."""

from cartlog.web import security


def test_password_round_trip():
    """Verify that a hashed password verifies correctly and rejects wrong inputs."""
    hashed = security.hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert security.verify_password("correct horse battery staple", hashed) is True
    assert security.verify_password("wrong", hashed) is False


def test_token_hashing_is_stable_and_opaque():
    """Verify that token hashing produces a stable, opaque, prefixed output."""
    token = security.generate_api_token()
    assert token.startswith("cartlog_")
    h = security.hash_token(token)
    assert h == security.hash_token(token)
    assert h != token


def test_csrf_round_trip():
    """Verify that a CSRF token verifies with its key and fails with any other key."""
    key = "s" * 32
    token = security.make_csrf_token(key)
    assert security.verify_csrf_token(token, key) is True
    assert security.verify_csrf_token(token, "other-key") is False
    assert security.verify_csrf_token("garbage", key) is False


def test_needs_rehash_returns_bool_for_fresh_hash():
    """Verify the real pwdlib API is exercised: a fresh hash should not need rehashing."""
    hashed = security.hash_password("some-password")
    result = security.needs_rehash(hashed)
    assert isinstance(result, bool)
    assert result is False
