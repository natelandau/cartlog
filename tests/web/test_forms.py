"""Unit tests for parsing the receipt edit form."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from cartlog.units import SoldBy
from cartlog.web.forms import LineEdit, parse_review_form

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
        sold_by=["item"],
        measure_unit=[""],
        size_amount=[""],
        size_unit=[""],
        unit_price=["2.00"],
        line_total=["2.00"],
    )

    # When parsing it
    edit = parse_review_form(form)

    # Then the line is marked as new (line_id is None) and carries its category_id
    assert edit.lines[0].line_id is None
    assert edit.lines[0].category_id == 5
    assert edit.lines[0].line_total == Decimal("2.00")


def test_parse_review_form_trims_receipt_text() -> None:
    """Verify the shared required-text validator strips surrounding whitespace on save."""
    # Given a line whose receipt text has leading/trailing whitespace
    form = _form(
        line_id=["7"],
        raw_description=["  EGGS  "],
        canonical_name=["eggs"],
        quantity=["1"],
        sold_by=["item"],
        measure_unit=[""],
        size_amount=[""],
        size_unit=[""],
        unit_price=["3.00"],
        line_total=["3.00"],
    )

    # When parsing it
    edit = parse_review_form(form)

    # Then the stored receipt text is trimmed
    assert edit.lines[0].raw_description == "EGGS"


def test_parse_review_form_blank_receipt_text_rejected() -> None:
    """Verify a blank receipt text on a receipt line is rejected, matching the search editor."""
    # Given a line whose receipt text is whitespace-only
    form = _form(
        line_id=["7"],
        raw_description=["   "],
        canonical_name=["eggs"],
        quantity=["1"],
        sold_by=["item"],
        measure_unit=[""],
        size_amount=[""],
        size_unit=[""],
        unit_price=["3.00"],
        line_total=["3.00"],
    )

    # When parsing it, then it fails with the shared required-text message
    with pytest.raises(ValueError, match="Receipt text is required"):
        parse_review_form(form)


def test_parse_review_form_category_id_optional_when_absent() -> None:
    """Verify a post with no category_id column still parses, leaving each line uncategorized."""
    # Given a line row with no category_id column at all (absent picker or new row)
    form = _form(
        line_id=["7"],
        raw_description=["EGGS"],
        canonical_name=["eggs"],
        quantity=["1"],
        sold_by=["item"],
        measure_unit=[""],
        size_amount=[""],
        size_unit=[""],
        unit_price=["3.00"],
        line_total=["3.00"],
    )

    # When parsing it
    edit = parse_review_form(form)

    # Then the line parses with a None category_id and the real id is preserved
    assert edit.lines[0].line_id == 7
    assert edit.lines[0].category_id is None


@pytest.mark.parametrize(
    "ragged_override",
    [
        # two ids but only one unit_price (a short core column)
        {"unit_price": ["3.00"]},
        # two line rows but three category_id values (a long category column)
        {"category_id": ["1", "2", "3"]},
    ],
)
def test_parse_review_form_ragged_columns_raises(ragged_override) -> None:
    """Verify a column whose length does not align with the rows is rejected, not truncated."""
    # Given an otherwise well-formed two-row post with one ragged column (a tampered post)
    columns = {
        "line_id": ["1", "2"],
        "raw_description": ["EGGS", "MILK"],
        "canonical_name": ["eggs", "milk"],
        "category_id": ["1", "2"],
        "quantity": ["1", "1"],
        "sold_by": ["item", "item"],
        "measure_unit": ["", ""],
        "size_amount": ["", ""],
        "size_unit": ["", ""],
        "unit_price": ["3.00", "2.00"],
        "line_total": ["3.00", "2.00"],
    }
    form = _form(**{**columns, **ragged_override})

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
        "sold_by": ["item"],
        "measure_unit": [""],
        "size_amount": [""],
        "size_unit": [""],
        "unit_price": ["1.00"],
        "line_total": ["1.00"],
    }
    # When parsing it
    edit = parse_review_form(form)
    # Then the line carries the integer category_id
    assert edit.lines[0].category_id == 3


def test_line_edit_measure_mode_requires_unit() -> None:
    """Verify a MEASURE line with no measure_unit fails validation."""
    # When constructing a MEASURE LineEdit without a measure_unit
    with pytest.raises(ValidationError):
        LineEdit(
            line_id=None,
            raw_description="x",
            canonical_name="x",
            category_id=None,
            quantity=Decimal(1),
            sold_by=SoldBy.MEASURE,
            measure_unit=None,
            size_amount=None,
            size_unit=None,
            unit_price=Decimal(1),
            line_total=Decimal(1),
        )


def test_line_edit_item_partial_size_rejected() -> None:
    """Verify an item line with a size amount but no size unit fails validation."""
    # When constructing an ITEM LineEdit with size_amount set but size_unit blank
    with pytest.raises(ValidationError):
        LineEdit(
            line_id=None,
            raw_description="x",
            canonical_name="x",
            category_id=None,
            quantity=Decimal(1),
            sold_by=SoldBy.ITEM,
            measure_unit=None,
            size_amount=Decimal(16),
            size_unit=None,
            unit_price=Decimal(1),
            line_total=Decimal(1),
        )
    # Then the symmetric case (unit without amount) is rejected too
    with pytest.raises(ValidationError):
        LineEdit(
            line_id=None,
            raw_description="x",
            canonical_name="x",
            category_id=None,
            quantity=Decimal(1),
            sold_by=SoldBy.ITEM,
            measure_unit=None,
            size_amount=None,
            size_unit="oz",
            unit_price=Decimal(1),
            line_total=Decimal(1),
        )


def test_parse_review_form_reads_structured_columns() -> None:
    """Verify parse_review_form maps sold_by/size_amount/size_unit into the LineEdit."""
    # Given a form with structured measure fields
    form = {
        "chain_name": ["Store"],
        "location": [""],
        "purchase_date": ["2026-01-01"],
        "total": ["20.60"],
        "currency": ["USD"],
        "line_id": [""],
        "raw_description": ["Beef 16oz"],
        "canonical_name": ["beef"],
        "quantity": ["2"],
        "sold_by": ["item"],
        "measure_unit": [""],
        "size_amount": ["16"],
        "size_unit": ["oz"],
        "unit_price": ["10.30"],
        "line_total": ["20.60"],
    }

    # When parsing it
    edit = parse_review_form(form)
    line = edit.lines[0]

    # Then the structured fields are populated correctly
    assert line.sold_by == SoldBy.ITEM
    assert (line.size_amount, line.size_unit) == (Decimal(16), "oz")
