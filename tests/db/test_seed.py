"""Tests for category seeding from the bundled fixture."""

from __future__ import annotations

from cartlog.categories.service import UNCATEGORIZED_NAME
from cartlog.db.models import Category
from cartlog.db.seed import (
    load_seed_categories,
    seed_categories,
)


def test_bundled_fixture_is_present_and_non_empty() -> None:
    """Verify the default fixture ships with the package so production gets seeded.

    Guards against the fixture being renamed/moved or dropped from the build, which would
    silently leave a production database with no categories.
    """
    # When loading the bundled fixture via its default (packaged) path
    names = load_seed_categories()

    # Then it resolves to a non-empty, slash-free category list
    assert len(names) > 5
    assert all("/" not in name for name in names)


def test_load_seed_categories_dedupes_and_strips(tmp_path) -> None:
    """Verify the loader trims, drops blanks, and removes case-insensitive duplicates."""
    # Given a fixture with padding, a blank, and a casing duplicate
    fixture = tmp_path / "categories.yaml"
    fixture.write_text("- Produce\n- '  produce '\n- ''\n- dairy\n", encoding="utf-8")

    # When loading it
    names = load_seed_categories(fixture)

    # Then duplicates and blanks are removed, first occurrence preserved in order
    assert names == ["Produce", "dairy"]


def test_seed_categories_inserts_missing_and_ensures_uncategorized(session, tmp_path) -> None:
    """Verify seeding adds fixture categories plus the reserved Uncategorized bucket."""
    # Given a small fixture
    fixture = tmp_path / "categories.yaml"
    fixture.write_text("- produce\n- dairy\n", encoding="utf-8")

    # When seeding a fresh database
    added = seed_categories(session, path=fixture)

    # Then both fixture rows are inserted and the system bucket exists
    assert added == 2
    names = {name for (name,) in session.query(Category.name)}
    assert {"produce", "dairy", UNCATEGORIZED_NAME} <= names


def test_seed_categories_is_additive_and_idempotent(session, tmp_path) -> None:
    """Verify re-seeding only adds new entries and never duplicates existing ones."""
    # Given a database already seeded from a one-item fixture
    fixture = tmp_path / "categories.yaml"
    fixture.write_text("- produce\n", encoding="utf-8")
    seed_categories(session, path=fixture)

    # When the fixture grows and seeding runs again
    fixture.write_text("- produce\n- dairy\n", encoding="utf-8")
    added = seed_categories(session, path=fixture)

    # Then only the new category is added and produce is not duplicated
    assert added == 1
    assert session.query(Category).filter_by(name="produce").count() == 1
    assert session.query(Category).filter_by(name="dairy").count() == 1
