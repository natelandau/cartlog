"""Tests for CategoryService."""

from __future__ import annotations

import pytest

from cartlog.categories.service import (
    UNCATEGORIZED_NAME,
    CategoryService,
)
from cartlog.db.models import Category, Product
from cartlog.exceptions import CategoryError


@pytest.fixture
def svc(session) -> CategoryService:
    """Return a CategoryService bound to the test session."""
    return CategoryService(session)


def _seed_basic(session) -> None:
    session.add_all(
        [Category(name="dairy & eggs"), Category(name="frozen"), Category(name="produce")]
    )
    session.flush()


def test_resolve_matches_category_by_normalized_name(svc, session) -> None:
    """Verify a raw string resolves to its category by case-insensitive name as a hit."""
    # Given a category in the taxonomy
    svc.create_category(name="spices & seasonings")
    session.flush()

    # When resolving the name in different casing
    category, matched = svc.resolve("Spices & Seasonings")

    # Then it resolves to that category and counts as a match
    assert category.name == "spices & seasonings"
    assert matched is True


def test_resolve_misses_when_no_category_matches(svc) -> None:
    """Verify an unknown string with no matching category goes to Uncategorized."""
    # Given a taxonomy without a 'herbs & spices' category
    # When resolving it
    category, matched = svc.resolve("herbs & spices")

    # Then it falls through to Uncategorized and is not a match
    assert category.name == UNCATEGORIZED_NAME
    assert matched is False


def test_ensure_uncategorized_is_idempotent_and_system(svc, session) -> None:
    """Verify ensure_uncategorized returns the same system row on repeated calls."""
    # Given no existing categories
    # When ensuring the reserved row twice
    a = svc.ensure_uncategorized()
    b = svc.ensure_uncategorized()

    # Then both calls return the same persisted system row
    assert a.id == b.id
    assert a.is_system is True
    assert a.name == UNCATEGORIZED_NAME
    assert session.query(Category).filter_by(name=UNCATEGORIZED_NAME).count() == 1


def test_resolve_exact_name(svc, session) -> None:
    """Verify resolve matches a category by its exact name."""
    # Given a seeded taxonomy
    _seed_basic(session)

    # When resolving an exact name
    cat, matched = svc.resolve("dairy & eggs")

    # Then the category is returned with matched=True
    assert matched is True
    assert cat.name == "dairy & eggs"


def test_resolve_is_case_insensitive(svc, session) -> None:
    """Verify resolve normalizes case and whitespace before matching."""
    # Given a seeded taxonomy
    _seed_basic(session)

    # When resolving a differently-cased, padded name
    cat, matched = svc.resolve("  Frozen  ")

    # Then the category is still matched
    assert matched is True
    assert cat.name == "frozen"


def test_resolve_miss_returns_uncategorized(svc, session) -> None:
    """Verify resolve returns Uncategorized with matched=False for an unknown string."""
    # Given a seeded taxonomy with no matching entries
    _seed_basic(session)

    # When resolving a string that has no match
    cat, matched = svc.resolve("mystery aisle")

    # Then the reserved Uncategorized category is returned
    assert matched is False
    assert cat.name == UNCATEGORIZED_NAME


def test_resolve_blank_returns_uncategorized(svc) -> None:
    """Verify resolve treats an empty string as a miss and returns Uncategorized."""
    # Given no categories seeded (blank input is short-circuited)
    # When resolving an empty string
    cat, matched = svc.resolve("")

    # Then Uncategorized is returned with matched=False
    assert matched is False
    assert cat.name == UNCATEGORIZED_NAME


def test_allowed_categories_excludes_system(svc, session) -> None:
    """Verify allowed_categories lists non-system categories and omits Uncategorized."""
    # Given a seeded taxonomy plus the system Uncategorized row
    _seed_basic(session)
    svc.ensure_uncategorized()

    # When requesting the allowed categories
    names = svc.allowed_categories()

    # Then user-visible names are included and system rows are excluded
    assert "dairy & eggs" in names
    assert "frozen" in names
    assert UNCATEGORIZED_NAME not in names


