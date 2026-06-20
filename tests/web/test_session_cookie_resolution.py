"""Verify load_user resolves a valid session cookie even when a stale cookie is also present.

Regression for the db-wipe-without-clearing-browser scenario: an old __Host- session cookie
(which Chrome still sends over http://localhost) lingered alongside a fresh plain cookie and,
because it was checked first, masked the valid session and dropped the user to anonymous.
"""

from __future__ import annotations

from types import SimpleNamespace

from cartlog.auth.sessions import SessionService
from cartlog.db.models import Role
from cartlog.web.guards import COOKIE_NAME, load_user
from tests.factories import seed_user


def _request(cookies: dict[str, str]) -> SimpleNamespace:
    """Build a minimal request stub exposing .cookies and .headers for load_user."""
    return SimpleNamespace(cookies=cookies, headers={})


def test_load_user_prefers_valid_plain_cookie_over_stale_host_cookie(session) -> None:
    """Verify a dead __Host- cookie does not mask a valid plain session cookie."""
    # Given a real user with a live session
    user = seed_user(session, username="dad", role=Role.ADMIN)
    live = SessionService(session).create(user)
    session.commit()

    # And a request carrying a stale (dead) __Host- cookie plus the valid plain cookie
    request = _request({COOKIE_NAME: "stale-dead-session-id", "cartlog_session": live.id})

    # When the current user is resolved
    resolved = load_user(request, session)  # ty: ignore[invalid-argument-type]

    # Then the valid plain cookie wins instead of being masked by the dead __Host- cookie
    assert resolved is not None
    assert resolved.id == user.id


def test_load_user_returns_none_when_all_session_cookies_are_dead(session) -> None:
    """Verify load_user returns None when neither cookie maps to a live session."""
    # Given a request whose only cookies are stale ids with no matching session rows
    request = _request({COOKIE_NAME: "dead-a", "cartlog_session": "dead-b"})

    # When resolving / Then nothing authenticates
    assert load_user(request, session) is None  # ty: ignore[invalid-argument-type]
