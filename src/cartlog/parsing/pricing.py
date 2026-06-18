"""Estimate the USD cost of an LLM call from its token usage via genai-prices."""

from __future__ import annotations

from typing import TYPE_CHECKING

from genai_prices import Usage as PriceUsage
from genai_prices import calc_price

if TYPE_CHECKING:
    from decimal import Decimal

    from pydantic_ai.usage import RunUsage


def estimate_cost(model: str, usage: RunUsage) -> Decimal | None:
    """Estimate the USD cost of one model call, for showing a hoster their parsing bill.

    Prices `usage` under `model` using genai-prices' bundled pricing data. `model` is the
    provider-prefixed id used throughout cartlog (e.g. "anthropic:claude-opus-4-8"); the
    provider prefix is split off and passed to genai-prices to disambiguate the lookup.

    Args:
        model: Provider-prefixed model id, e.g. "anthropic:claude-opus-4-8".
        usage: The token usage reported by Pydantic AI for the call.

    Returns:
        The estimated cost as a Decimal, or None when genai-prices has no pricing data
        for the model (the caller still stores the raw token counts).
    """
    provider_id, _sep, model_ref = model.partition(":")
    if not model_ref:
        # No provider prefix present; treat the whole string as the model reference.
        provider_id, model_ref = "", model
    try:
        calculation = calc_price(
            PriceUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            ),
            model_ref,
            provider_id=provider_id or None,
        )
    except LookupError:
        # genai-prices raises LookupError when it cannot match the model to a provider.
        return None
    return calculation.total_price
