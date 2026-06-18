"""Tests for canonicalization helpers in cartlog.normalization."""

from __future__ import annotations

import concurrent.futures

import pytest

from cartlog.normalization import equivalent_forms


@pytest.mark.parametrize(
    ("singular", "plural"),
    [
        ("banana", "bananas"),
        ("cheese stick", "cheese sticks"),
        ("berry", "berries"),
        ("loaf", "loaves"),
        ("tomato", "tomatoes"),
        ("potato chip", "potato chips"),
    ],
)
def test_singular_and_plural_share_forms(singular: str, plural: str) -> None:
    """Verify singular and plural spellings share equivalent forms and the same plural anchor."""
    # When both variants are reduced to their equivalent forms
    sing = equivalent_forms(singular)
    plur = equivalent_forms(plural)

    # Then each form set contains both spellings and both agree on the plural anchor
    assert singular in sing.forms and plural in sing.forms
    assert singular in plur.forms and plural in plur.forms
    assert sing.plural == plural
    assert plur.plural == plural


@pytest.mark.parametrize("word", ["milk", "rice", "asparagus", "molasses", "swiss"])
def test_mass_nouns_include_their_own_spelling(word: str) -> None:
    """Verify a mass noun / false-plural's own spelling is present in its forms set so it can still self-match an existing product."""
    # When a non-count or already-singular term is reduced
    forms = equivalent_forms(word)

    # Then the word itself is present (it can still self-match an existing product)
    assert word in forms.forms


def test_equivalent_forms_normalizes_case_and_whitespace() -> None:
    """Verify equivalent forms are normalized by lowercasing and trimming."""
    # When a messy spelling is reduced
    forms = equivalent_forms("  Bananas ")

    # Then the normalized singular and plural are present
    assert "banana" in forms.forms
    assert "bananas" in forms.forms
    assert forms.plural == "bananas"


def test_equivalent_forms_is_thread_safe() -> None:
    """Verify equivalent_forms returns consistent results across concurrent threads."""
    # When the helper is hammered from many threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: equivalent_forms("bananas"), range(200)))

    # Then every call agrees on the plural anchor (no engine state corruption)
    assert all(r.plural == "bananas" for r in results)
    assert all("banana" in r.forms for r in results)
