"""Tests for the focused LLM category classifier."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cartlog.parsing.category_classifier import LLMCategoryClassifier, ProductToClassify

ALLOWED = ["fruits", "vegetables", "dairy & eggs"]


def _classifier_returning(items: list[SimpleNamespace]) -> tuple[LLMCategoryClassifier, MagicMock]:
    """Build a classifier whose mocked client returns the given structured items."""
    fake_response = MagicMock()
    fake_response.parsed_output = SimpleNamespace(items=items)
    client = MagicMock()
    client.messages.parse.return_value = fake_response
    return LLMCategoryClassifier(
        client=client, model="claude-haiku-4-5", allowed_categories=ALLOWED
    ), client


def test_classify_maps_answers_and_coerces_uncategorized():
    """Verify a valid category maps through and the uncategorized escape becomes None."""
    # Given a classifier whose model answers one product and declines another
    classifier, _client = _classifier_returning(
        [
            SimpleNamespace(canonical_name="bananas", category="fruits"),
            SimpleNamespace(canonical_name="mystery widget", category="uncategorized"),
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


def test_classify_coerces_unknown_category_to_none():
    """Verify a category outside the taxonomy is treated as a decline (defense in depth)."""
    # Given a model that returns a category not in the allowed list
    classifier, _client = _classifier_returning(
        [SimpleNamespace(canonical_name="spinach", category="produce")]
    )

    # When classifying
    result = classifier.classify(
        [ProductToClassify(canonical_name="spinach", raw_description="SPINACH")]
    )

    # Then the invented category is coerced to None rather than trusted
    assert result == {"spinach": None}


def test_classify_empty_input_makes_no_call():
    """Verify classifying nothing returns an empty dict without calling the model."""
    # Given a classifier
    classifier, client = _classifier_returning([])

    # When classifying an empty batch
    result = classifier.classify([])

    # Then no API call is made
    assert result == {}
    client.messages.parse.assert_not_called()


def test_classify_prompt_is_focused_and_lists_taxonomy():
    """Verify the prompt is categorization-only, lists the taxonomy, and carries the produce rule."""
    # Given a classifier and one product with an earlier guess
    classifier, client = _classifier_returning(
        [SimpleNamespace(canonical_name="bananas", category="fruits")]
    )

    # When classifying
    classifier.classify(
        [
            ProductToClassify(
                canonical_name="bananas",
                raw_description="BANANAS",
                original_guesses=("produce",),
            )
        ]
    )

    # Then the prompt carries the taxonomy, the product, the produce rule, and no receipt framing
    prompt = client.messages.parse.call_args.kwargs["messages"][0]["content"][0]["text"]
    assert "dairy & eggs" in prompt
    assert "bananas" in prompt
    assert "produce" in prompt  # the disambiguation rule and the earlier guess
    assert "purchase date" not in prompt.lower()
    assert "grand total" not in prompt.lower()


def test_classifier_requires_allowed_categories():
    """Verify constructing a classifier with no taxonomy raises."""
    # Given/When/Then an empty allowed list is rejected
    with pytest.raises(ValueError, match="non-empty allowed_categories"):
        LLMCategoryClassifier(client=MagicMock(), model="m", allowed_categories=[])
