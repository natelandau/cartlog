"""Tests for the genai-prices cost wrapper."""

from decimal import Decimal

from pydantic_ai.usage import RunUsage

from cartlog.parsing.pricing import estimate_cost


def test_estimate_cost_known_model_returns_positive_decimal():
    """Verify a known model and non-zero usage yields a positive Decimal cost."""
    # Given usage against a model genai-prices knows
    usage = RunUsage(input_tokens=1000, output_tokens=500)

    # When estimating the cost
    cost = estimate_cost("anthropic:claude-opus-4-8", usage)

    # Then the cost is a positive Decimal
    assert isinstance(cost, Decimal)
    assert cost > 0


def test_estimate_cost_unknown_model_returns_none():
    """Verify an unknown model returns None instead of raising."""
    # Given usage against a model genai-prices does not know
    usage = RunUsage(input_tokens=1000, output_tokens=500)

    # When estimating the cost
    cost = estimate_cost("anthropic:totally-made-up-model-xyz", usage)

    # Then no cost is returned
    assert cost is None
