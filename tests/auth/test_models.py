"""Tests for auth ORM models: User, Session, ApiToken, AppConfig, Role."""

from __future__ import annotations

from cartlog.db.models import ApiToken, AppConfig, Role, Session, User


def test_user_auth_columns(session):
    """Verify a User persists its auth columns with sane defaults."""
    user = User(username="dad", password_hash="hash", role=Role.ADMIN)
    session.add(user)
    session.commit()
    loaded = session.get(User, user.id)
    assert loaded.username == "dad"
    assert loaded.role == Role.ADMIN
    assert loaded.is_active is True
    assert loaded.must_change_password is False
    assert loaded.email is None


def test_session_and_token_link_to_user(session):
    """Verify sessions and API tokens reference their owning user."""
    user = User(username="mom", password_hash="h", role=Role.EDITOR)
    session.add(user)
    session.flush()
    session.add(Session(id="tok", user_id=user.id, expires_at=None))
    session.add(ApiToken(user_id=user.id, name="iphone", token_hash="th"))
    session.commit()
    assert session.query(Session).count() == 1
    assert session.query(ApiToken).count() == 1


def test_app_config_singleton(session):
    """Verify AppConfig stores the public-read flag, defaulting to True."""
    config = AppConfig(id=1)
    session.add(config)
    session.commit()
    assert session.get(AppConfig, 1).allow_anonymous_read is True
