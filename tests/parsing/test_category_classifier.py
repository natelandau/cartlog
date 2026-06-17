"""Tests for the focused LLM category classifier."""

from unittest.mock import MagicMock

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel

from cartlog.parsing.category_classifier import LLMCategoryClassifier, ProductToClassify

ALLOWED = ["fruits", "vegetables", "dairy & eggs"]


def _classifier_returning(items: list[dict]) -> LLMCategoryClassifier:
    """Build a classifier whose injected TestModel returns the given structured items."""
    model = TestModel(custom_output_args={"items": items})
    return LLMCategoryClassifier(model=model, allowed_categories=ALLOWED)


def test_classify_maps_answers_and_coerces_uncategorized():
    """Verify a valid category maps through and the uncategorized escape becomes None."""
    # Given a classifier whose model places one product and declines another
    classifier = _classifier_returning(
        [
            {"canonical_name": "bananas", "category": "fruits"},
            {"canonical_name": "mystery widget", "category": "uncategorized"},
        ]
    )

    # When classifying both products
    result = classifier.classify(
        [
            ProductToClassify(canonical_name="bananas", raw_description="BANANAS"),
            ProductToClassify(canonical_name="mystery widget", raw_description="MYST WIDGET"),
        ]
    )

    # Then the placed product maps to its taxonomy name and the decline becomes None
    assert result == {"bananas": "fruits", "mystery widget": None}


def test_classify_empty_input_makes_no_call():
    """Verify classifying nothing returns an empty dict without calling the model."""
    # Given a classifier with a spy on its agent
    classifier = _classifier_returning([])
    mock_run_sync = MagicMock()
    classifier._agent.run_sync = mock_run_sync  # ty:ignore[invalid-assignment]

    # When classifying an empty batch
    result = classifier.classify([])

    # Then no model call is made
    assert result == {}
    mock_run_sync.assert_not_called()


def test_classify_wraps_model_failure_as_value_error():
    """Verify a model failure is re-raised as the no-structured-output ValueError."""
    # Given a classifier whose agent raises a model-behavior error
    classifier = _classifier_returning([])
    classifier._agent.run_sync = MagicMock(side_effect=UnexpectedModelBehavior("boom"))  # ty:ignore[invalid-assignment]

    # When classifying, then the failure surfaces as the expected ValueError
    with pytest.raises(ValueError, match="no structured output"):
        classifier.classify([ProductToClassify(canonical_name="x", raw_description="X")])


def test_classify_prompt_is_focused_and_lists_taxonomy():
    """Verify the prompt is categorization-only, lists the taxonomy, and carries the produce rule."""
    # Given a classifier and one product with an earlier guess
    classifier = _classifier_returning([])

    # When building the prompt for that product
    prompt = classifier._build_prompt(
        [
            ProductToClassify(
                canonical_name="bananas",
                raw_description="BANANAS",
                original_guesses=("produce",),
            )
        ]
    )

    # Then the prompt carries the taxonomy, the product, the produce rule, and no receipt framing
    assert "dairy & eggs" in prompt
    assert "bananas" in prompt
    assert "produce" in prompt  # the disambiguation rule and the earlier guess
    assert "purchase date" not in prompt.lower()
    assert "grand total" not in prompt.lower()


def test_classifier_requires_allowed_categories():
    """Verify constructing a classifier with no taxonomy raises."""
    # Given/When/Then an empty allowed list is rejected
    with pytest.raises(ValueError, match="non-empty allowed_categories"):
        LLMCategoryClassifier(model=TestModel(), allowed_categories=[])
