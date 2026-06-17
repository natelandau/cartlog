"""Generic entity-merge mechanics shared by product and store merges.

Product and store merges follow the same shape: reassign the source entity's child rows to
the target, collapse any rules that pointed at the source, upsert a persistent rule keyed on
the source's normalized identity, then delete the source. `merge_into` factors that shape out
so each service differs only in its models and how a source maps to a rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import InstrumentedAttribute, Session

    from cartlog.db.base import Base


def merge_into[EntityT: Base, RuleT: Base](  # noqa: PLR0913 - models + rule mapping per entity
    session: Session,
    *,
    source_id: int,
    target_id: int,
    entity_model: type[EntityT],
    noun: str,
    error_class: type[Exception],
    child_model: type[Base],
    child_fk: InstrumentedAttribute,
    rule_model: type[RuleT],
    rule_target_fk: InstrumentedAttribute,
    rule_key_field: str,
    normalized_key: Callable[[EntityT], str],
    new_rule: Callable[[EntityT, int], RuleT],
) -> RuleT:
    """Merge the source entity into the target and upsert a persistent transformation rule.

    Reassigns every child row (via `child_fk`) from source to target, repoints any existing
    rules that targeted the source so chained merges collapse (A->B then B->C leaves A->C),
    upserts the rule keyed on the source's normalized identity, and deletes the source. The
    caller owns commit/rollback.

    Args:
        session: SQLAlchemy session; the caller commits on success.
        source_id: Id of the entity being merged away.
        target_id: Id of the surviving entity.
        entity_model: The mapped entity class (e.g. Product, Store).
        noun: Singular entity noun used in error messages (e.g. "product").
        error_class: Exception type raised for an invalid merge.
        child_model: The child table to reassign (e.g. LineItem, Receipt).
        child_fk: The child column referencing the entity (e.g. LineItem.product_id).
        rule_model: The transformation-rule class (e.g. ProductMerge).
        rule_target_fk: The rule column pointing at the surviving entity
            (e.g. ProductMerge.target_product_id).
        rule_key_field: Name of the rule's unique normalized-key column.
        normalized_key: Maps the source entity to its normalized rule key.
        new_rule: Builds a fresh rule from the source entity and the target id.

    Raises:
        error_class: If merging an entity into itself, or either entity is missing.
    """
    if source_id == target_id:
        msg = f"Cannot merge a {noun} into itself."
        raise error_class(msg)
    source = session.get(entity_model, source_id)
    target = session.get(entity_model, target_id)
    if source is None or target is None:
        msg = f"Both {noun}s must exist to merge."
        raise error_class(msg)

    # Move children, then collapse any rule that pointed at the now-removed source.
    session.query(child_model).filter(child_fk == source_id).update({child_fk: target_id})
    session.query(rule_model).filter(rule_target_fk == source_id).update(
        {rule_target_fk: target_id}
    )

    # The rule may have just been repointed in-memory by the bulk update above (if it targeted
    # the source); reassigning the target below is idempotent and always lands correct.
    rule = (
        session.query(rule_model)
        .filter_by(**{rule_key_field: normalized_key(source)})
        .one_or_none()
    )
    if rule is None:
        rule = new_rule(source, target_id)
        session.add(rule)
    else:
        setattr(rule, rule_target_fk.key, target_id)

    session.delete(source)
    return rule
