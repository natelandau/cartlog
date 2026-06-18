"""Tests for product merge persistence and the resolve/merge service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cartlog.db.models import LineItem, Product, ProductMerge
from cartlog.exceptions import ProductMergeError
from cartlog.products.service import merge_products, resolve_product
from cartlog.receipts.service import apply_line_item_edit

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_product_merge_round_trips(session) -> None:
    """Verify a ProductMerge row persists and resolves its target product."""
    # Given a target product and a saved rule
    target = Product(canonical_name="coca cola")
    session.add(target)
    session.flush()
    session.add(
        ProductMerge(source_name="Coke", source_name_normalized="coke", target_product_id=target.id)
    )
    session.commit()

    # When the rule is read back
    rule = session.query(ProductMerge).one()

    # Then it points at the target product
    assert rule.target_product.canonical_name == "coca cola"


def test_resolve_product_creates_when_no_rule(session) -> None:
    """Verify resolve_product get-or-creates a product when no rule matches."""
    # When resolving an unseen name
    product = resolve_product(session, "eggs")
    session.flush()

    # Then a single product is created
    assert product.canonical_name == "eggs"
    assert session.query(Product).filter_by(canonical_name="eggs").count() == 1


def test_resolve_product_redirects_on_normalized_match(session) -> None:
    """Verify resolve_product returns the target when a rule matches (case-insensitively)."""
    # Given a target product and a rule for "coke"
    target = Product(canonical_name="coca cola")
    session.add(target)
    session.flush()
    session.add(
        ProductMerge(source_name="Coke", source_name_normalized="coke", target_product_id=target.id)
    )
    session.flush()

    # When resolving a differently-cased/spaced variant of the source term
    resolved = resolve_product(session, "  COKE ")

    # Then the target product is returned and no new product is created
    assert resolved.id == target.id
    assert session.query(Product).filter_by(canonical_name="Coke").count() == 0


def _product(session: Session, name: str) -> Product:
    """Create and flush a bare product for merge tests."""
    p = Product(canonical_name=name)
    session.add(p)
    session.flush()
    return p


def test_merge_products_reassigns_items_and_deletes_source(session) -> None:
    """Verify merge moves line items to the target, deletes the source, and saves a rule."""
    # Given two products, the source carrying a line item
    source = _product(session, "Coke")
    target = _product(session, "coca cola")
    session.add(
        LineItem(
            product=source,
            receipt_id=1,
            raw_description="COKE 12PK",
            quantity=1,
            unit_price=5,
            line_total=5,
        )
    )
    session.flush()

    # When the source is merged into the target
    rule = merge_products(session, source_id=source.id, target_id=target.id)
    session.commit()

    # Then the item now belongs to the target, the source is gone, and a rule exists
    assert session.query(LineItem).filter_by(product_id=target.id).count() == 1
    assert session.get(Product, source.id) is None
    assert rule.source_name == "Coke"
    assert rule.source_name_normalized == "coke"
    assert rule.target_product_id == target.id


def test_merge_products_repoints_chained_rule(session) -> None:
    """Verify merging B into C repoints an existing A->B rule to A->C."""
    # Given A already merged into B, then a new C
    a = _product(session, "A")
    b = _product(session, "B")
    merge_products(session, source_id=a.id, target_id=b.id)
    session.commit()
    c = _product(session, "C")

    # When B is merged into C
    merge_products(session, source_id=b.id, target_id=c.id)
    session.commit()

    # Then the rule for "a" now points at C
    a_rule = session.query(ProductMerge).filter_by(source_name_normalized="a").one()
    assert a_rule.target_product_id == c.id


def test_merge_products_rejects_self_merge(session) -> None:
    """Verify merging a product into itself raises."""
    # Given a product
    p = _product(session, "eggs")

    # When/Then merging it into itself is rejected
    with pytest.raises(ProductMergeError):
        merge_products(session, source_id=p.id, target_id=p.id)


def test_apply_line_item_edit_honors_merge_rule(session: Session) -> None:
    """Verify editing a line to a merged-away name redirects it to the rule's target."""
    # Given a saved rule "coke" -> "coca cola" and a line on some other product
    target = _product(session, "coca cola")
    other = _product(session, "water")
    session.add(
        ProductMerge(source_name="Coke", source_name_normalized="coke", target_product_id=target.id)
    )
    line = LineItem(
        product=other,
        receipt_id=1,
        raw_description="X",
        quantity=1,
        unit_price=1,
        line_total=1,
    )
    session.add(line)
    session.commit()

    # When the line is edited to the merged-away name
    apply_line_item_edit(session, line, canonical_name="Coke", category_id=None)

    # Then it resolves to the target, and no "Coke" product is created
    assert line.product_id == target.id
    assert session.query(Product).filter_by(canonical_name="Coke").count() == 0


