"""Tests that fixtures conform to the ReceiptParser protocol."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from cartlog.parsing.schema import ParsedReceipt

if TYPE_CHECKING:
    from cartlog.parsing.protocol import ReceiptParser


def test_fake_parser_satisfies_protocol(fake_parser):
    """Verify the fake_parser fixture is usable wherever a ReceiptParser is expected."""
    # The fixture must be usable anywhere a ReceiptParser is expected.
    parser: ReceiptParser = fake_parser
    result = parser.parse(Path("anything.png"))
    assert isinstance(result, ParsedReceipt)
