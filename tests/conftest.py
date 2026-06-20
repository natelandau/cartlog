"""Shared pytest fixtures for the cartlog test suite."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from cartlog.db import models  # noqa: F401  # registers tables on Base.metadata
from cartlog.db.base import Base
from cartlog.parsing.schema import ParsedLineItem, ParsedReceipt

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate tests from a developer's real .env.secret by loading from a nonexistent file."""
    monkeypatch.setattr("cartlog.config._ENV_FILE", str(tmp_path / "absent.env"))


@pytest.fixture(autouse=True)
def _test_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a deterministic secret key so Settings construction succeeds in tests."""
    monkeypatch.setenv("CARTLOG_SECRET_KEY", "test-secret-key-0123456789abcdef")


@pytest.fixture(autouse=True)
def _dummy_provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a dummy provider credential so model construction in tests needs no real credentials.

    Model construction reads the provider's key env var but makes no network call; a dummy
    value lets the early-guard and factory code paths run. Tests that exercise the
    missing-key path delete this var themselves.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _restore_template_auto_reload() -> Generator[None]:
    """Restore the shared Jinja2 auto_reload flag after each test.

    `create_app(dev=...)` mutates the process-global `templates` singleton, so tests that
    build dev/prod apps would otherwise leak that flag into later tests and make assertions
    on template reloading order-dependent.
    """
    from cartlog.web.templating import templates  # noqa: PLC0415

    original = templates.env.auto_reload
    yield
    templates.env.auto_reload = original


@pytest.fixture
def session_factory():
    """Return a session factory bound to a fresh in-memory SQLite database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    # Dispose so the in-memory connection is closed; otherwise Python 3.14 emits a
    # ResourceWarning at GC time, which the strict warning filter promotes to an error.
    engine.dispose()


@pytest.fixture
def session(session_factory) -> Generator[Session]:
    """Yield a session from the in-memory database, closed on teardown."""
    with session_factory() as s:
        yield s


@pytest.fixture
def sample_parsed_receipt() -> ParsedReceipt:
    """Return a representative two-line ParsedReceipt for use across tests."""
    return ParsedReceipt(
        store_name="Safeway",
        store_location="Main St",
        purchase_date=date(2026, 3, 1),
        currency="USD",
        total=6.96,
        confidence=0.95,
        line_items=[
            ParsedLineItem(
                raw_description="GV LRG EGGS 12CT",
                canonical_name="eggs",
                category="dairy & eggs",
                quantity=1,
                unit_size="12CT",
                unit_price=3.48,
                line_total=3.48,
            ),
            ParsedLineItem(
                raw_description="BANANAS",
                canonical_name="bananas",
                category="produce",
                quantity=2,
                unit="lb",
                unit_price=1.74,
                line_total=3.48,
            ),
        ],
    )


class FakeReceiptParser:
    """A ReceiptParser that returns a fixed result without calling any LLM."""

    def __init__(self, result: ParsedReceipt):
        """Store the fixed ParsedReceipt this parser will always return."""
        self._result = result
        self.calls: list[Path] = []

    def parse(self, file_path: Path, *, usage=None) -> ParsedReceipt:
        self.calls.append(file_path)
        return self._result


@pytest.fixture
def fake_parser(sample_parsed_receipt) -> FakeReceiptParser:
    """Return a FakeReceiptParser preloaded with the sample receipt."""
    return FakeReceiptParser(sample_parsed_receipt)
