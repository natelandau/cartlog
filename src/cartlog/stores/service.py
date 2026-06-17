"""Resolve stores through saved merge rules and perform store merges."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cartlog.db.merge import merge_into
from cartlog.db.models import Receipt, Store, StoreMerge
from cartlog.exceptions import StoreMergeError
from cartlog.ingest.persistence import _get_or_create
from cartlog.normalization import normalize_store_identity

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def resolve_store(session: Session, chain_name: str, location: str | None) -> Store:
    """Get-or-create a store by (chain, location), honoring saved merge rules.

    Use this everywhere a store is created from parsed receipt fields so saved
    transformations keep applying to future receipts. When a rule matches the normalized
    identity its target store is returned unchanged; with no matching rule this behaves like
    a plain get-or-create on the (chain_name, location) pair.

    Args:
        session: SQLAlchemy session; the caller owns commit/rollback.
        chain_name: The store/chain name from a parser.
        location: The store location from a parser; may be None.
    """
    rule = (
        session.query(StoreMerge)
        .filter_by(source_identity_normalized=normalize_store_identity(chain_name, location))
        .one_or_none()
    )
    if rule is not None:
        return rule.target_store
    return _get_or_create(session, Store, chain_name=chain_name, location=location)


def merge_stores(session: Session, *, source_id: int, target_id: int) -> StoreMerge:
    """Merge the source store into the target and record a persistent transformation rule.

    Reassigns every receipt from source to target, repoints any existing rules that targeted
    the source (so chained merges collapse: A->B then B->C leaves A->C), upserts the rule for
    the source's identity, and deletes the source store. The caller owns commit/rollback.

    Args:
        session: SQLAlchemy session; the caller commits on success.
        source_id: Id of the store being merged away.
        target_id: Id of the surviving store.

    Returns:
        The created or updated transformation rule.

    Raises:
        StoreMergeError: If merging a store into itself, or either store is missing.
    """
    return merge_into(
        session,
        source_id=source_id,
        target_id=target_id,
        entity_model=Store,
        noun="store",
        error_class=StoreMergeError,
        child_model=Receipt,
        child_fk=Receipt.store_id,
        rule_model=StoreMerge,
        rule_target_fk=StoreMerge.target_store_id,
        rule_key_field="source_identity_normalized",
        normalized_key=lambda store: normalize_store_identity(store.chain_name, store.location),
        new_rule=lambda store, target: StoreMerge(
            source_chain_name=store.chain_name,
            source_location=store.location,
            source_identity_normalized=normalize_store_identity(store.chain_name, store.location),
            target_store_id=target,
        ),
    )
