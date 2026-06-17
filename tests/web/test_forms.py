"""Unit tests for parsing the receipt edit form."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cartlog.web.forms import parse_review_form

BASE_HEADER = {
    "chain_name": ["Safeway"],
    "location": ["Main St"],
    "purchase_date": ["2026-03-05"],
    "total": ["5.20"],
    "currency": ["USD"],
}


def _form(**line_columns: list[str]) -> dict[str, list[str]]:
    """Build a raw multi-dict form from the shared header plus per-line columns."""
    return {**BASE_HEADER, **line_columns}


def test_parse_review_form_blank_line_id_is_none() -> None:
    """Verify a blank line_id (a newly added row) parses to None rather than failing."""
    # Given a single line row whose hidden line_id is empty, with a category_id
    form = _form(
        line_id=[""],
        raw_description=["NEW ITEM"],
        canonical_name=["bread"],
        category_id=["5"],
        quantity=["1"],
        unit=[""],
        unit_size=[""],
        unit_price=["2.00"],
        line_total=["2.00"],
    )

    # When parsing it
    edit = parse_review_form(form)

    # Then the line is marked as new (line_id is None) and carries its category_id
    assert edit.lines[0].line_id is None
    assert edit.lines[0].category_id == 5
    assert edit.lines[0].line_total == Decimal("2.00")


def test_parse_review_form_category_id_optional_when_absent() -> None:
    """Verify a post with no category_id column still parses, leaving each line uncategorized."""
    # Given a line row with no category_id column at all (absent picker or new row)
    form = _form(
        line_id=["7"],
        raw_description=["EGGS"],
        canonical_name=["eggs"],
        quantity=["1"],
        unit=[""],
        unit_size=[""],
        unit_price=["3.00"],
        line_total=["3.00"],
    )

    # When parsing it
    edit = parse_review_form(form)

    # Then the line parses with a None category_id and the real id is preserved
    assert edit.lines[0].line_id == 7
    assert edit.lines[0].category_id is None


def test_parse_review_form_ragged_core_columns_raises() -> None:
    """Verify mismatched core column lengths are rejected rather than silently truncated."""
    # Given two ids but only one unit_price (a tampered / malformed post)
    form = _form(
        line_id=["1", "2"],
        raw_description=["EGGS", "MILK"],
        canonical_name=["eggs", "milk"],
        category_id=["1", "2"],
        quantity=["1", "1"],
        unit=["", ""],
        unit_size=["", ""],
        unit_price=["3.00"],
        line_total=["3.00", "2.00"],
    )

    # When parsing it, Then a ValueError is raised
    with pytest.raises(ValueError, match="Invalid form input"):
        parse_review_form(form)


def test_parse_review_form_ragged_category_raises() -> None:
    """Verify a category_id column that does not align with the rows is rejected."""
    # Given two line rows but three category_id values (a tampered / malformed post)
    form = _form(
        line_id=["1", "2"],
        raw_description=["EGGS", "MILK"],
        canonical_name=["eggs", "milk"],
        category_id=["1", "2", "3"],
        quantity=["1", "1"],
        unit=["", ""],
        unit_size=["", ""],
        unit_price=["3.00", "2.00"],
        line_total=["3.00", "2.00"],
    )

    # When parsing it, Then a ValueError is raised
    with pytest.raises(ValueError, match="Invalid form input"):
        parse_review_form(form)


def test_parse_review_form_reads_category_id() -> None:
    """Verify the review form parses a per-line category_id integer."""
    # Given a single-line form post carrying a category_id
    form = {
        "chain_name": ["S"],
        "purchase_date": ["2026-01-01"],
        "total": ["1.00"],
        "currency": ["USD"],
        "line_id": [""],
        "raw_description": ["x"],
        "canonical_name": ["x"],
        "category_id": ["3"],
        "quantity": ["1"],
        "unit": [""],
        "unit_size": [""],
        "unit_price": ["1.00"],
        "line_total": ["1.00"],
    }
    # When parsing it
    edit = parse_review_form(form)
    # Then the line carries the integer category_id
    assert edit.lines[0].category_id == 3
