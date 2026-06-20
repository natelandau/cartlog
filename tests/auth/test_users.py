"""Tests for UserService: creation, authentication, and account management."""

from cartlog.auth.users import UserService
from cartlog.db.models import Role


def test_create_and_authenticate(session):
    """Verify that a created user authenticates with correct credentials and fails with wrong ones."""
    # Given a new user
    svc = UserService(session)
    svc.create_user("dad", "violet pantry koala", Role.ADMIN)
    session.commit()

    # When authenticating
    # Then correct password succeeds, wrong password fails, unknown user fails
    assert svc.authenticate("dad", "violet pantry koala") is not None
    assert svc.authenticate("dad", "wrong") is None
    assert svc.authenticate("ghost", "whatever") is None


def test_username_is_case_insensitive(session):
    """Verify that usernames are stored and matched in lowercase regardless of input case."""
    # Given a user created with mixed-case username
    svc = UserService(session)
    svc.create_user("Dad", "violet pantry koala", Role.VIEWER)
    session.commit()

    # When looking up by lowercase username
    # Then the user is found
    assert svc.get_by_username("dad") is not None


def test_inactive_user_cannot_authenticate(session):
    """Verify that a deactivated user cannot authenticate even with correct credentials."""
    # Given an inactive user
    svc = UserService(session)
    user = svc.create_user("mom", "violet pantry koala", Role.EDITOR)
    svc.set_active(user, active=False)
    session.commit()

    # When authenticating
    # Then authentication fails
    assert svc.authenticate("mom", "violet pantry koala") is None
