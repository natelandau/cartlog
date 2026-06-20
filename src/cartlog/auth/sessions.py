"""Server-side session lifecycle: create, resolve (with expiry/idle), and revoke."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from cartlog.db.models import Session as SessionRow
from cartlog.db.models import User
from cartlog.web.security import generate_session_id

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session


def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    """Treat naive datetimes (SQLite returns naive) as UTC for comparison.

    SQLite stores and returns naive datetimes without tzinfo; attaching UTC lets
    us compare them against aware datetimes from the clock without a TypeError.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SessionService:
    """Manage browser sessions stored in the database so they can be revoked individually."""

    def __init__(
        self,
        session: Session,
        *,
        lifetime_days: int = 14,
        idle_timeout_days: int = 7,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        """Bind the service to a DB session and the lifetime/idle policy.

        Args:
            session: The SQLAlchemy session to use for all database operations.
            lifetime_days: Absolute session lifetime in days from creation.
            idle_timeout_days: Maximum allowed idle gap in days between accesses.
            clock: Callable returning the current UTC time; injectable for tests.
        """
        self._db = session
        self._lifetime = timedelta(days=lifetime_days)
        self._idle = timedelta(days=idle_timeout_days)
        self._now = clock

    def create(
        self,
        user: User,
        *,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> SessionRow:
        """Create and persist a new session row for the user.

        Sets created_at and last_seen_at explicitly to the clock value rather than
        relying on server defaults so the row is immediately usable in-process
        before the transaction is flushed to the database.

        Args:
            user: The authenticated user who owns this session.
            user_agent: The HTTP User-Agent string from the browser, if available.
            ip: The client IP address, if available.

        Returns:
            The newly created, unsaved SessionRow; call commit() to persist.
        """
        now = self._now()
        row = SessionRow(
            id=generate_session_id(),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + self._lifetime,
            user_agent=user_agent,
            ip=ip,
        )
        self._db.add(row)
        return row

    def resolve(self, session_id: str | None) -> User | None:
        """Return the active user for a session id, or None if missing/expired/idle/inactive.

        Enforces both the absolute expiry (expires_at) and the idle timeout
        (last_seen_at + idle_timeout). Expired/idle sessions are deleted on
        resolution so the database self-cleans over time. Active sessions have
        last_seen_at slid forward to extend the idle window.

        Args:
            session_id: The opaque session id from the browser cookie.

        Returns:
            The owning User if the session is valid and the user is active, else None.
        """
        if not session_id:
            return None
        row: SessionRow | None = self._db.get(SessionRow, session_id)
        if row is None:
            return None
        now = self._now()
        expired = row.expires_at is not None and _aware(row.expires_at) <= now
        idle = _aware(row.last_seen_at) + self._idle <= now
        if expired or idle:
            self._db.delete(row)
            return None
        if row.user is None or not row.user.is_active:
            return None
        # Slide the idle window so active users are not interrupted mid-session.
        row.last_seen_at = now
        return row.user

    def revoke(self, session_id: str) -> None:
        """Delete a single session, silently ignoring missing ids.

        Args:
            session_id: The opaque session id to remove.
        """
        row: SessionRow | None = self._db.get(SessionRow, session_id)
        if row is not None:
            self._db.delete(row)

    def revoke_all_for_user(self, user_id: int) -> None:
        """Delete every session belonging to a user.

        Use this when a password changes or an account is disabled so all
        existing browser sessions are immediately invalidated.

        Args:
            user_id: The primary key of the user whose sessions should be removed.
        """
        self._db.query(SessionRow).filter(SessionRow.user_id == user_id).delete()
