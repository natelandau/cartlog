"""Tests for the ParsedReceipt and ParsedLineItem schemas."""

from datetime import date

from cartlog.parsing.schema import ParsedLineItem, ParsedReceipt


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
