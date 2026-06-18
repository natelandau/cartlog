"""Resolve products through saved merge rules and perform product merges."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func

from cartlog.db.merge import merge_into
from cartlog.db.models import LineItem, Product, ProductMerge
from cartlog.exceptions import ProductMergeError
from cartlog.ingest.persistence import _get_or_create
from cartlog.normalization import equivalent_forms, normalize_text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def resolve_product(
    session: Session, canonical_name: str, *, defaults: dict[str, Any] | None = None
) -> Product:
    """Get-or-create a product by name, honoring saved merge rules.

    Use this everywhere a product is created from a name so saved transformations keep
    applying to future items. When a rule matches the normalized name its target product is
    returned unchanged (so `defaults`, e.g. an ingest category guess, are ignored because the
    target already exists). With no matching rule this behaves like a plain get-or-create.

    Args:
        session: SQLAlchemy session; the caller owns commit/rollback.
        canonical_name: The product name from a parser or an edit form.
        defaults: Construction values applied only when a brand-new product is created.
    """
    rule = (
        session.query(ProductMerge)
        .filter_by(source_name_normalized=normalize_text(canonical_name))
        .one_or_none()
    )
    if rule is not None:
        return rule.target_product

    # Collapse singular/plural variants into one product, but only into a product that
    # already exists under an equivalent spelling. This is stateless: equivalence is
    # recomputed every call from inflect's rules plus the current product set, so no rule
    # is persisted (which would otherwise break the ProductMerge delete contract).
    nf = equivalent_forms(canonical_name)
    matches = session.query(Product).filter(func.lower(Product.canonical_name).in_(nf.forms)).all()
    if matches:
        # Deterministic survivor: prefer the product already named in the plural form.
        survivor = next(
            (p for p in matches if normalize_text(p.canonical_name) == nf.plural),
            matches[0],
        )
        # Prefer plural only once a plural spelling has actually been seen; never force it.
        incoming_is_plural = normalize_text(canonical_name) == nf.plural
        if incoming_is_plural and normalize_text(survivor.canonical_name) != nf.plural:
            survivor.canonical_name = canonical_name
        return survivor

    return _get_or_create(session, Product, defaults=defaults, canonical_name=canonical_name)


def merge_products(session: Session, *, source_id: int, target_id: int) -> ProductMerge:
    """Merge the source product into the target and record a persistent transformation rule.

    Reassigns every line item from source to target, repoints any existing rules that targeted
    the source (so chained merges collapse: A->B then B->C leaves A->C), upserts the rule for
    the source's name, and deletes the source product. The caller owns commit/rollback.

    Args:
        session: SQLAlchemy session; the caller commits on success.
        source_id: Id of the product being merged away.
        target_id: Id of the surviving product.

    Returns:
        The created or updated transformation rule.

    Raises:
        ProductMergeError: If merging a product into itself, or either product is missing.
    """
    return merge_into(
        session,
        source_id=source_id,
        target_id=target_id,
        entity_model=Product,
        noun="product",
        error_class=ProductMergeError,
        child_model=LineItem,
        child_fk=LineItem.product_id,
        rule_model=ProductMerge,
        rule_target_fk=ProductMerge.target_product_id,
        rule_key_field="source_name_normalized",
        normalized_key=lambda product: normalize_text(product.canonical_name),
        new_rule=lambda product, target: ProductMerge(
            source_name=product.canonical_name,
            source_name_normalized=normalize_text(product.canonical_name),
            target_product_id=target,
        ),
    )
