"""A focused, single-purpose LLM extractor that recovers a package size from line text.

The receipt extraction pass often leaves the size embedded in the raw description without
structuring it. This extractor does one thing: given a product's text, return a structured
(value, unit) size or decline. Output is constrained to the canonical unit tokens plus an
explicit "none" escape so the model can decline rather than invent a unit. Mirrors the
category reclassifier; intended to run on the cheap assist model.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import BaseModel, create_model
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.settings import ModelSettings

from cartlog.constants import ALLOWED_UNIT_TOKENS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.models import Model
    from pydantic_ai.usage import RunUsage

_MAX_TOKENS = 2048
# The legal "no size present" answer, kept out of the unit token set.
_NONE_CHOICE = "none"


@dataclass(frozen=True)
class LineToSize:
    """One line to size: a caller-owned key plus the text the model reasons over."""

    key: str
    canonical_name: str
    raw_description: str


@dataclass(frozen=True)
class ParsedSize:
    """A structured size recovered from text: a numeric value and a canonical unit token."""

    value: float
    unit: str


class _SizedItem(Protocol):
    key: str
    measure_value: float | None
    measure_unit: str


class _ExtractionOutput(Protocol):
    items: Sequence[_SizedItem]


class SizeExtractor(Protocol):
    """Recover a package size for each line, returning key -> ParsedSize (or None to decline)."""

    def extract(
        self, lines: Sequence[LineToSize], *, usage: RunUsage | None = None
    ) -> dict[str, ParsedSize | None]:
        """Return a structured size per line key, or None where the model declined."""
        ...


@lru_cache(maxsize=1)
def _build_output_model() -> type[BaseModel]:
    """Build a result model whose unit field is a Literal over the allowed tokens plus 'none'."""
    choices = (*ALLOWED_UNIT_TOKENS, _NONE_CHOICE)
    unit_type = Literal[choices]  # type: ignore[valid-type, ty:invalid-type-form]
    item_model = create_model(
        "SizedItem",
        key=(str, ...),
        measure_value=(float | None, ...),
        measure_unit=(unit_type, ...),
    )
    return create_model("SizeExtractionResult", items=(list[item_model], ...))  # type: ignore[ty:invalid-type-form]


class LLMSizeExtractor:
    """Recover package sizes from line text with a narrow, single-purpose LLM call.

    Inject a pydantic_ai Model so tests can substitute a TestModel/FunctionModel. A cheap model
    (e.g. Haiku) is appropriate for this narrow task.
    """

    def __init__(self, model: Model) -> None:
        """Build the extraction agent and precompile the unit-constrained output schema."""
        self._output_model = _build_output_model()
        self._agent = Agent(
            model,
            output_type=self._output_model,
            model_settings=ModelSettings(max_tokens=_MAX_TOKENS),
        )

    def extract(
        self, lines: Sequence[LineToSize], *, usage: RunUsage | None = None
    ) -> dict[str, ParsedSize | None]:
        """Return key -> ParsedSize for each line, or None where the model declined.

        Raises:
            ValueError: If the model returned no structured output.
        """
        if not lines:
            return {}
        prompt = self._build_prompt(lines)
        try:
            result = self._agent.run_sync(prompt, usage=usage)
        except UnexpectedModelBehavior as exc:
            msg = "Size extractor returned no structured output; the response may have been truncated."
            raise ValueError(msg) from exc
        extracted = cast("_ExtractionOutput", result.output)
        out: dict[str, ParsedSize | None] = {}
        for entry in extracted.items:
            if entry.measure_value is None or entry.measure_unit == _NONE_CHOICE:
                out[entry.key] = None
            else:
                out[entry.key] = ParsedSize(value=entry.measure_value, unit=entry.measure_unit)
        return out

    def _build_prompt(self, lines: Sequence[LineToSize]) -> str:
        """Render the size-extraction prompt for a batch of lines."""
        units = ", ".join(ALLOWED_UNIT_TOKENS)
        line_block = "\n".join(
            f'- key: "{line.key}" | name: "{line.canonical_name}" | '
            f'receipt text: "{line.raw_description}"'
            for line in lines
        )
        return (
            "You read grocery line items and recover the package size when one is present in "
            "the text. For each line return measure_value (a number, the net content of ONE "
            f"package) and measure_unit (EXACTLY one of: {units}). If no size is present in the "
            f'text, return measure_value null and measure_unit "{_NONE_CHOICE}". Do not guess a '
            "size that is not written in the text.\n\n"
            f"Lines:\n{line_block}\n\n"
            "Return one entry per line with its exact key."
        )
