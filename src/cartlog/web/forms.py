"""Pydantic models for parsing and validating the review/correct form post."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, ValidationError, model_validator

from cartlog.units import SoldBy


def _blank_to_none(value: str | None) -> str | None:
    """Treat an empty/whitespace-only form field as None so optional columns stay null."""
    if value is None:
        return None
    if not isinstance(value, str):
        # Non-string value (e.g. Decimal passed directly from model_copy) passes through.
        return value  # type: ignore[return-value]
    if value.strip() == "":
        return None
    return value


def _blank_int_to_none(value: str | int | None) -> str | int | None:
    """Treat a blank line_id (a newly added, unsaved row) as None so the row is created."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    return value


def clean_required_text(value: str | None) -> str:
    """Strip a required text field and reject it when blank, so the column never goes empty.

    Shared by the receipt review form (via the RequiredText type) and the search inline editor
    so the receipt text is validated identically wherever it is edited.

    Raises:
        ValueError: If the field is missing or whitespace-only.
    """
    cleaned = value.strip() if value is not None else ""
    if not cleaned:
        msg = "Receipt text is required"
        raise ValueError(msg)
    return cleaned


# Optional text columns arrive as empty strings from HTML inputs; coerce those to None.
OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]
# Required text columns (e.g. the receipt text) are trimmed and rejected when blank.
RequiredText = Annotated[str, BeforeValidator(clean_required_text)]
# A new, unsaved row posts an empty line_id; an edited row posts its integer id.
OptionalLineId = Annotated[int | None, BeforeValidator(_blank_int_to_none)]
# A blank/absent category_id means "no change"; a present value is the taxonomy category pk.
OptionalCategoryId = Annotated[int | None, BeforeValidator(_blank_int_to_none)]
# Money/quantity bounds mirror the DB columns (Numeric(10, 2) / Numeric(10, 3)) so an
# out-of-range edit is rejected as a clean form error instead of silently corrupting data.
Money = Annotated[Decimal, Field(max_digits=10, decimal_places=2)]
Quantity = Annotated[Decimal, Field(max_digits=10, decimal_places=3)]
# Decimal | None with blank-to-None coercion; mirrors the Numeric(12,4) size_amount column.
# Use nested Annotated so Field constraints only apply to non-None values.
_ConstrainedDecimal = Annotated[Decimal, Field(max_digits=12, decimal_places=4)]
SizeAmount = Annotated[_ConstrainedDecimal | None, BeforeValidator(_blank_to_none)]


def measure_mode_error(
    *,
    sold_by: SoldBy,
    measure_unit: str | None,
    size_amount: Decimal | None,
    size_unit: str | None,
) -> str | None:
    """Return a message for an inconsistent measure mode, or None when the combination is valid.

    Shared by the receipt review form (LineEdit) and the search inline editor so both reject
    the same impossible combinations: a by-weight/volume line with no unit, an item line
    carrying a measure unit, and an item size given as only an amount or only a unit, which
    `compute_measure` would otherwise silently drop (resolving the line as per-each and
    discarding the entered size).
    """
    if sold_by == SoldBy.MEASURE and not measure_unit:
        return "A by-weight/volume line needs a unit"
    if sold_by == SoldBy.ITEM:
        if measure_unit:
            return "An item line must not set a measure unit"
        if (size_amount is None) != (size_unit is None):
            return "A size needs both an amount and a unit"
    return None


class LineEdit(BaseModel):
    """One edited line item from the review form."""

    line_id: OptionalLineId
    raw_description: RequiredText
    canonical_name: str
    # Optional: written back to the shared Product by taxonomy id, recategorizing every receipt using it.
    category_id: OptionalCategoryId
    quantity: Quantity
    sold_by: SoldBy
    measure_unit: OptionalText
    size_amount: SizeAmount
    size_unit: OptionalText
    unit_price: Money
    line_total: Money

    @model_validator(mode="after")
    def _check_mode(self) -> LineEdit:
        error = measure_mode_error(
            sold_by=self.sold_by,
            measure_unit=self.measure_unit,
            size_amount=self.size_amount,
            size_unit=self.size_unit,
        )
        if error is not None:
            raise ValueError(error)
        return self


class ReceiptEdit(BaseModel):
    """The full review form: receipt header plus its edited lines."""

    chain_name: str
    location: OptionalText
    purchase_date: date
    total: Money
    # Mirror the stores/receipts currency column (String(3)) so an over-length code is rejected.
    currency: Annotated[str, Field(min_length=1, max_length=3)]
    lines: list[LineEdit]


def _check_optional_column_length(
    values: list[str], rows: list[tuple[str, ...]], name: str
) -> None:
    """Raise ValueError when an optional column is present but does not align with rows."""
    if values and len(values) != len(rows):
        msg = f"{name} column has {len(values)} values for {len(rows)} rows"
        raise ValueError(msg)


def parse_review_form(form: dict[str, list[str]]) -> ReceiptEdit:
    """Build a ReceiptEdit from raw multi-valued form data.

    The line-item fields are repeated once per row, so they are zipped back together by
    position. core_keys defines the strictly-zipped columns; category is an optional
    index-aligned column kept separate so a post without it still parses (lines default to
    None category). Surfaced as a single ValueError so the route can re-render the form
    with a friendly message instead of leaking a stack trace.

    Args:
        form: Mapping of field name to its list of submitted values (multi-dict items).

    Returns:
        A validated ReceiptEdit.

    Raises:
        ValueError: If the core line columns are ragged or a Decimal/date/length field is
            invalid (all surfaced as a single ValueError so the route can re-render).
    """
    core_keys = (
        "line_id",
        "raw_description",
        "canonical_name",
        "quantity",
        "sold_by",
        "measure_unit",
        "size_amount",
        "size_unit",
        "unit_price",
        "line_total",
    )
    try:
        columns = [form.get(key, []) for key in core_keys]
        # category_id is an optional column kept out of the strict core zip so an absent
        # column does not zero out the line set; when present it must align 1:1 with the
        # rows, the same ragged-data guarantee the core columns get via strict=True.
        category_ids = form.get("category_id", [])
        rows = list(zip(*columns, strict=True))
        _check_optional_column_length(category_ids, rows, "category_id")
        lines: list[dict[str, str | None]] = []
        for index, row in enumerate(rows):
            line = dict(zip(core_keys, row, strict=True))
            line["category_id"] = category_ids[index] if category_ids else None
            lines.append(line)
        payload = {
            "chain_name": _one(form, "chain_name"),
            "location": _one(form, "location"),
            "purchase_date": _one(form, "purchase_date"),
            "total": _one(form, "total"),
            "currency": _one(form, "currency"),
            "lines": lines,
        }
        return ReceiptEdit.model_validate(payload)
    except (InvalidOperation, ValueError, ValidationError) as exc:
        msg = f"Invalid form input: {exc}"
        raise ValueError(msg) from exc


def _one(form: dict[str, list[str]], key: str) -> str:
    """Return the single value for a header field, or empty string if absent."""
    values = form.get(key, [])
    return values[0] if values else ""
