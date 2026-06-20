"""API token lifecycle: mint (plaintext shown once), resolve, list, revoke."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cartlog.auth.security import generate_api_token, hash_token
from cartlog.db.models import ApiToken, User

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ApiTokenService:
    """Mint and verify per-user API tokens for programmatic upload."""

    def __init__(self, session: Session) -> None:
        """Bind the service to a database session."""
        self._db = session

    def mint(self, user: User, name: str) -> tuple[ApiToken, str]:
        """Create a token for the user and return the row and the one-time plaintext.

        The plaintext is shown only once; only its hash is stored. Callers must
        commit the session to persist the new row.

        Args:
            user: The user who owns the token.
            name: A human-readable label (e.g. "iphone") to identify the token.

        Returns:
            A tuple of (ApiToken row, plaintext token string).
        """
        plaintext = generate_api_token()
        row = ApiToken(user_id=user.id, name=name.strip(), token_hash=hash_token(plaintext))
        self._db.add(row)
        return row, plaintext

    def resolve(self, plaintext: str | None) -> User | None:
        """Return the active user owning a non-revoked token, updating last_used_at.

        Use this to authenticate an incoming API request. Returns None for missing,
        revoked, or inactive-user tokens so callers can treat all failure cases uniformly.

        Args:
            plaintext: The raw API token from the request header, or None.

        Returns:
            The owning User if the token is valid and active, else None.
        """
        if not plaintext:
            return None
        row = self._db.scalar(
            select(ApiToken).where(
                ApiToken.token_hash == hash_token(plaintext),
                ApiToken.revoked_at.is_(None),
            )
        )
        if row is None or row.user is None or not row.user.is_active:
            return None
        row.last_used_at = datetime.now(UTC)
        return row.user

    def list_for_user(self, user_id: int) -> list[ApiToken]:
        """Return a user's tokens ordered newest first.

        Args:
            user_id: The primary key of the user whose tokens to fetch.

        Returns:
            A list of ApiToken rows, most recently created first.
        """
        return list(
            self._db.scalars(
                select(ApiToken)
                .where(ApiToken.user_id == user_id)
                .order_by(ApiToken.created_at.desc())
            )
        )

    def revoke(self, token_id: int, user_id: int) -> None:
        """Soft-revoke a token the user owns, ignoring already-revoked tokens.

        Ownership is checked to prevent one user from revoking another's tokens.
        Already-revoked tokens are silently ignored so this is safe to call twice.

        Args:
            token_id: The primary key of the token to revoke.
            user_id: The primary key of the requesting user (ownership check).
        """
        row = self._db.get(ApiToken, token_id)
        if row is not None and row.user_id == user_id and row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
