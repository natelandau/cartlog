"""Tests for store merge persistence and the resolve/merge service."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cartlog.db.models import Receipt, Store, StoreMerge
from cartlog.exceptions import StoreMergeError
from cartlog.ingest.persistence import persist_receipt
from cartlog.stores.service import merge_stores, resolve_store

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_store_merge_round_trips(session: Session) -> None:
    """Verify a StoreMerge row persists and resolves its target store."""
    # Given a target store and a saved rule
    target = Store(chain_name="Safeway", location="Main St")
    session.add(target)
    session.flush()
    session.add(
        StoreMerge(
            source_chain_name="Safeway",
            source_location="Main Street",
            source_identity_normalized="safeway\x1fmain street",
            target_store_id=target.id,
        )
    )
    session.commit()

    # When the rule is read back
    rule = session.query(StoreMerge).one()

    # Then it points at the target store
    assert rule.target_store.chain_name == "Safeway"
    assert rule.target_store.location == "Main St"


def _store(session: Session, chain: str, location: str | None) -> Store:
    """Create and flush a bare store for merge tests."""
    s = Store(chain_name=chain, location=location)
    session.add(s)
    session.flush()
    return s


def test_resolve_store_creates_when_no_rule(session: Session) -> None:
    """Verify resolve_store get-or-creates a store when no rule matches."""
    # When resolving an unseen store
    store = resolve_store(session, "Safeway", "Main St")
    session.flush()

    # Then a single store is created
    assert store.chain_name == "Safeway"
    assert session.query(Store).filter_by(chain_name="Safeway").count() == 1


def test_resolve_store_redirects_on_normalized_match(session: Session) -> None:
    """Verify resolve_store returns the target when a rule matches a (chain, location) variant."""
    # Given a target store and a rule for a spelling variant
    target = _store(session, "Safeway", "Main St")
    session.add(
        StoreMerge(
            source_chain_name="Safeway",
            source_location="Main Street",
            source_identity_normalized="safeway\x1fmain street",
            target_store_id=target.id,
        )
    )
    session.flush()

    # When resolving the variant
    resolved = resolve_store(session, " SAFEWAY ", "main street")

    # Then the target is returned and no new store is created
    assert resolved.id == target.id
    assert session.query(Store).filter_by(location="Main Street").count() == 0


def test_merge_stores_reassigns_receipts_and_deletes_source(session: Session) -> None:
    """Verify merge moves receipts to the target, deletes the source, and saves a rule."""
    # Given two stores, the source carrying a receipt
    source = _store(session, "Safeway", "Main Street")
    target = _store(session, "Safeway", "Main St")
    session.add(
        Receipt(
            store=source,
            purchase_date=date(2026, 1, 1),
            total=Decimal("1.00"),
            currency="USD",
            image_path="/tmp/x.png",  # noqa: S108
            raw_parser_json="{}",
            source="cli",
            status="parsed",
        )
    )
    session.flush()

    # When the source is merged into the target
    rule = merge_stores(session, source_id=source.id, target_id=target.id)
    session.commit()

    # Then the receipt now belongs to the target, the source is gone, and a rule exists
    assert session.query(Receipt).filter_by(store_id=target.id).count() == 1
    assert session.get(Store, source.id) is None
    assert rule.source_chain_name == "Safeway"
    assert rule.source_identity_normalized == "safeway\x1fmain street"
    assert rule.target_store_id == target.id


def test_merge_stores_repoints_chained_rule(session: Session) -> None:
    """Verify merging B into C repoints an existing A->B rule to A->C."""
    # Given A already merged into B, then a new C
    a = _store(session, "A", "x")
    b = _store(session, "B", "y")
    merge_stores(session, source_id=a.id, target_id=b.id)
    session.commit()
    c = _store(session, "C", "z")

    # When B is merged into C
    merge_stores(session, source_id=b.id, target_id=c.id)
    session.commit()

    # Then the rule for A now points at C
    a_rule = session.query(StoreMerge).filter_by(source_identity_normalized="a\x1fx").one()
    assert a_rule.target_store_id == c.id


def test_merge_stores_rejects_self_merge(session: Session) -> None:
    """Verify merging a store into itself raises."""
    # Given a store
    s = _store(session, "Safeway", "Main St")

    # When/Then merging it into itself is rejected
    with pytest.raises(StoreMergeError):
        merge_stores(session, source_id=s.id, target_id=s.id)


def test_persist_receipt_honors_store_merge_rule(session: Session, sample_parsed_receipt) -> None:
    """Verify a future ingest with a merged-away identity lands on the target store."""
    # Given a target store and a rule redirecting the sample receipt's store to it
    target = _store(session, "Safeway Superstore", "Main St")
    session.add(
        StoreMerge(
            source_chain_name="Safeway",
            source_location="Main St",
            source_identity_normalized="safeway\x1fmain st",
            target_store_id=target.id,
        )
    )
    session.flush()

    # When a receipt parsed as "Safeway"/"Main St" is persisted
    receipt, _ = persist_receipt(
        session,
        sample_parsed_receipt,
        image_path="/tmp/x.png",  # noqa: S108
        source="cli",
        status="parsed",
        raw_json="{}",
    )
    session.flush()

    # Then it attaches to the target store and creates no duplicate "Safeway" store
    assert receipt.store_id == target.id
    assert session.query(Store).filter_by(chain_name="Safeway").count() == 0
