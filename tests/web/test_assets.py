"""Tests for the Tailwind/daisyUI CSS build helper."""

from __future__ import annotations

import shutil

import pytest

from cartlog.web import assets


def test_build_css_raises_when_node_modules_missing(monkeypatch, tmp_path) -> None:
    """Verify a clear error is raised when the npm toolchain is not installed."""
    # Point the helper's project root at an empty dir so node_modules is absent.
    monkeypatch.setattr(assets, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(assets, "_TAILWIND_BIN", tmp_path / "node_modules" / ".bin" / "tailwindcss")

    with pytest.raises(assets.AssetBuildError, match="node_modules"):
        assets.build_css(watch=False)


@pytest.mark.skipif(
    not (assets._PROJECT_ROOT / "node_modules").exists(),
    reason="npm toolchain not installed (run `npm install`)",
)
def test_build_css_compiles_stylesheet() -> None:
    """Verify a real build produces a stylesheet containing the theme names."""
    assets.build_css(watch=False, minify=False)
    output = assets._OUTPUT.read_text(encoding="utf-8")
    assert "cartlog-light" in output
    assert "cartlog-dark" in output


def test_module_uses_npx_fallback_when_bin_absent(monkeypatch, tmp_path) -> None:
    """Verify the CLI command falls back to npx when the local bin is missing."""
    monkeypatch.setattr(assets, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(assets, "_TAILWIND_BIN", tmp_path / "missing")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")
    assert assets._cli_command() == ["/usr/bin/npx", "@tailwindcss/cli"]
