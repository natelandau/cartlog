"""Construct the production LLM parser and classifier from settings.

These builders turn provider-prefixed model strings into ready-to-use parsing components,
failing fast with a `ModelConfigurationError` when the provider key is unset, the provider
prefix is unknown, or there is no taxonomy to classify into. `serve` patches these out in
tests and translates the error into a clean exit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.exceptions import UserError
from pydantic_ai.models import infer_model

from cartlog.exceptions import ModelConfigurationError
from cartlog.parsing.category_classifier import LLMCategoryClassifier
from cartlog.parsing.llm_parser import LLMReceiptParser
from cartlog.parsing.size_extractor import LLMSizeExtractor

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from cartlog.config import Settings


def build_model(model_id: str) -> Model:
    """Build a Pydantic AI model from a provider-prefixed id, failing fast on a bad config.

    Construction reads the provider's API key from its native environment variable. A missing
    key raises UserError; an unknown provider prefix raises ValueError. Both are surfaced as a
    ModelConfigurationError naming the problem rather than a raw traceback.

    Raises:
        ModelConfigurationError: If the provider key is unset or the model id names an unknown
            provider.
    """
    try:
        return infer_model(model_id)
    except (UserError, ValueError) as exc:
        raise ModelConfigurationError(str(exc)) from exc


def build_parser(
    settings: Settings, allowed_categories: list[str] | None = None
) -> LLMReceiptParser:
    """Construct the production LLM parser from settings (patched out in tests).

    An empty list for `allowed_categories` is treated the same as None: both
    fall back to free-form category guidance in the prompt.
    """
    model = build_model(settings.parse_model)
    return LLMReceiptParser(model=model, allowed_categories=allowed_categories)


def build_classifier(settings: Settings, allowed_categories: list[str]) -> LLMCategoryClassifier:
    """Construct the focused category classifier from settings (patched out in tests).

    Raises:
        ModelConfigurationError: If the taxonomy is empty (nothing to classify into).
    """
    # Validate the provider key before the taxonomy so a missing key reports first, matching
    # the pre-flight order callers rely on (a key error is the root cause, not the taxonomy).
    model = build_model(settings.assist_model)
    if not allowed_categories:
        msg = "No categories in the taxonomy to classify into; seed categories first."
        raise ModelConfigurationError(msg)
    return LLMCategoryClassifier(model=model, allowed_categories=allowed_categories)


def build_size_extractor(settings: Settings) -> LLMSizeExtractor:
    """Construct the focused size extractor from settings (patched out in tests).

    Uses the cheap assist model. Raises ModelConfigurationError on a missing/invalid key,
    matching build_classifier so callers handle one error type.
    """
    model = build_model(settings.assist_model)
    return LLMSizeExtractor(model=model)


def build_ingest_classifier(
    settings: Settings, allowed_categories: list[str]
) -> LLMCategoryClassifier | None:
    """Build the auto-reclassification classifier for ingestion, or None if no taxonomy exists.

    Returns None when no categories are seeded yet, so ingestion runs without the second pass
    rather than failing; the API key is already validated by the parser build. Pass the allowed
    taxonomy the caller already read, so the command issues a single query for it.
    """
    if not allowed_categories:
        return None
    return build_classifier(settings, allowed_categories)
