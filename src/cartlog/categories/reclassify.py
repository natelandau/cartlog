"""Re-home products stuck in Uncategorized with a focused LLM pass.

This is the second pass: the extraction pass leaves products in Uncategorized whenever its
category guess did not match the taxonomy. This sweep sends each such product to the focused
classifier. It operates at the Product grain because category lives on Product, so a single
rescue fixes every receipt that references that product.

The LLM tier is capped per product (`max_attempts`): each time the classifier is spent on a
product that stays Uncategorized, a counter increments; once it reaches the cap the product
is no longer sent to the model and is simply left for manual review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cartlog.categories.service import CategoryService
from cartlog.parsing.category_classifier import ProductToClassify

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from cartlog.db.models import Product, Receipt
    from cartlog.parsing.category_classifier import CategoryClassifier

# Default cap on how many times the LLM reclassifier is spent on one stubborn product.
DEFAULT_MAX_RECLASSIFY_ATTEMPTS = 2


@dataclass(frozen=True)
class ReclassifyResult:
    """Summary of a reclassification sweep."""

    considered: int  # products that started on Uncategorized
    rescued_by_llm: int  # re-homed by the focused classifier
    still_uncategorized: int  # left in Uncategorized (declined, or LLM cap already reached)


def _distinct_guesses(product: Product) -> tuple[str, ...]:
    """Return the distinct non-blank original category guesses across a product's line items."""
    seen: dict[str, None] = {}
    for line in product.line_items:
        guess = (line.original_category or "").strip()
        if guess:
            seen.setdefault(guess, None)
    return tuple(seen)


def _representative_description(product: Product) -> str:
    """Return a raw description for the product, falling back to the canonical name."""
    for line in product.line_items:
        if line.raw_description:
            return line.raw_description
    return product.canonical_name


def reclassify_products(
    session: Session,
    products: Sequence[Product],
    classifier: CategoryClassifier | None,
    *,
    max_attempts: int = DEFAULT_MAX_RECLASSIFY_ATTEMPTS,
) -> ReclassifyResult:
    """Re-home the given (assumed Uncategorized) products with the focused classifier.

    Flushes but does not commit; the caller owns the transaction.

    Args:
        session: The session to mutate.
        products: The Uncategorized products to attempt.
        classifier: The focused classifier that places the products. When None, nothing is
            rescued.
        max_attempts: Per-product cap on LLM reclassification attempts. Products at or above
            the cap are not sent to the classifier.

    Returns:
        ReclassifyResult counting what was rescued and how.
    """
    categories = CategoryService(session)

    pending = [
        (
            product,
            ProductToClassify(
                canonical_name=product.canonical_name,
                raw_description=_representative_description(product),
                original_guesses=_distinct_guesses(product),
            ),
        )
        for product in products
    ]

    rescued_by_llm = _run_classifier(categories, classifier, pending, max_attempts=max_attempts)

    session.flush()
    return ReclassifyResult(
        considered=len(products),
        rescued_by_llm=rescued_by_llm,
        still_uncategorized=len(products) - rescued_by_llm,
    )


def reclassify_receipt(
    session: Session,
    receipt: Receipt,
    classifier: CategoryClassifier | None,
    *,
    max_attempts: int = DEFAULT_MAX_RECLASSIFY_ATTEMPTS,
) -> ReclassifyResult:
    """Reclassify only the distinct Uncategorized products referenced by one receipt.

    Used during ingestion so a freshly-parsed receipt's miscategorized lines are re-homed
    immediately, without sweeping the whole database.
    """
    uncategorized = CategoryService(session).ensure_uncategorized()
    distinct: dict[int, Product] = {}
    for line in receipt.line_items:
        if line.product.category_id == uncategorized.id:
            distinct.setdefault(line.product.id, line.product)
    return reclassify_products(
        session, list(distinct.values()), classifier, max_attempts=max_attempts
    )


def unmapped_categories_for(receipt: Receipt, uncategorized_id: int) -> list[str]:
    """Return the distinct non-blank original guesses of lines still Uncategorized.

    Recomputed after a reclassification pass so the UNMAPPED_CATEGORY review reason reflects
    only what genuinely remains uncategorized, not what was just rescued.
    """
    result: list[str] = []
    for line in receipt.line_items:
        if line.product.category_id == uncategorized_id:
            guess = (line.original_category or "").strip()
            if guess and guess not in result:
                result.append(guess)
    return result


def _run_classifier(
    categories: CategoryService,
    classifier: CategoryClassifier | None,
    pending: list[tuple[Product, ProductToClassify]],
    *,
    max_attempts: int,
) -> int:
    """Classify pending products under the budget; assign hits, spend an attempt on misses.

    Products at or above `max_attempts` are skipped (left for manual review). Returns the
    number rescued.
    """
    if classifier is None:
        return 0
    eligible = [(p, item) for p, item in pending if p.reclassify_attempts < max_attempts]
    if not eligible:
        return 0

    answers = classifier.classify([item for _product, item in eligible])
    rescued = 0
    for product, item in eligible:
        chosen = answers.get(item.canonical_name)
        if chosen:
            category, matched = categories.resolve(chosen)
            if matched:
                product.category = category
                rescued += 1
                continue
        # Declined (or an answer that did not resolve): spend one of this product's attempts.
        product.reclassify_attempts += 1
    return rescued
