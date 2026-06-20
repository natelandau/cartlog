"""Tests for password validation rules enforced by validate_password."""

from cartlog.auth.passwords import validate_password


def test_rejects_short_password():
    """Verify that passwords below the minimum length are rejected."""
    assert validate_password("short") is not None


def test_rejects_common_password():
    """Verify that well-known common passwords are rejected regardless of length."""
    assert validate_password("password1234") is not None


def test_accepts_strong_passphrase():
    """Verify that a multi-word passphrase passes all validation rules."""
    assert validate_password("violet pantry koala lamp") is None
