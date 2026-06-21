"""Provider-agnostic vision parser that turns receipt files into ParsedReceipt objects."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic_ai import Agent, BinaryContent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.settings import ModelSettings

from cartlog.constants import (
    ALLOWED_UNIT_TOKENS,
    IMAGE_MEDIA_TYPES,
    PDF_MEDIA_TYPE,
    PDF_SUFFIX,
)
from cartlog.parsing.schema import ParsedReceipt

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai.models import Model
    from pydantic_ai.usage import RunUsage

# Token budget for the parse response; large receipts need headroom for every line item.
_MAX_TOKENS = 4096

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
# Appended to every prompt so the model knows which unit tokens are canonical.
_MEASURE_RULES = (
    " For each line also return a structured package measure: 'measure_value' (a number) and "
    "'measure_unit' (exactly one of: {units}). Use the net content of ONE package (e.g. a 1.5L "
    "milk -> measure_value 1.5, measure_unit 'l'; a 12-count egg carton -> 12, 'ct'). If a size "
    "appears ANYWHERE IN THE LINE text (e.g. 'Granola, Maple Sea Salt, 11oz' -> 11, 'oz'), you "
    "MUST populate these fields from it rather than leaving them null. Leave both null for items "
    "sold loose by weight where the quantity already carries the unit (e.g. bananas priced per "
    "lb); for per-each produce printed as 'Per Count' or 'Per Each' (e.g. avocados sold each), "
    "leave measure_value null and let the quantity stand as the count; and for items with no "
    "measurable size (a single apple)."
)


def _build_prompt(allowed_categories: list[str] | None) -> str:
    """Build the parser prompt, constraining categories and measure units to allowed lists."""
    measure = _MEASURE_RULES.format(units=", ".join(ALLOWED_UNIT_TOKENS))
    if allowed_categories:
        bulleted = "\n".join(f"- {name}" for name in allowed_categories)
        return _PROMPT_BASE + _CATEGORY_RULES.format(allowed=bulleted) + measure
    # No categories seeded yet: fall back to free-form guidance.
    return _PROMPT_BASE + " Use a single category like 'dairy & eggs' or 'produce'." + measure


class LLMReceiptParser:
    """Parses a receipt image or PDF into a ParsedReceipt using a Pydantic AI vision model.

    Inject a `pydantic_ai` `Model` so tests can substitute a `TestModel`/`FunctionModel`
    without network calls. Pass `allowed_categories` to constrain the model to a fixed
    taxonomy; omit it to fall back to free-form guidance.
    """

    def __init__(self, model: Model, allowed_categories: list[str] | None = None) -> None:
        """Build the parsing agent and precompute the prompt for the given taxonomy.

        An empty list is treated the same as None: both fall back to free-form category
        guidance rather than constraining the prompt to a fixed taxonomy.
        """
        self._prompt = _build_prompt(allowed_categories)
        self._agent = Agent(
            model,
            output_type=ParsedReceipt,
            model_settings=ModelSettings(max_tokens=_MAX_TOKENS),
        )

    def parse(self, file_path: Path, *, usage: RunUsage | None = None) -> ParsedReceipt:
        """Parse a receipt file into a structured ParsedReceipt.

        Args:
            file_path: Path to the receipt. Must be a supported format
                (.png, .jpg, .jpeg, .webp, .gif, or .pdf).
            usage: Optional accumulator; token counts from this call are added into it so
                the caller can price the parse without coupling to the agent internals.

        Returns:
            ParsedReceipt populated by the model's structured output.

        Raises:
            ValueError: If the file extension is not supported, or if the model returned no
                usable structured output (e.g. the response was truncated or refused).
        """
        content = self._build_binary_content(file_path)
        try:
            # Image/PDF before the instruction text; vision models attend better when the
            # document precedes the question. Pass the accumulator so the caller can price it.
            result = self._agent.run_sync([content, self._prompt], usage=usage)
        except UnexpectedModelBehavior as exc:
            msg = (
                "Model returned no structured receipt; the response may have been "
                "truncated (raise max_tokens) or refused."
            )
            raise ValueError(msg) from exc
        return cast("ParsedReceipt", result.output)

    @staticmethod
    def _build_binary_content(file_path: Path) -> BinaryContent:
        """Read the file into a BinaryContent block, choosing the media type by suffix.

        PDFs are sent with the application/pdf media type so capable providers read each
        page natively; images use their format-specific media type.

        Raises:
            ValueError: If the file extension is not a supported image or PDF type.
        """
        suffix = file_path.suffix.lower()
        if suffix == PDF_SUFFIX:
            media_type = PDF_MEDIA_TYPE
        elif suffix in IMAGE_MEDIA_TYPES:
            media_type = IMAGE_MEDIA_TYPES[suffix]
        else:
            # Validate before reading so an unsupported file is rejected without I/O.
            msg = f"Unsupported file type: {file_path.suffix}"
            raise ValueError(msg)
        return BinaryContent(data=file_path.read_bytes(), media_type=media_type)
