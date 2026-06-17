"""Tests for category name normalization."""

from __future__ import annotations

import pytest

from cartlog.normalization import normalize_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Dairy & Eggs", "dairy & eggs"),
        ("  produce  ", "produce"),
        ("HERBS & SPICES", "herbs & spices"),
        ("", ""),
    ],
)
def test_normalize_category_lowercases_and_strips(raw: str, expected: str) -> None:
    """Verify normalization lowercases and strips so spelling variants compare equal."""
    assert normalize_text(raw) == expected
