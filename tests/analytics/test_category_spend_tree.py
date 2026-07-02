"""Tests for the category-spend drill-down hierarchy (category → product treemap)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from cartlog.analytics.service import AnalyticsService
from cartlog.db.models import Category, Product, ReceiptStatus, Store
from tests.factories import make_line, make_receipt

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cartlog.analytics.results import SpendTreeNode


def _root(nodes: list[SpendTreeNode]) -> SpendTreeNode:
    """Return the single root node (the one with no parent)."""
    roots = [n for n in nodes if n.parent_id == ""]
    assert len(roots) == 1, f"expected exactly one root, got {[r.label for r in roots]}"
    return roots[0]


def _children(nodes: list[SpendTreeNode], parent: SpendTreeNode) -> list[SpendTreeNode]:
    """Return the direct children of `parent`, in payload order."""
    return [n for n in nodes if n.parent_id == parent.id]


def _child(nodes: list[SpendTreeNode], parent: SpendTreeNode, label: str) -> SpendTreeNode:
    """Return the one direct child of `parent` with the given label."""
    matches = [n for n in _children(nodes, parent) if n.label == label]
    assert len(matches) == 1, f"expected one {label!r} under {parent.label!r}, got {len(matches)}"
    return matches[0]


def _tree_session(session: Session) -> Session:
    """Seed one store with two categories, each holding several products, then return the session.

    produce: bananas 50, apples 30, carrots 10, lettuce 5, kale 3  (98 total, 5 products)
    dairy:   milk 40, eggs 20                                       (60 total, 2 products)
    """
    store = Store(chain_name="Mart", location=None)
    produce = Category(name="produce")
    dairy = Category(name="dairy")
    lines = [
        make_line(
            Product(canonical_name="bananas", category=produce),
            raw="BANANAS",
            qty="1",
            unit_price="50",
            line_total="50",
        ),
        make_line(
            Product(canonical_name="apples", category=produce),
            raw="APPLES",
            qty="1",
            unit_price="30",
            line_total="30",
        ),
        make_line(
            Product(canonical_name="carrots", category=produce),
            raw="CARROTS",
            qty="1",
            unit_price="10",
            line_total="10",
        ),
        make_line(
            Product(canonical_name="lettuce", category=produce),
            raw="LETTUCE",
            qty="1",
            unit_price="5",
            line_total="5",
        ),
        make_line(
            Product(canonical_name="kale", category=produce),
            raw="KALE",
            qty="1",
            unit_price="3",
            line_total="3",
        ),
        make_line(
            Product(canonical_name="milk", category=dairy),
            raw="MILK",
            qty="1",
            unit_price="40",
            line_total="40",
        ),
        make_line(
            Product(canonical_name="eggs", category=dairy),
            raw="EGGS",
            qty="1",
            unit_price="20",
            line_total="20",
        ),
    ]
    receipt = make_receipt(store, date(2026, 1, 15), ReceiptStatus.PARSED, lines)
    session.add_all([store, produce, dairy, receipt])
    session.commit()
    return session


def test_category_spend_tree_builds_category_and_product_nodes(session: Session):
    """Verify the tree roots categories under a root and products under their category."""
    # Given a store with two categories of products
    _tree_session(session)

    # When building the tree with folding disabled (no Other budget)
    tree = AnalyticsService(session).category_spend_tree(max_other_share=0.0)

    # Then a single root holds the two categories, highest-spend first
    root = _root(tree.nodes)
    assert root.label == "All categories"
    assert [c.label for c in _children(tree.nodes, root)] == ["produce", "dairy"]

    # And each category's spend is the sum of its products
    produce = _child(tree.nodes, root, "produce")
    assert produce.total_spend == Decimal(98)
    assert [p.label for p in _children(tree.nodes, produce)] == [
        "bananas",
        "apples",
        "carrots",
        "lettuce",
        "kale",
    ]
    assert _child(tree.nodes, produce, "bananas").total_spend == Decimal(50)

    # And the payload total describes the whole selection
    assert tree.total_spend == Decimal(158)


def test_category_spend_tree_folds_product_tail_into_other(session: Session):
    """Verify a category's small product tail folds into one muted Other leaf."""
    # Given the produce category has five products
    _tree_session(session)

    # When capping each category to two product tiles with a 10% Other budget
    tree = AnalyticsService(session).category_spend_tree(
        products_per_category=3, max_other_share=0.10
    )

    # Then produce shows its top two products plus a folded Other leaf
    root = _root(tree.nodes)
    produce = _child(tree.nodes, root, "produce")
    children = _children(tree.nodes, produce)
    assert [c.label for c in children] == ["bananas", "apples", "Other"]

    # And the Other leaf sums the folded tail (carrots + lettuce + kale) and is marked muted
    other = children[-1]
    assert other.is_other is True
    assert other.total_spend == Decimal(18)  # 10 + 5 + 3
    assert other.line_item_count == 3

    # And dairy (only two products) is not folded
    dairy = _child(tree.nodes, root, "dairy")
    assert [c.label for c in _children(tree.nodes, dairy)] == ["milk", "eggs"]