def test_resolve_collapses_singular_then_plural(session) -> None:
    """Verify seeing the singular first, then the plural, yields one product named in the plural."""
    # Given the singular was ingested first
    first = resolve_product(session, "banana")
    session.flush()

    # When the plural variant arrives
    second = resolve_product(session, "bananas")
    session.flush()

    # Then it is the same product, now displayed in the plural
    assert second.id == first.id
    assert second.canonical_name == "bananas"
    assert session.query(Product).count() == 1


def test_resolve_collapses_plural_then_singular(session) -> None:
    """Verify seeing the plural first keeps the plural display when the singular arrives."""
    # Given the plural was ingested first
    first = resolve_product(session, "bananas")
    session.flush()

    # When the singular variant arrives
    second = resolve_product(session, "banana")
    session.flush()

    # Then it is the same product and the display stays plural
    assert second.id == first.id
    assert second.canonical_name == "bananas"
    assert session.query(Product).count() == 1


def test_resolve_does_not_force_pluralize_mass_noun(session) -> None:
    """Verify a mass noun seen only in the singular keeps its singular display."""
    # When the same mass noun is resolved twice
    first = resolve_product(session, "milk")
    session.flush()
    second = resolve_product(session, "milk")
    session.flush()

    # Then there is one product and it is never pluralized to "milks"
    assert second.id == first.id
    assert second.canonical_name == "milk"
    assert session.query(Product).count() == 1


def test_resolve_does_not_merge_false_plural(session) -> None:
    """Verify a singular word ending in 's' (e.g. asparagus) is not merged into an erroneous stem."""
    # When asparagus is ingested with no real counterpart present
    product = resolve_product(session, "asparagus")
    session.flush()

    # Then it stands alone under its own name
    assert product.canonical_name == "asparagus"
    assert session.query(Product).count() == 1


def test_manual_merge_rule_still_wins_first(session) -> None:
    """Verify an existing manual ProductMerge rule takes precedence over variant collapsing."""
    # Given a target product and a manual rule mapping "banana" -> that target
    target = Product(canonical_name="fruit")
    session.add(target)
    session.flush()
    session.add(
        ProductMerge(
            source_name="banana", source_name_normalized="banana", target_product_id=target.id
        )
    )
    session.flush()

    # When the ruled source name is resolved
    resolved = resolve_product(session, "banana")

    # Then the manual rule's target wins, not a new/variant product
    assert resolved.id == target.id


def test_resolve_applies_defaults_only_on_create(session) -> None:
    """Verify defaults are applied when creating, ignored when collapsing into an existing product."""
    # Given a singular product created with no category
    first = resolve_product(session, "banana")
    session.flush()

    # When the plural arrives carrying a defaults dict
    second = resolve_product(session, "bananas", defaults={"reclassify_attempts": 5})
    session.flush()

    # Then it collapses into the existing product and ignores defaults
    assert second.id == first.id
    assert second.reclassify_attempts == 0
