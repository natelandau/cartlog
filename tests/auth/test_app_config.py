"""Tests for AppConfig seeding and persistence."""

from cartlog.auth.config import AppConfigService
from cartlog.db.models import AppConfig
from cartlog.db.seed import seed_app_config


def test_seed_app_config_is_idempotent(session):
    """Seeding twice yields exactly one config row defaulting to open read access."""
    seed_app_config(session)
    seed_app_config(session)
    session.commit()
    rows = session.query(AppConfig).all()
    assert len(rows) == 1
    assert rows[0].allow_anonymous_read is True


def test_toggle_anonymous_read(session):
    """Verify toggling anonymous read access via AppConfigService."""
    svc = AppConfigService(session)
    assert svc.allow_anonymous_read() is True
    svc.set_allow_anonymous_read(value=False)
    session.commit()
    assert AppConfigService(session).allow_anonymous_read() is False
