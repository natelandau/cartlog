"""Tests for the ParsedReceipt and ParsedLineItem schemas."""

from datetime import date

from cartlog.parsing.schema import ParsedLineItem, ParsedReceipt


def test_parsed_line_item_accepts_structured_measure():
    """Verify ParsedLineItem stores measure_value and measure_unit when provided."""
    # Given a line item with explicit package measure fields
    item = ParsedLineItem(
        raw_description="MILK 1.5L",
        canonical_name="milk",
        category="dairy & eggs",
        quantity=1,
        unit="ea",
        unit_size="1.5L",
        measure_value=1.5,
        measure_unit="l",
        unit_price=4.5,
        line_total=4.5,
    )

    # Then the measure fields round-trip correctly
    assert item.measure_value == 1.5
    assert item.measure_unit == "l"


def test_parsed_line_item_measure_defaults_none():
    """Verify measure_value and measure_unit default to None when omitted."""
    # Given a line item without measure fields
    item = ParsedLineItem(
        raw_description="BANANAS",
        canonical_name="bananas",
        category="produce",
        quantity=2,
        unit="lb",
        unit_price=1.74,
        line_total=3.48,
    )

    # Then both measure fields are absent
    assert item.measure_value is None
    assert item.measure_unit is None


def test_parsed_receipt_round_trips_from_json():
    """Verify a ParsedReceipt validates from a JSON-style payload with correct types."""
    payload = {
        "store_name": "Safeway",
        "store_location": "Main St",
        "purchase_date": "2026-03-01",
        "currency": "USD",
        "total": 3.48,
        "confidence": 0.95,
        "line_items": [
            {
                "raw_description": "GV LRG EGGS 12CT",
                "canonical_name": "eggs",
                "category": "dairy/eggs",
                "quantity": 1,
                "unit": None,
                "unit_size": "12CT",
                "unit_price": 3.48,
                "line_total": 3.48,
            }
        ],
    }

    receipt = ParsedReceipt.model_validate(payload)

    assert receipt.store_name == "Safeway"
    assert receipt.purchase_date == date(2026, 3, 1)
    assert receipt.line_items[0] == ParsedLineItem.model_validate(payload["line_items"][0])
    assert receipt.line_items[0].canonical_name == "eggs"
