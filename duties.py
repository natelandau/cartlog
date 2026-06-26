"""Duty tasks for the project."""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from duty import duty, tools

if TYPE_CHECKING:
    from duty.context import Context


PYPROJECT = Path("pyproject.toml")
PY_SRC_PATHS = (Path(_) for _ in ("src/", "tests/", "duties.py", "scripts/") if Path(_).exists())
PY_SRC_LIST = tuple(str(_) for _ in PY_SRC_PATHS)
CI = os.environ.get("CI", "0") in {"1", "true", "yes", ""}
PROJECT_ROOT = Path(__file__).parent
VERSION = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a string.

    Args:
        text (str): String to remove ANSI escape sequences from.

    Returns:
        str: String without ANSI escape sequences.
    """
    ansi_chars = re.compile(r"(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]")

    # Replace [ with \[ so rich doesn't interpret output as style tags
    return ansi_chars.sub("", text).replace("[", r"\[")


def pyprefix(title: str) -> str:
    """Add a prefix to the title if CI is true.

    Returns:
        str: Title with prefix if CI is true.
    """
    if CI:
        prefix = f"(python{sys.version_info.major}.{sys.version_info.minor})"
        return f"{prefix:14}{title}"
    return title


@duty(silent=True)
def clean(ctx: Context) -> None:
    """Clean the project."""
    ctx.run("rm -rf .coverage*")
    ctx.run("rm -rf .cache")
    ctx.run("rm -rf build")
    ctx.run("rm -rf dist")
    ctx.run("rm -rf pip-wheel-metadata")
    ctx.run("find . -type d -name __pycache__ | xargs rm -rf")
    ctx.run("find . -name '.DS_Store' -delete")


@duty
def ruff(ctx: Context) -> None:
    """Check the code quality with ruff."""
    ctx.run(
        tools.ruff.check(*PY_SRC_LIST, fix=False, config="pyproject.toml"),
        title=pyprefix("code quality check"),
        command=f"ruff check --config pyproject.toml --no-fix {' '.join(PY_SRC_LIST)}",
    )


@duty
def format(ctx: Context) -> None:  # noqa: A001
    """Format the code with ruff."""
    ctx.run(
        tools.ruff.format(*PY_SRC_LIST, check=True, config="pyproject.toml"),
        title=pyprefix("code formatting"),
        command=f"ruff format --config pyproject.toml {' '.join(PY_SRC_LIST)}",
    )


@duty
def ty(ctx: Context) -> None:
    """Check the code with ty."""
    ctx.run(
        ["ty", "check", *PY_SRC_LIST],
        title="ty check",
    )


@duty
def typos(ctx: Context) -> None:
    """Check the code with typos."""
    ctx.run(
        ["typos", "--config", ".typos.toml"],
        title=pyprefix("typos check"),
        command="typos --config .typos.toml",
    )


@duty(skip_if=CI, skip_reason="skip prek in CI environments")
def prek(ctx: Context) -> None:
    """Run prek hooks."""
    ctx.run(
        "PREK_SKIP=ty,pytest,ruff-check prek run --all-files",
        title=pyprefix("prek hooks"),
    )


@duty(pre=[ruff, format, ty, typos, prek], capture=CI)
def lint(ctx: Context) -> None:
    """Run all linting duties."""


@duty(capture=CI)
def update(ctx: Context) -> None:
    """Update the project."""
    ctx.run(["uv", "lock", "--upgrade"], title="update uv lock")
    ctx.run(["uv", "sync"], title="update uv sync")
    ctx.run(["prek", "autoupdate"], title="prek autoupdate")
    ctx.run(["uvx", "uv-upx", "upgrade", "run"], title="uv-upx upgrade")
    ctx.run(["npm", "install"], title="install npm packages")
    ctx.run(["npm", "update", "--save"], title="update npm packages")


@duty
def dev(ctx: Context, host: str = "127.0.0.1", port: str = "8000") -> None:
    """Serve the web app in development mode (templates reload on change).

    A thin wrapper around `cartlog serve --dev` so this dev entrypoint can never drift from
    the package's own serve logic: bootstrap, parser, classifier, and the in-process worker
    pool all live in `cartlog serve`.
    """
    ctx.run(
        ["uv", "run", "cartlog", "serve", "--dev", "--host", host, "--port", port],
        title=f"serving cartlog (dev) at http://{host}:{port}",
        capture=False,
    )


@duty(capture=CI)
def build(ctx: Context) -> None:
    """Compile the daisyUI stylesheet (one-shot, minified)."""
    ctx.run(["npm", "run", "build:css"], title="build daisyUI stylesheet")


@duty()
def test(ctx: Context, *cli_args: str) -> None:
    """Test package and generate coverage reports."""
    ctx.run(
        tools.pytest(
            "tests/",
            config_file="pyproject.toml",
            color="yes",
        ).add_args(
            "--cov",
            "--cov-config=pyproject.toml",
            "--cov-report=xml",
            "--cov-report=term",
            *cli_args,
        ),
        title=pyprefix("Running tests - this may take a while"),
        capture=CI,
    )


@duty()
def e2e(ctx: Context, *cli_args: str) -> None:
    """Run the Playwright browser end-to-end tests (installs the browser first).

    The `e2e` marker is deselected by default, so these never run as part of `duty test`,
    `duty lint`, or the pre-commit hooks. The `live_server` fixture builds the CSS bundle.
    """
    ctx.run(["uv", "run", "playwright", "install", "chromium"], title="install chromium")
    ctx.run(
        # -n0 forces serial execution: the addopts default is -n auto, but the e2e tests share a
        # session-scoped live server, so parallel workers would each spin up a redundant server.
        tools.pytest("tests/e2e", config_file="pyproject.toml", color="yes").add_args(
            "-m", "e2e", "-n0", *cli_args
        ),
        title=pyprefix("Running browser e2e tests"),
        capture=CI,
    )
