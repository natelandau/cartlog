"""Tests for ApiTokenService: mint, resolve, revoke lifecycle."""

from cartlog.auth.tokens import ApiTokenService
from cartlog.auth.users import UserService
from cartlog.db.models import Role


def _editor(session):
    """Create and flush a test editor user."""
    user = UserService(session).create_user("mom", "violet pantry koala", Role.EDITOR)
    session.flush()
    return user


def test_mint_and_resolve(session):
    """Verify that a minted token resolves to its owner and that an unknown token returns None."""
    # Given a user and a minted token
    svc = ApiTokenService(session)
    user = _editor(session)
    _row, plaintext = svc.mint(user, "iphone")
    session.commit()

    # When resolving the plaintext token
    # Then the correct user is returned and a bogus token returns None
    resolved = svc.resolve(plaintext)
    assert resolved is not None
    assert resolved.id == user.id
    assert svc.resolve("cartlog_bogus") is None


def test_revoked_token_resolves_none(session):
    """Verify that a revoked token no longer resolves to any user."""
    # Given a user with a minted token
    svc = ApiTokenService(session)
    user = _editor(session)
    row, plaintext = svc.mint(user, "iphone")
    session.commit()

    # When the token is revoked
    svc.revoke(row.id, user.id)
    session.commit()

    # Then resolving the plaintext returns None
    assert svc.resolve(plaintext) is None
