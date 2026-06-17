"""Protocol describing the interface every receipt parser must implement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from cartlog.parsing.schema import ParsedReceipt


class ReceiptParser(Protocol):
    """A receipt parser turns a receipt file (image or PDF) into a structured ParsedReceipt."""

    def parse(self, file_path: Path) -> ParsedReceipt:
        """Parse the receipt at `file_path` into a structured ParsedReceipt."""
        ...
