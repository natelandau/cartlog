"""Tests for the two-pass Uncategorized reclassification sweep."""

from __future__ import annotations

from datetime import date

from cartlog.categories.reclassify import reclassify_products
from cartlog.categories.service import UNCATEGORIZED_NAME, CategoryService
from cartlog.db.models import Product
from cartlog.ingest.persistence import persist_receipt
from cartlog.parsing.schema import ParsedLineItem, ParsedReceipt


class FakeClassifier:
    """A classifier that returns canned answers and records what it was asked to classify."""

    def __init__(self, answers: dict[str, str | None]) -> None:
        """Store the canned canonical_name -> category answers this double will return."""
        self.answers = answers
        self.seen_names: list[str] = []

    def classify(self, products):  # duck-typed test double
        self.seen_names.extend(p.canonical_name for p in products)
        return {p.canonical_name: self.answers.get(p.canonical_name) for p in products}


def _persist_unmapped(session, *, canonical: str, raw: str, guess: str) -> None:
    """Persist a one-line receipt whose category guess does not resolve, landing in Uncategorized."""
    parsed = ParsedReceipt(
        store_name="TestMart",
        store_location=None,
        purchase_date=date(2026, 1, 1),
        currency="USD",
        total=1.0,
        confidence=0.9,
        line_items=[
            ParsedLineItem(
                raw_description=raw,
                canonical_name=canonical,
                category=guess,
                quantity=1,
                unit_price=1.0,
                line_total=1.0,
            )
        ],
    )
    persist_receipt(
        session, parsed, image_path="x.png", source="test", status="parsed", raw_json="{}"
    )
    session.flush()


def _uncategorized_products(session):
    """Return every product currently parked in Uncategorized (the sweep's input set)."""
    uncategorized = CategoryService(session).ensure_uncategorized()
    return session.query(Product).filter(Product.category_id == uncategorized.id).all()


def test_reclassify_rescues_via_classifier(session):
    """Verify an Uncategorized product is re-homed by the focused classifier."""
    # Given an Uncategorized 'produce' product and the 'fruits' category seeded
    CategoryService(session).create_category(name="fruits")
    _persist_unmapped(session, canonical="bananas", raw="BANANAS", guess="produce")
    session.flush()
    classifier = FakeClassifier({"bananas": "fruits"})

    # When running the sweep with the classifier
    result = reclassify_products(session, _uncategorized_products(session), classifier)
    session.commit()

    # Then the classifier placed it into 'fruits' and a success spends no attempt
    bananas = session.query(Product).filter_by(canonical_name="bananas").one()
    assert bananas.category.name == "fruits"
    assert bananas.reclassify_attempts == 0
    assert result.rescued_by_llm == 1
    assert classifier.seen_names == ["bananas"]


def test_reclassify_caps_llm_attempts_then_leaves_for_review(session):
    """Verify a stubborn product is sent to the classifier at most max_attempts times."""
    # Given a product the classifier always declines, with a 'fruits' taxonomy seeded
    CategoryService(session).create_category(name="fruits")
    _persist_unmapped(session, canonical="mystery", raw="???", guess="grocery")
    session.flush()
    classifier = FakeClassifier({"mystery": None})  # always declines

    # When the sweep runs three times with a cap of 2
    for _ in range(3):
        reclassify_products(session, _uncategorized_products(session), classifier, max_attempts=2)
        session.flush()

    # Then the classifier was asked exactly twice and the product is left for manual review
    mystery = session.query(Product).filter_by(canonical_name="mystery").one()
    assert classifier.seen_names == ["mystery", "mystery"]
    assert mystery.reclassify_attempts == 2
    assert mystery.category.name == UNCATEGORIZED_NAME


def test_reclassify_leaves_declined_products_uncategorized(session):
    """Verify a product the classifier declines stays in Uncategorized."""
    # Given a product the classifier will decline
    CategoryService(session).create_category(name="fruits")
    _persist_unmapped(session, canonical="mystery", raw="???", guess="grocery")
    session.flush()
    classifier = FakeClassifier({"mystery": None})

    # When running the sweep
    result = reclassify_products(session, _uncategorized_products(session), classifier)
    session.commit()

    # Then it remains Uncategorized and is counted as such
    mystery = session.query(Product).filter_by(canonical_name="mystery").one()
    assert mystery.category.name == UNCATEGORIZED_NAME
    assert result.rescued_by_llm == 0
    assert result.still_uncategorized == 1