def test_candidate_pairs_excludes_and_includes_system(svc, session) -> None:
    """Verify candidate_pairs honors exclude_id and the include_system flag."""
    # Given two categories plus the system bucket
    produce = svc.create_category(name="produce")
    svc.create_category(name="frozen")
    uncat = svc.ensure_uncategorized()

    # When excluding one category and omitting system rows
    pairs = svc.candidate_pairs(exclude_id=produce.id)

    # Then the excluded and system rows are absent
    ids = {cid for cid, _name in pairs}
    assert produce.id not in ids
    assert uncat.id not in ids

    # And include_system surfaces the Uncategorized bucket
    with_system = svc.candidate_pairs(include_system=True)
    assert uncat.id in {cid for cid, _name in with_system}


# ---------------------------------------------------------------------------
# create_category
# ---------------------------------------------------------------------------


def test_create_category(svc) -> None:
    """Verify create_category produces a category with the given name."""
    # Given no existing categories
    # When creating a category
    cat = svc.create_category(name="produce")

    # Then the category exists with the requested name
    assert cat.id is not None
    assert cat.name == "produce"


def test_create_duplicate_rejected(svc) -> None:
    """Verify create_category rejects a duplicate name."""
    # Given an existing category named "produce"
    svc.create_category(name="produce")

    # When creating another category with the same name
    # Then a CategoryError mentioning "already exists" is raised
    with pytest.raises(CategoryError, match="already exists"):
        svc.create_category(name="produce")


def test_create_case_insensitive_duplicate_rejected(svc) -> None:
    """Verify a name differing only in case from an existing category is rejected."""
    # Given an existing lowercase category
    svc.create_category(name="dairy")

    # When creating one that differs only in case
    # Then it is rejected so resolve() never has two rows for one normalized name
    with pytest.raises(CategoryError, match="already exists"):
        svc.create_category(name="Dairy")


def test_rename_case_insensitive_duplicate_rejected(svc) -> None:
    """Verify renaming to a case-variant of an existing name is rejected."""
    # Given two categories
    svc.create_category(name="dairy")
    bakery = svc.create_category(name="bakery")

    # When renaming one to a case-variant of the other / Then it is rejected
    with pytest.raises(CategoryError, match="already exists"):
        svc.rename_category(bakery.id, new_name="DAIRY")


def test_create_name_with_slash_rejected(svc) -> None:
    """Verify a category name containing a slash is rejected."""
    # When creating a category whose name contains '/' / Then it is rejected
    with pytest.raises(CategoryError, match="cannot contain"):
        svc.create_category(name="canned/beans")


def test_create_blank_name_rejected(svc) -> None:
    """Verify a blank category name is rejected."""
    # When creating a category with a whitespace-only name / Then it is rejected
    with pytest.raises(CategoryError, match="cannot be blank"):
        svc.create_category(name="   ")


# ---------------------------------------------------------------------------
# rename_category
# ---------------------------------------------------------------------------


def test_rename_updates_name(svc) -> None:
    """Verify rename_category updates the category name."""
    # Given a category
    cat = svc.create_category(name="dairy")

    # When renaming it
    svc.rename_category(cat.id, new_name="dairy & eggs")

    # Then the name is updated
    assert cat.name == "dairy & eggs"


def test_rename_system_rejected(svc) -> None:
    """Verify rename_category rejects renaming a system category."""
    # Given the reserved system Uncategorized category
    uncat = svc.ensure_uncategorized()

    # When attempting to rename it / Then a CategoryError mentioning "system" is raised
    with pytest.raises(CategoryError, match="system"):
        svc.rename_category(uncat.id, new_name="misc")


def test_rename_to_existing_name_rejected(svc) -> None:
    """Verify renaming a category to a name already in use is rejected."""
    # Given two categories
    svc.create_category(name="dairy")
    bakery = svc.create_category(name="bakery")
    # When renaming one to the other's existing name / Then it is rejected cleanly
    with pytest.raises(CategoryError, match="already exists"):
        svc.rename_category(bakery.id, new_name="dairy")


def test_rename_missing_id_rejected(svc) -> None:
    """Verify renaming a nonexistent category id raises a clean CategoryError."""
    # When renaming an id that does not exist / Then a clean CategoryError is raised
    with pytest.raises(CategoryError, match="does not exist"):
        svc.rename_category(9999, new_name="x")


# ---------------------------------------------------------------------------
# merge_categories
# ---------------------------------------------------------------------------


