"""Tests for store identity normalization."""

from __future__ import annotations

from cartlog.normalization import normalize_store_identity


def test_normalize_store_identity_lowercases_and_strips() -> None:
    """Verify case and surrounding whitespace do not affect the identity key."""
    # Given two spelling variants of one store
    # When both are normalized
    a = normalize_store_identity("  Safeway ", " Main St ")
    b = normalize_store_identity("safeway", "main st")

    # Then they produce the same key
    assert a == b


def test_normalize_store_identity_distinguishes_locations() -> None:
    """Verify the same chain at different locations yields different keys."""
    # Given one chain at two locations
    # When each is normalized
    main = normalize_store_identity("Safeway", "Main St")
    airport = normalize_store_identity("Safeway", "Airport Rd")

    # Then the keys differ
    assert main != airport


def test_normalize_store_identity_handles_none_location() -> None:
    """Verify a null location normalizes to a stable empty-location key."""
    # Given a store with no location
    # When normalized with None and with an empty/blank string
    none_key = normalize_store_identity("Depot", None)
    blank_key = normalize_store_identity("Depot", "  ")

    # Then both collapse to the same key
    assert none_key == blank_key
