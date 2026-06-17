"""Compile the Tailwind/daisyUI stylesheet for the web UI.

The UI is styled with daisyUI v5 on Tailwind CSS v4. Tailwind scans the Jinja templates
at build time and emits a single stylesheet at `static/app.css`. `cartlog serve` runs this
so the served CSS can never drift from the templates.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_WEB_DIR = Path(__file__).parent
_INPUT = _WEB_DIR / "assets" / "app.css"
_OUTPUT = _WEB_DIR / "static" / "app.css"
# src/cartlog/web -> src/cartlog -> src -> repo root
_PROJECT_ROOT = _WEB_DIR.parents[2]
_TAILWIND_BIN = _PROJECT_ROOT / "node_modules" / ".bin" / "tailwindcss"


class AssetBuildError(RuntimeError):
    """Raised when the CSS build cannot run or fails."""


def _cli_command() -> list[str]:
    """Resolve the Tailwind CLI: prefer the local install, fall back to npx.

    Returns:
        list[str]: The command prefix to invoke the Tailwind CLI.
    """
    if _TAILWIND_BIN.exists():
        return [str(_TAILWIND_BIN)]
    npx = shutil.which("npx")
    if npx:
        return [npx, "@tailwindcss/cli"]
    msg = (
        "Tailwind CLI not found. Install the frontend toolchain first: "
        "run `duty update` or `npm install` in the project root."
    )
    raise AssetBuildError(msg)


def build_css(*, watch: bool = False, minify: bool = True) -> subprocess.Popen[bytes] | None:
    """Compile the daisyUI stylesheet from the Tailwind source.

    Use this before serving so the browser always loads CSS that matches the current
    templates. In watch mode the returned process rebuilds on source/template edits and
    must be terminated by the caller on shutdown.

    Args:
        watch: Run the CLI in --watch mode and return the long-lived process.
        minify: Minify the output. Disabled in dev for readable CSS.

    Returns:
        subprocess.Popen[bytes] | None: The watch process when watch is True, else None.
    """
    if not (_PROJECT_ROOT / "node_modules").exists():
        msg = (
            "node_modules is missing. Install the frontend toolchain first: "
            "run `duty update` or `npm install` in the project root."
        )
        raise AssetBuildError(msg)

    cmd = [*_cli_command(), "-i", str(_INPUT), "-o", str(_OUTPUT)]
    if minify:
        cmd.append("--minify")

    if watch:
        cmd.append("--watch")
        return subprocess.Popen(cmd, cwd=_PROJECT_ROOT)  # noqa: S603

    result = subprocess.run(  # noqa: S603
        cmd, cwd=_PROJECT_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        msg = f"Tailwind build failed (exit {result.returncode}):\n{result.stderr}"
        raise AssetBuildError(msg)
    return None
