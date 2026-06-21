"""Tests for the focused LLM size extractor using a stub model (no network)."""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cartlog.parsing.size_extractor import LineToSize, LLMSizeExtractor, ParsedSize


def _model_returning(items: list[dict]) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # The output tool is the structured-result schema; answer it directly.
        tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(tool.name, {"items": items})])

    return FunctionModel(respond)


def test_extractor_maps_answers_by_key_and_declines_with_none():
    """Verify the extractor maps answers by key and yields None for declined lines."""
    # Given a stub model that returns one sized item and one decline
    model = _model_returning(
        [
            {"key": "1", "measure_value": 11.0, "measure_unit": "oz"},
            {"key": "2", "measure_value": None, "measure_unit": "none"},
        ]
    )
    extractor = LLMSizeExtractor(model=model)
    lines = [
        LineToSize(key="1", canonical_name="granola", raw_description="Granola 11oz Bob's"),
        LineToSize(key="2", canonical_name="apple", raw_description="A Single Apple"),
    ]

    # When extracting sizes
    out = extractor.extract(lines)

    # Then the sized item maps to ParsedSize and the decline maps to None
    assert out == {"1": ParsedSize(value=11.0, unit="oz"), "2": None}


def test_extractor_returns_empty_for_no_lines():
    """Verify extracting an empty batch returns an empty dict without calling the model."""
    # Given an extractor with a stub model
    extractor = LLMSizeExtractor(model=_model_returning([]))

    # When extracting with no lines
    result = extractor.extract([])

    # Then the result is empty
    assert result == {}
