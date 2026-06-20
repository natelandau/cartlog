"""Protocol describing the interface every receipt parser must implement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai.usage import RunUsage

    from cartlog.parsing.schema import ParsedReceipt


class ReceiptParser(Protocol):
    """A receipt parser turns a receipt file (image or PDF) into a structured ParsedReceipt."""

    def parse(self, file_path: Path, *, usage: RunUsage | None = None) -> ParsedReceipt:
        """Parse the receipt at `file_path`, accumulating token usage into `usage` if given."""
        ...
