"""Canonical comparison forms so case/spacing variants of a term compare equal.

Every place that dedupes user-entered or parsed text (product names, category names, store
identities) normalizes through here, so the comparison rule lives in exactly one module.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import inflect

# ASCII unit separator: a delimiter that cannot occur in chain/location text, so the joined
# store key is unambiguous and a null location collapses to an empty location part.
_SEP = "\x1f"

# inflect.engine() caches internal state and is not documented as thread-safe; the ingest
# worker pool runs on threads, so give each thread its own engine instead of locking.
_thread_local = threading.local()

# Common grocery mass nouns that inflect would otherwise treat as countable.
# Registering each as its own plural prevents equivalent_forms from ever producing
# a "plural" form that differs from the base, so resolving e.g. "milks" never
# renames a stored "milk" product.
_GROCERY_MASS_NOUNS: tuple[str, ...] = (
    "flour",
    "milk",
    "rice",
    "sugar",
    "water",
)


def _inflect_engine() -> inflect.engine:
    engine = getattr(_thread_local, "engine", None)
    if engine is None:
        engine = inflect.engine()
        # Treat grocery mass nouns as uncountable (plural == singular).
        for noun in _GROCERY_MASS_NOUNS:
            engine.defnoun(noun, noun)
        _thread_local.engine = engine
    return engine


@dataclass(frozen=True)
class NameForms:
    """The set of normalized spellings a product name is considered equivalent to.

    `forms` holds every spelling that should resolve to the same product (the name itself,
    its singular base, and its plural). `plural` is the spelling we prefer to display.
    """

    forms: frozenset[str]
    plural: str


def equivalent_forms(name: str) -> NameForms:
    """Return the singular/plural spellings `name` should be treated as equivalent to.

    Singularize for matching (never pluralize for storage) so mass nouns like "milk" are
    left alone. `inflect` wrongly singularizes some words that merely end in "s" (e.g.
    "asparagus" -> "asparagu"), but that is harmless: callers only collapse into a product
    that already exists, and no real product is named by the erroneous stem.
    """
    engine = _inflect_engine()
    n = normalize_text(name)
    base = engine.singular_noun(n) or n  # False when already singular -> keep n
    plural = engine.plural_noun(base)
    return NameForms(forms=frozenset({n, base, plural}), plural=plural)


def normalize_text(value: str) -> str:
    """Return the canonical comparison form of a single text value.

    Lowercase and strip surrounding whitespace so " Coke " and "coke", or "Dairy & Eggs" and
    " dairy & eggs ", resolve to the same key.
    """
    return value.strip().lower()


def normalize_store_identity(chain_name: str, location: str | None) -> str:
    """Return the canonical comparison form of a store's (chain, location) identity.

    Lowercase and strip both parts and join them so " Safeway "/"Main St" and
    "safeway"/"main st" match the same store-merge rule. A null or blank location normalizes
    to an empty location part.
    """
    return f"{normalize_text(chain_name)}{_SEP}{normalize_text(location or '')}"
