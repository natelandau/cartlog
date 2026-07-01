"""Health probe for container healthchecks and uptime monitors.

cartlog runs as a single `cartlog serve` process (web server, ingestion workers, and the local
SQLite database all in one place), so the usual Kubernetes liveness/readiness split buys nothing
here: there is no load-balancer rotation to pull an instance out of, and the database is a local
file rather than an independently-failing network dependency. One `/healthz` endpoint therefore
does the meaningful work: it verifies the database is reachable, the schema is migrated to head,
and (when running under `serve`) at least one ingestion worker thread is alive. A failing check
returns 503 so Docker healthchecks and uptime monitors see the instance as unhealthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

router = APIRouter(tags=["health"])

# Exact paths the setup-gate, force-password-change, and CSRF middlewares must let through
# unauthenticated and without a redirect, so a probe gets a real status code instead of a 303
# to /setup. middleware.py imports this as the single source of truth.
HEALTH_PATHS: frozenset[str] = frozenset({"/healthz"})

# Resolved against the working directory, matching how bootstrap.prepare_runtime locates the
# Alembic config; `cartlog serve` runs from the project root.
_ALEMBIC_INI = "alembic.ini"


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single health check: whether it passed and a human-readable detail."""

    ok: bool
    detail: str


def _check_database(session_factory: sessionmaker[Session]) -> CheckResult:
    """Confirm the database answers a trivial query."""
    with session_factory() as session:
        session.execute(text("SELECT 1"))
    return CheckResult(ok=True, detail="reachable")


@lru_cache(maxsize=1)
def _expected_heads() -> frozenset[str]:
    """Return the Alembic script head revisions, computed once per process.

    Migrations run only at startup, so the heads are fixed for the process lifetime; caching
    keeps every probe from re-walking alembic/versions/ off disk.
    """
    return frozenset(ScriptDirectory.from_config(Config(_ALEMBIC_INI)).get_heads())


def _check_migrations(engine: Engine) -> CheckResult:
    """Confirm the database schema is stamped at the latest Alembic head."""
    expected = _expected_heads()
    with engine.connect() as connection:
        current = frozenset(MigrationContext.configure(connection).get_current_heads())
    if current == expected:
        return CheckResult(ok=True, detail=f"at head ({', '.join(sorted(expected)) or 'none'})")
    return CheckResult(ok=False, detail=f"current {sorted(current)} != head {sorted(expected)}")


def _check_worker(app: FastAPI) -> CheckResult:
    """Confirm at least one ingestion worker thread is alive.

    The worker pool is owned by the `serve` command, which registers its threads on
    `app.state.worker_threads`. When the app runs without a pool (the test harness or a
    web-only embedding), there is nothing to monitor and the check passes.
    """
    threads = getattr(app.state, "worker_threads", None)
    if threads is None:
        return CheckResult(ok=True, detail="not monitored")
    alive = sum(1 for thread in threads if thread.is_alive())
    total = len(threads)
    # An empty pool (total == 0) can't be "dead"; only a pool whose threads have all exited is.
    ok = alive > 0 or total == 0
    return CheckResult(ok=ok, detail=f"{alive}/{total} alive")


def _run(check: Callable[..., CheckResult], *args: object) -> CheckResult:
    """Run a check, turning any failure into a reported result so a probe never returns 500."""
    try:
        return check(*args)
    except Exception as exc:  # noqa: BLE001 - a health probe must report every failure mode as 503, not crash
        # Report only the exception class, never str(exc): this endpoint is unauthenticated
        # (reachable even before setup), and messages can leak DB file paths, SQL, or
        # connection details to any caller.
        return CheckResult(ok=False, detail=type(exc).__name__)


@router.get("/healthz", include_in_schema=False)
def healthz(request: Request) -> JSONResponse:
    """Health probe: 200 when the DB, migrations, and workers are all healthy, else 503.

    Deliberately a sync endpoint so FastAPI runs it in a threadpool: the checks make blocking
    SQLite queries (with a multi-second busy timeout) and walk the filesystem, which must not
    run on the event loop and stall concurrent requests.
    """
    session_factory = request.app.state.session_factory
    checks = {
        "database": _run(_check_database, session_factory),
        "migrations": _run(_check_migrations, session_factory.kw["bind"]),
        "worker": _run(_check_worker, request.app),
    }
    healthy = all(result.ok for result in checks.values())
    return JSONResponse(
        content={
            "status": "ok" if healthy else "unhealthy",
            "checks": {name: {"ok": r.ok, "detail": r.detail} for name, r in checks.items()},
        },
        status_code=200 if healthy else 503,
    )
