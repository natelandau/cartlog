"""CSRF token signing for the web layer."""

from __future__ import annotations

import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Namespace for CSRF tokens so the same secret key can sign other payloads without collision.
_CSRF_SALT = "cartlog-csrf"


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