def test_category_spend_tree_drops_non_positive_products_and_categories(session: Session):
    """Verify refund/coupon lines that net a product or category non-positive are excluded."""
    # Given produce with a spoiled (negative) product line and a wholly-negative misc category
    store = Store(chain_name="Mart", location=None)
    produce = Category(name="produce")
    misc = Category(name="misc")
    lines = [
        make_line(
            Product(canonical_name="bananas", category=produce),
            raw="BANANAS",
            qty="1",
            unit_price="50",
            line_total="50",
        ),
        make_line(
            Product(canonical_name="apples", category=produce),
            raw="APPLES",
            qty="1",
            unit_price="30",
            line_total="30",
        ),
        make_line(
            Product(canonical_name="spoiled", category=produce),
            raw="SPOILED CREDIT",
            qty="1",
            unit_price="-5",
            line_total="-5",
        ),
        make_line(
            Product(canonical_name="refund", category=misc),
            raw="REFUND",
            qty="1",
            unit_price="-8",
            line_total="-8",
        ),
    ]
    session.add_all(
        [store, produce, misc, make_receipt(store, date(2026, 2, 1), ReceiptStatus.PARSED, lines)]
    )
    session.commit()

    # When building the tree
    tree = AnalyticsService(session).category_spend_tree(max_other_share=0.0)
    labels = {n.label for n in tree.nodes}

    # Then the negative product and the all-negative category never appear
    assert "spoiled" not in labels
    assert "misc" not in labels
    assert "refund" not in labels

    # And produce's total reflects only its positive products
    root = _root(tree.nodes)
    produce_node = _child(tree.nodes, root, "produce")
    assert produce_node.total_spend == Decimal(80)  # 50 + 30
    assert tree.total_spend == Decimal(80)


def test_category_spend_tree_applies_store_filter(session: Session):
    """Verify a store filter scopes the tree to only that store's categories."""
    # Given produce sold at Mart and dairy sold at Depot
    mart = Store(chain_name="Mart", location=None)
    depot = Store(chain_name="Depot", location=None)
    produce = Category(name="produce")
    dairy = Category(name="dairy")
    session.add_all(
        [
            mart,
            depot,
            produce,
            dairy,
            make_receipt(
                mart,
                date(2026, 1, 5),
                ReceiptStatus.PARSED,
                [
                    make_line(
                        Product(canonical_name="bananas", category=produce),
                        raw="B",
                        qty="1",
                        unit_price="50",
                        line_total="50",
                    ),
                ],
            ),
            make_receipt(
                depot,
                date(2026, 1, 6),
                ReceiptStatus.PARSED,
                [
                    make_line(
                        Product(canonical_name="milk", category=dairy),
                        raw="M",
                        qty="1",
                        unit_price="40",
                        line_total="40",
                    ),
                ],
            ),
        ]
    )
    session.commit()
    service = AnalyticsService(session)

    # When filtering to the Mart store
    tree = service.category_spend_tree(store_id=mart.id)

    # Then only produce (the category sold at Mart) is in the map
    root = _root(tree.nodes)
    assert [c.label for c in _children(tree.nodes, root)] == ["produce"]