def test_merge_repoints_products_and_deletes_source(svc, session) -> None:
    """Verify merge_categories moves products to the target and deletes the source."""
    # Given two categories, one with a product
    a = svc.create_category(name="bakery")
    b = svc.create_category(name="bread")
    session.add(Product(canonical_name="sourdough", category_id=b.id))
    session.flush()

    # When merging b into a
    svc.merge_categories(source_id=b.id, target_id=a.id)

    # Then the source is deleted and its product belongs to the target
    assert session.get(Category, b.id) is None
    prod = session.query(Product).filter_by(canonical_name="sourdough").one()
    assert prod.category_id == a.id


def test_merge_into_self_rejected(svc) -> None:
    """Verify merge_categories rejects a self-merge."""
    # Given a category
    a = svc.create_category(name="x")

    # When attempting to merge it into itself / Then a CategoryError mentioning "itself" is raised
    with pytest.raises(CategoryError, match="itself"):
        svc.merge_categories(source_id=a.id, target_id=a.id)


def test_merge_away_system_rejected(svc) -> None:
    """Verify merge_categories rejects merging away a system category."""
    # Given a normal category and the system bucket
    normal = svc.create_category(name="produce")
    uncat = svc.ensure_uncategorized()

    # When merging the system bucket into the normal category
    # Then a CategoryError mentioning "system" is raised
    with pytest.raises(CategoryError, match="system"):
        svc.merge_categories(source_id=uncat.id, target_id=normal.id)


def test_merge_into_system_rejected(svc) -> None:
    """Verify merge_categories raises when the target is a system category."""
    # Given a normal category and the system Uncategorized bucket
    normal = svc.create_category(name="produce")
    uncat = svc.ensure_uncategorized()

    # When merging the normal category into the system bucket
    # Then a CategoryError mentioning "system" is raised
    with pytest.raises(CategoryError, match="system"):
        svc.merge_categories(source_id=normal.id, target_id=uncat.id)


# ---------------------------------------------------------------------------
# delete_category
# ---------------------------------------------------------------------------


def test_delete_empty_category(svc, session) -> None:
    """Verify delete_category removes a category that has no products."""
    # Given a category with no products
    a = svc.create_category(name="temp")

    # When deleting it
    svc.delete_category(a.id)

    # Then it no longer exists in the database
    assert session.get(Category, a.id) is None


def test_delete_with_products_requires_target(svc, session) -> None:
    """Verify delete_category raises when a category has products and no reassignment target."""
    # Given a category with a product
    a = svc.create_category(name="snacks")
    session.add(Product(canonical_name="chips", category_id=a.id))
    session.flush()

    # When deleting without a reassignment target
    # Then a CategoryError mentioning "reassign" is raised
    with pytest.raises(CategoryError, match="reassign"):
        svc.delete_category(a.id)


def test_delete_with_products_reassigns(svc, session) -> None:
    """Verify delete_category moves products to the reassignment target."""
    # Given two categories, the first containing a product
    a = svc.create_category(name="snacks")
    b = svc.create_category(name="pantry")
    session.add(Product(canonical_name="chips", category_id=a.id))
    session.flush()

    # When deleting with a reassignment target
    svc.delete_category(a.id, reassign_to_id=b.id)

    # Then the source is deleted and the product belongs to the target
    assert session.get(Category, a.id) is None
    assert session.query(Product).filter_by(canonical_name="chips").one().category_id == b.id


def test_delete_system_rejected(svc) -> None:
    """Verify delete_category rejects deleting a system category."""
    # Given the reserved system Uncategorized category
    uncat = svc.ensure_uncategorized()

    # When attempting to delete it / Then a CategoryError mentioning "system" is raised
    with pytest.raises(CategoryError, match="system"):
        svc.delete_category(uncat.id)


def test_delete_reassign_to_uncategorized_works(svc, session) -> None:
    """Verify delete_category can reassign products to the system Uncategorized bucket."""
    # Given a category with a product and the system Uncategorized bucket
    snacks = svc.create_category(name="snacks")
    uncat = svc.ensure_uncategorized()
    session.add(Product(canonical_name="crisps", category_id=snacks.id))
    session.flush()

    # When deleting the category with Uncategorized as the reassignment target
    svc.delete_category(snacks.id, reassign_to_id=uncat.id)

    # Then the source is deleted and the product belongs to Uncategorized
    assert session.get(Category, snacks.id) is None
    product = session.query(Product).filter_by(canonical_name="crisps").one()
    assert product.category_id == uncat.id
