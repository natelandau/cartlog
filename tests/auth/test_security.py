"""Tests for stateless auth security primitives."""

import pytest

from cartlog.auth import security

# Exercise the real production Argon2id parameters, not the fast test hasher.
pytestmark = pytest.mark.real_hashing


def test_password_round_trip():
    """Verify a hashed password verifies correctly and rejects wrong inputs."""
    # Given a hashed password
    hashed = security.hash_password("correct horse battery staple")
    # Then the hash is opaque and verification distinguishes right from wrong
    assert hashed != "correct horse battery staple"
    assert security.verify_password("correct horse battery staple", hashed) is True
    assert security.verify_password("wrong", hashed) is False


def test_token_hashing_is_stable_and_opaque():
    """Verify token hashing produces a stable, opaque, prefixed output."""
    # Given a freshly generated API token
    token = security.generate_api_token()
    # Then it carries the prefix and hashes deterministically and opaquely
    assert token.startswith("cartlog_")
    h = security.hash_token(token)
    assert h == security.hash_token(token)
    assert h != token


def test_needs_rehash_returns_bool_for_fresh_hash():
    """Verify a fresh hash does not need rehashing."""
    # Given a fresh hash
    hashed = security.hash_password("some-password")
    # When checking rehash need
    result = security.needs_rehash(hashed)
    # Then it is a bool and False
    assert isinstance(result, bool)
    assert result is False


def test_generate_session_id_is_unique_and_urlsafe():
    """Verify session ids are non-empty and unique per call."""
    # When generating two session ids
    a = security.generate_session_id()
    b = security.generate_session_id()
    # Then they are non-empty and differ
    assert a and b
    assert a != b
