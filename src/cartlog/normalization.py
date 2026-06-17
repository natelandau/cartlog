"""Canonical comparison forms so case/spacing variants of a term compare equal.

Every place that dedupes user-entered or parsed text (product names, category names, store
identities) normalizes through here, so the comparison rule lives in exactly one module.
"""

from __future__ import annotations

# ASCII unit separator: a delimiter that cannot occur in chain/location text, so the joined
# store key is unambiguous and a null location collapses to an empty location part.
_SEP = "\x1f"


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
