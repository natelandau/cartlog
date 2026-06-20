"""User account operations: creation, authentication, and administration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from cartlog.auth.security import (
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)
from cartlog.db.models import Role, User

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class UserService:
    """Create and manage user accounts and verify login credentials."""

    def __init__(self, session: Session) -> None:
        """Bind the service to a database session."""
        self._db = session

    def count(self) -> int:
        """Return the number of user accounts (used by the setup gate)."""
        return self._db.scalar(select(func.count()).select_from(User)) or 0

    def get_by_username(self, username: str) -> User | None:
        """Return the user with this username (case-insensitive), or None."""
        return self._db.scalar(select(User).where(User.username == username.strip().lower()))

    def list_users(self) -> list[User]:
        """Return all users ordered by username."""
        return list(self._db.scalars(select(User).order_by(User.username)))

    def create_user(
        self,
        username: str,
        password: str,
        role: Role,
        *,
        name: str | None = None,
        must_change_password: bool = False,
    ) -> User:
        """Create and persist a user with a hashed password (caller commits).

        Args:
            username: The login handle; stored lowercased for case-insensitive lookup.
            password: The plaintext password to hash and store.
            role: The access tier to grant.
            name: Optional display name shown in the UI.
            must_change_password: Force a password change before any other action.

        Returns:
            The newly created User instance (not yet committed).
        """
        user = User(
            username=username.strip().lower(),
            name=name,
            password_hash=hash_password(password),
            role=role,
            must_change_password=must_change_password,
        )
        self._db.add(user)
        return user

    def authenticate(self, username: str, password: str) -> User | None:
        """Return the user if credentials are valid and the account is active, else None.

        Runs dummy_verify() when the username is unknown so response timing does not
        reveal whether the username exists. Returns None for inactive accounts even
        when the password is correct.

        Args:
            username: The login handle to look up.
            password: The plaintext password to check.

        Returns:
            The authenticated User, or None on failure.
        """
        user = self.get_by_username(username)
        if user is None:
            # Equalize timing so unknown usernames are indistinguishable from wrong passwords.
            dummy_verify()
            return None
        if not verify_password(password, user.password_hash):
            return None
        if not user.is_active:
            return None
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        user.last_login_at = datetime.now(UTC)
        return user

    def set_role(self, user: User, role: Role) -> None:
        """Change a user's role.

        Args:
            user: The user to update.
            role: The new role to assign.
        """
        user.role = role

    def set_password(self, user: User, password: str, *, must_change: bool = False) -> None:
        """Set a new password hash and optionally force a change on next login.

        Args:
            user: The user whose password to update.
            password: The new plaintext password to hash and store.
            must_change: When True, require the user to change their password before proceeding.
        """
        user.password_hash = hash_password(password)
        user.must_change_password = must_change

    def set_active(self, user: User, *, active: bool) -> None:
        """Activate or deactivate a user account.

        Args:
            user: The user to update.
            active: True to enable login, False to disable it.
        """
        user.is_active = active
