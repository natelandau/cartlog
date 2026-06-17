"""Anthropic vision-model parser that turns receipt files into ParsedReceipt objects."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from cartlog.parsing.schema import ParsedReceipt

if TYPE_CHECKING:
    from pathlib import Path

_IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_PDF_SUFFIX = ".pdf"
_PDF_MEDIA_TYPE = "application/pdf"

# File suffixes this parser can ingest; reused by upload sources to reject unsupported files.
SUPPORTED_SUFFIXES: frozenset[str] = frozenset({*_IMAGE_MEDIA_TYPES, _PDF_SUFFIX})

_PROMPT_BASE = (
    "You are reading a scanned grocery receipt. Extract every purchased line item. "
    "For each line, return the raw description exactly as printed, plus a normalized "
    "canonical product name (e.g. 'GV LRG EGGS 12CT' -> 'eggs') and a category. "
    "Also return the store name and location, the purchase date, the currency, and the "
    "grand total. Set 'confidence' to your overall confidence from 0 to 1."
)
_CATEGORY_RULES = (
    " Choose each category EXACTLY from this allowed list; never invent a new category. If "
    "nothing fits, return an empty string for that line's category. Do NOT use storage-only "
    "tags (shelf stable, refrigerated); only use 'frozen' when frozen changes the product "
    "itself (frozen berries vs fresh). Allowed categories:\n{allowed}"
)


def _build_prompt(allowed_categories: list[str] | None) -> str:
    """Build the parser prompt, constraining categories to the allowed list when provided."""
    if allowed_categories:
        bulleted = "\n".join(f"- {name}" for name in allowed_categories)
        return _PROMPT_BASE + _CATEGORY_RULES.format(allowed=bulleted)
    # No categories seeded yet: fall back to free-form guidance.
    return _PROMPT_BASE + " Use a single category like 'dairy & eggs' or 'produce'."


class LLMReceiptParser:
    """Parses a receipt image or PDF into a ParsedReceipt using an Anthropic vision model.

    Inject an `anthropic.Anthropic` client so tests can substitute a mock
    without making real network calls. Pass `allowed_categories` to constrain
    the model to a fixed taxonomy; omit it to fall back to free-form guidance.
    """

    def __init__(
        self, client: object, model: str, allowed_categories: list[str] | None = None
    ) -> None:
        """Store the injected client, the model name, and the allowed taxonomy paths.

        An empty list is treated the same as None: both fall back to free-form
        category guidance rather than constraining the prompt to a fixed taxonomy.
        """
        # client is an anthropic.Anthropic instance; injected so tests can mock it.
        self._client = client
        self._model = model
        self._prompt = _build_prompt(allowed_categories)

    def parse(self, file_path: Path) -> ParsedReceipt:
        """Parse a receipt file into a structured ParsedReceipt.

        Args:
            file_path: Path to the receipt. Must be a supported format
                (.png, .jpg, .jpeg, .webp, .gif, or .pdf).

        Returns:
            ParsedReceipt populated by the LLM's structured output.

        Raises:
            ValueError: If the file extension is not supported, or if the model
                returned no structured output (e.g. the response was truncated or refused).
        """
        source_block = self._build_source_block(file_path)

        response = self._client.messages.parse(  # type: ignore[attr-defined, ty:unresolved-attribute]
            model=self._model,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [source_block, {"type": "text", "text": self._prompt}],
                }
            ],
            output_format=ParsedReceipt,
        )
        parsed: ParsedReceipt | None = response.parsed_output  # type: ignore[attr-defined]
        if parsed is None:
            msg = (
                "Model returned no structured receipt; the response may have been "
                "truncated (raise max_tokens) or refused."
            )
            raise ValueError(msg)
        return parsed

    @staticmethod
    def _build_source_block(file_path: Path) -> dict[str, object]:
        """Build the base64 content block for the file, choosing image vs. document by suffix."""
        suffix = file_path.suffix.lower()

        # PDFs use a 'document' content block; the model reads each page with vision.
        if suffix == _PDF_SUFFIX:
            block_type, media_type = "document", _PDF_MEDIA_TYPE
        elif suffix in _IMAGE_MEDIA_TYPES:
            block_type, media_type = "image", _IMAGE_MEDIA_TYPES[suffix]
        else:
            # Validate before reading so an unsupported file is rejected without I/O.
            msg = f"Unsupported file type: {file_path.suffix}"
            raise ValueError(msg)

        encoded = base64.standard_b64encode(file_path.read_bytes()).decode("utf-8")
        return {
            "type": block_type,
            "source": {"type": "base64", "media_type": media_type, "data": encoded},
        }
