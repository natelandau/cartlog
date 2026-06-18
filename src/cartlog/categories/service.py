"""Service owning all taxonomy mutations and lookups (one impl for CLI, web, ingest)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from cartlog.db.models import Category
from cartlog.exceptions import CategoryError
from cartlog.normalization import normalize_text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

UNCATEGORIZED_NAME = "Uncategorized"


class CategoryService:
    """Create, rename, merge, delete, and resolve flat (single-level) taxonomy categories.

    The caller owns the transaction; methods add/flush but do not commit unless documented.
    """

    def __init__(self, session: Session) -> None:
        """Store the session this service mutates and queries."""
        self._session = session
        # Cached {normalized name: Category} for resolve(); built lazily and cleared on every
        # mutation. resolve() runs once per receipt line during ingest, so caching turns an
        # N-line receipt's N full category scans into one.
        self._name_index_cache: dict[str, Category] | None = None

    def _invalidate(self) -> None:
        """Drop the cached name index after a mutation so resolve() rebuilds it."""
        self._name_index_cache = None

    def ensure_uncategorized(self) -> Category:
        """Return the reserved system 'Uncategorized' category, creating it if absent."""
        existing = (
            self._session.query(Category).filter(Category.name == UNCATEGORIZED_NAME).one_or_none()
        )
        if existing is not None:
            return existing
        category = Category(name=UNCATEGORIZED_NAME, is_system=True)
        self._session.add(category)
        self._session.flush()
        self._invalidate()
        return category

    def tree(self) -> list[Category]:
        """Return all categories with products eager-loaded, system rows last, then by name.

        The reserved Uncategorized bucket sorts to the bottom of the management view.
        """
        return (
            self._session.query(Category)
            .options(selectinload(Category.products))
            .order_by(Category.is_system, Category.name)
            .all()
        )

    def _name_index(self) -> dict[str, Category]:
        """Map every category's normalized name to its Category, cached per instance.

        Rebuilt lazily and invalidated by every mutation, so a bulk resolve (one call per
        receipt line during ingest) issues a single category query instead of one per line.
        """
        if self._name_index_cache is None:
            self._name_index_cache = {
                normalize_text(cat.name): cat for cat in self._session.query(Category).all()
            }
        return self._name_index_cache

    def resolve(self, raw: str | None) -> tuple[Category, bool]:
        """Resolve a raw category string to a Category by exact (normalized) name.

        Returns (category, matched). A blank input, or a string that does not match the
        taxonomy, returns the reserved Uncategorized category with matched=False so the
        caller can flag the receipt.

        Args:
            raw: The raw category string from a parser or user input.

        Returns:
            tuple[Category, bool]: The matched category and whether it was a hit.
        """
        norm = normalize_text(raw or "")
        if not norm:
            return self.ensure_uncategorized(), False
        match = self._name_index().get(norm)
        if match is not None:
            return match, True
        return self.ensure_uncategorized(), False

    def allowed_categories(self) -> list[str]:
        """Return sorted names of all non-system categories (for the prompt + pickers).

        Returns:
            list[str]: Sorted category names excluding system rows like Uncategorized.
        """
        names = self._session.query(Category.name).filter(Category.is_system.is_(False)).all()
        return sorted(name for (name,) in names)

    def candidate_pairs(
        self, *, exclude_id: int | None = None, include_system: bool = False
    ) -> list[tuple[int, str]]:
        """Return (id, name) pairs for category pickers/forms, ordered by name.

        Excludes `exclude_id` (e.g. the category being merged/deleted). Excludes system
        categories unless `include_system` is True (a reviewer may reassign to Uncategorized).

        Args:
            exclude_id: Primary key of the category to omit from the result.
            include_system: When True, system categories are included in the result.

        Returns:
            list[tuple[int, str]]: Sorted (id, name) pairs for use in pickers.
        """
        cats = self._session.query(Category).all()
        pairs = [
            (c.id, c.name)
            for c in cats
            if c.id != exclude_id and (include_system or not c.is_system)
        ]
        return sorted(pairs, key=lambda p: p[1].lower())

    def _get(self, category_id: int) -> Category:
        """Fetch a Category by primary key or raise CategoryError if absent.

        Args:
            category_id: The primary key of the category to fetch.

        Returns:
            Category: The resolved Category row.
        """
        category = self._session.get(Category, category_id)
        if category is None:
            msg = f"Category id {category_id} does not exist"
            raise CategoryError(msg)
        return category

    @staticmethod
    def _clean_name(name: str) -> str:
        """Strip and validate a proposed category name, rejecting blanks and slashes."""
        clean = name.strip()
        if not clean:
            msg = "Category name cannot be blank"
            raise CategoryError(msg)
        if "/" in clean:
            msg = "Category name cannot contain '/'"
            raise CategoryError(msg)
        return clean

    def _require_unique_name(self, name: str, *, exclude_id: int | None = None) -> None:
        """Raise CategoryError when another category already uses `name` (case-insensitively).

        Matching is case-insensitive so the create/rename guards agree with resolve(), which
        looks categories up by normalized (lowercased) name. A case-sensitive check would let
        'Dairy' and 'dairy' coexist, and resolve() would then pick between them arbitrarily.
        """
        clash = (
            self._session.query(Category).filter(func.lower(Category.name) == name.lower()).first()
        )
        if clash is not None and clash.id != exclude_id:
            msg = f"Category '{name}' already exists"
            raise CategoryError(msg)

    def create_category(self, *, name: str) -> Category:
        """Create a category, enforcing non-blank, slash-free, unique names.

        Args:
            name: The display name for the new category.

        Returns:
            Category: The newly created and flushed Category row.
        """
        clean = self._clean_name(name)
        self._require_unique_name(clean)
        category = Category(name=clean)
        self._session.add(category)
        self._session.flush()
        self._invalidate()
        return category

    def rename_category(self, category_id: int, *, new_name: str) -> Category:
        """Rename a category, rejecting system rows and duplicate names.

        Args:
            category_id: Primary key of the category to rename.
            new_name: The replacement display name (whitespace is stripped).

        Returns:
            Category: The mutated Category row after flushing.
        """
        category = self._get(category_id)
        if category.is_system:
            msg = "Cannot rename a system category"
            raise CategoryError(msg)
        clean = self._clean_name(new_name)
        self._require_unique_name(clean, exclude_id=category.id)
        category.name = clean
        self._session.flush()
        self._invalidate()
        return category

    def _absorb(self, source: Category, target: Category) -> None:
        """Repoint source's products onto target, then delete source. Caller validates guards."""
        for product in list(source.products):
            product.category = target
        self._session.flush()
        self._session.delete(source)
        self._session.flush()
        self._invalidate()

    def merge_categories(self, *, source_id: int, target_id: int) -> Category:
        """Merge `source` into `target`: repoint its products, then delete source.

        Args:
            source_id: Primary key of the category to remove.
            target_id: Primary key of the category that absorbs the source.

        Returns:
            Category: The target Category after all mutations are flushed.
        """
        if source_id == target_id:
            msg = "Cannot merge a category into itself"
            raise CategoryError(msg)
        source = self._get(source_id)
        target = self._get(target_id)
        if source.is_system:
            msg = "Cannot merge away a system category"
            raise CategoryError(msg)
        if target.is_system:
            msg = "Cannot merge into a system category"
            raise CategoryError(msg)
        self._absorb(source, target)
        return target

    def delete_category(self, category_id: int, *, reassign_to_id: int | None = None) -> None:
        """Delete a category, requiring a reassignment target when it still has products.

        When `reassign_to_id` is provided the operation repoints products before deletion.
        Unlike merge_categories, the reassignment target may be the system Uncategorized bucket.

        Args:
            category_id: Primary key of the category to delete.
            reassign_to_id: Primary key of the category that absorbs any products,
                or None when the category is known to be empty.
        """
        category = self._get(category_id)
        if category.is_system:
            msg = "Cannot delete a system category"
            raise CategoryError(msg)
        if category.products and reassign_to_id is None:
            msg = "Category has products; choose a category to reassign them to"
            raise CategoryError(msg)
        if reassign_to_id is not None:
            target = self._get(reassign_to_id)
            self._absorb(category, target)
            return
        self._session.delete(category)
        self._session.flush()
        self._invalidate()
