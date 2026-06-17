"""Exception hierarchy for cartlog.

All domain errors descend from `CartlogError`, so callers can catch the whole family with one
clause. Merge failures share `EntityMergeError`; the per-entity subclasses let a caller (e.g. the
admin router) distinguish a product merge failure from a store one.
"""

from __future__ import annotations


class CartlogError(Exception):
    """Base class for every cartlog domain error."""


class EntityMergeError(CartlogError):
    """A requested entity merge is invalid (self-merge or a missing entity)."""


class ProductMergeError(EntityMergeError):
    """A product merge is invalid (self-merge or missing product)."""


class StoreMergeError(EntityMergeError):
    """A store merge is invalid (self-merge or missing store)."""


# Keeps ValueError in the bases so existing `except ValueError` callers stay correct.
class CategoryError(CartlogError, ValueError):
    """A taxonomy operation was rejected (e.g. blank name, duplicate, or system-row guard)."""
