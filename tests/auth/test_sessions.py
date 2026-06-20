"""Tests for SessionService: create, resolve (expiry + idle), revoke, revoke_all_for_user."""

from __future__ import annotations

from datetime import UTC, datetime

from cartlog.auth.sessions import SessionService
from cartlog.db.models import Role, User


def _user(session):
    """Create and flush a minimal User for session tests."""
    u = User(username="dad", password_hash="h", role=Role.ADMIN)
    session.add(u)
    session.flush()
    return u


def test_create_and_resolve(session):
    """Verify a freshly created session resolves to its owner."""
    # Given a service and a persisted user
    svc = SessionService(session)
    u = _user(session)

    # When a session is created and committed
    s = svc.create(u)
    session.commit()

    # Then resolving the session id returns the same user
    resolved = svc.resolve(s.id)
    assert resolved is not None
    assert resolved.id == u.id


def test_expired_session_resolves_none(session):
    """Verify a session whose expires_at is in the past returns None."""
    # Given a service whose clock is fixed in year 2000 (so expires_at is also in 2000)
    past = datetime(2000, 1, 1, tzinfo=UTC)
    svc = SessionService(session, clock=lambda: past)
    u = _user(session)
    s = svc.create(u)
    session.commit()

    # When resolving with the real (current) clock, the session is long expired
    assert SessionService(session).resolve(s.id) is None


def test_revoke_all_for_user(session):
    """Verify revoke_all_for_user removes every session belonging to that user."""
    # Given a user with two sessions
    svc = SessionService(session)
    u = _user(session)
    svc.create(u)
    svc.create(u)
    session.commit()

    # When all sessions for that user are revoked
    svc.revoke_all_for_user(u.id)
    session.commit()

    # Then the sessions table is empty
    from cartlog.db.models import Session as SessionRow  # noqa: PLC0415

    assert session.query(SessionRow).count() == 0
