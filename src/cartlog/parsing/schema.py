"""Pydantic schemas describing the structured output of a parsed receipt."""

from datetime import date

from pydantic import BaseModel, Field


class ParsedLineItem(BaseModel):
    """One line on a receipt, with both the raw text and the normalized product."""

    raw_description: str = Field(description="The line exactly as printed on the receipt.")
    canonical_name: str = Field(description="Normalized product name, e.g. 'eggs'.")
    category: str = Field(description="Product category, e.g. 'dairy/eggs' or 'produce'.")
    # Floats keep the LLM JSON schema simple; persistence converts to Decimal via str().
    quantity: float = Field(default=1.0, description="Quantity purchased.")
    unit: str | None = Field(default=None, description="Unit of measure, e.g. 'lb'.")
    unit_size: str | None = Field(default=None, description="Package size, e.g. '12CT'.")
    measure_value: float | None = Field(
        default=None, description="Numeric size of ONE package's measurable content, e.g. 1.5."
    )
    measure_unit: str | None = Field(
        default=None,
        description="Unit for measure_value, one of the allowed unit tokens (e.g. 'l', 'oz').",
    )
    unit_price: float = Field(description="Price per unit.")
    line_total: float = Field(description="Total price for this line.")


class ParsedReceipt(BaseModel):
    """The full structured result of parsing one receipt image."""

    store_name: str = Field(description="Store or chain name.")
    store_location: str | None = Field(default=None, description="Store location or address.")
    purchase_date: date = Field(description="Date of purchase.")
    currency: str = Field(default="USD", description="ISO currency code.")
    total: float = Field(description="Receipt grand total.")
    confidence: float = Field(description="Parser confidence from 0 to 1.")
    line_items: list[ParsedLineItem] = Field(description="All purchased line items.")
