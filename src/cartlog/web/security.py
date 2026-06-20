"""Stateless security primitives: password hashing, opaque tokens, and CSRF tokens."""

from __future__ import annotations

import hashlib
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash

# Argon2id with library-recommended parameters; the single source of truth for hashing.
_password_hash = PasswordHash.recommended()

# Namespace for CSRF tokens so the same secret key can sign other payloads without collision.
_CSRF_SALT = "cartlog-csrf"


def hash_password(plain: str) -> str:
    """Hash a plaintext password with Argon2id for storage.

    Args:
        plain: The plaintext password to hash.

    Returns:
        An Argon2id hash string suitable for database storage.
    """
    return _password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if the plaintext matches the stored hash.

    Args:
        plain: The plaintext password to check.
        hashed: The stored Argon2id hash.

    Returns:
        True if the password matches, False otherwise.
    """
    return _password_hash.verify(plain, hashed)


def needs_rehash(hashed: str) -> bool:
    """Return True if the stored hash should be upgraded to current parameters.

    Delegates to the current hasher's check so callers need not import pwdlib.
    This only inspects hash metadata, not the plaintext, so it is safe to call
    in a read-only context before a login is confirmed.

    Args:
        hashed: The stored Argon2id hash to inspect.

    Returns:
        True if the hash was created with outdated parameters.
    """
    return _password_hash.current_hasher.check_needs_rehash(hashed)


# A throwaway hash verified on the unknown-user login path so timing does not reveal
# whether a username exists. Computed once at import time.
_DUMMY_HASH = _password_hash.hash("cartlog-dummy-password")


def dummy_verify() -> None:
    """Perform a verify against a fixed hash to equalize timing for unknown usernames.

    Call this on the code path where a user is not found so the response time
    matches the path where password verification actually runs.
    """
    _password_hash.verify("cartlog-dummy-password-x", _DUMMY_HASH)


def generate_api_token() -> str:
    """Return a new high-entropy API token with a recognizable prefix.

    Returns:
        A token in the form ``cartlog_<urlsafe_random>``.
    """
    return f"cartlog_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest used to store and look up an API token.

    Tokens are stored as hashes so a database breach does not expose bearer credentials.

    Args:
        token: The raw API token returned to the caller.

    Returns:
        A 64-character hex string safe for database storage.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_session_id() -> str:
    """Return a new opaque session id used as the cookie value.

    Returns:
        A URL-safe random string of sufficient entropy for session identification.
    """
    return secrets.token_urlsafe(32)


def _csrf_serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=_CSRF_SALT)


def make_csrf_token(secret_key: str) -> str:
    """Return a signed, timestamped CSRF token.

    Args:
        secret_key: The application secret used to sign the token.

    Returns:
        A URL-safe signed token string.
    """
    return _csrf_serializer(secret_key).dumps(secrets.token_urlsafe(16))


def verify_csrf_token(token: str, secret_key: str, max_age_seconds: int = 86400) -> bool:
    """Return True if the CSRF token is a valid, unexpired signature for this key.

    Args:
        token: The CSRF token from the request.
        secret_key: The application secret used to verify the signature.
        max_age_seconds: Maximum token age before it is considered expired.

    Returns:
        True if the token is valid and not expired, False otherwise.
    """
    try:
        _csrf_serializer(secret_key).loads(token, max_age=max_age_seconds)
    except BadSignature, SignatureExpired:
        return False
    return True
