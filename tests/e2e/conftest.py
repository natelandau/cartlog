"""Fixtures for Playwright browser end-to-end tests.

Runs the real cartlog web app (the FastAPI factory, so no LLM API keys are required)
against a temporary, seeded SQLite database and drives it with a headless browser. These
tests are marked `e2e` and deselected by default (see pyproject `addopts`); run them with
`uv run duty e2e` or `uv run pytest -m e2e tests/e2e`.

A reusable harness: the `live_server` fixture stands up the app and yields its base URL, and
the `page` fixture yields an isolated browser page. Front-end checks should assert on rendered
behavior and on response status codes (no 4xx), not on template source alone.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import uvicorn
from playwright.sync_api import sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cartlog.config import get_settings
from cartlog.db.base import Base
from cartlog.db.models import Role
from cartlog.db.seed import seed_app_config
from tests.factories import seed_receipts, seed_user

if TYPE_CHECKING:
    from collections.abc import Iterator

    from playwright.sync_api import Browser, Page

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Settings the live server needs that are independent of the temp paths chosen per session.
_STATIC_SERVER_ENV = {
    "CARTLOG_SECRET_KEY": "e2e-secret-key-0123456789abcdef0123456789",  # gitleaks:allow - throwaway test key
    "CARTLOG_COOKIE_SECURE": "false",
}


def _free_port() -> int:
    """Return an unused localhost TCP port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_up(url: str, *, timeout: float = 20.0) -> None:
    """Block until the server answers `url`, or raise after `timeout` seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with contextlib.suppress(OSError), urllib.request.urlopen(url, timeout=1):  # noqa: S310 - localhost only
            return
        time.sleep(0.2)
    msg = f"e2e live server did not become ready at {url}"
    raise RuntimeError(msg)


@pytest.fixture(scope="session")
def _built_css() -> None:
    """Compile the daisyUI/Tailwind stylesheet so layout-dependent assertions are meaningful.

    Skips the e2e session when the Node toolchain is unavailable.
    """
    if shutil.which("npm") is None:
        pytest.skip("npm is required to build the CSS for browser e2e tests")
    subprocess.run(
        ["npm", "run", "build:css"],  # noqa: S607
        cwd=_PROJECT_ROOT,
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session")
def live_server(tmp_path_factory: pytest.TempPathFactory, _built_css: None) -> Iterator[str]:
    """Run the cartlog app against a temporary seeded DB and yield its base URL.

    Uses the app factory directly rather than `cartlog serve`, so no LLM API keys are needed.
    Anonymous read defaults on, so the pages load without authentication.
    """
    workdir = tmp_path_factory.mktemp("e2e")
    storage = workdir / "storage"
    storage.mkdir()
    db_path = workdir / "e2e.db"

    managed_keys = (*_STATIC_SERVER_ENV, "CARTLOG_DATABASE_URL", "CARTLOG_IMAGE_STORAGE_DIR")
    previous_env = {key: os.environ.get(key) for key in managed_keys}
    os.environ.update(_STATIC_SERVER_ENV)
    os.environ["CARTLOG_DATABASE_URL"] = str(db_path)
    os.environ["CARTLOG_IMAGE_STORAGE_DIR"] = str(storage)
    get_settings.cache_clear()

    engine = create_engine(get_settings().database_url)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        seed_receipts(session)
        seed_app_config(session)
        # A user must exist or the setup-gate middleware redirects every page to /setup.
        # Anonymous read is on (seed_app_config default), so the pages still load without login.
        seed_user(session, username="e2e-admin", role=Role.ADMIN)
        session.commit()
    engine.dispose()

    port = _free_port()
    config = uvicorn.Config(
        "cartlog.web.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_up(f"{base_url}/insights/store-comparison")
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def _browser() -> Iterator[Browser]:
    """Launch a headless Chromium for the session, skipping when the browser is not installed."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - any launch failure means skip, not fail
            pytest.skip(f"Chromium not installed; run `uv run playwright install chromium` ({exc})")
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def page(_browser: Browser) -> Iterator[Page]:
    """Yield a fresh browser page in an isolated context for one test."""
    context = _browser.new_context(viewport={"width": 1400, "height": 900})
    try:
        yield context.new_page()
    finally:
        context.close()
