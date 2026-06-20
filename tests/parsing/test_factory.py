"""Tests for the LLM parser/classifier factory."""

from __future__ import annotations

import pytest

from cartlog.exceptions import ModelConfigurationError
from cartlog.parsing.factory import build_model


def test_build_model_returns_model_for_valid_id():
    """Verify the factory builds a model when the provider key is present."""
    # Given the autouse dummy key fixture has set ANTHROPIC_API_KEY
    # When building a model from a provider-prefixed id
    model = build_model("anthropic:claude-opus-4-8")

    # Then a model object is returned
    assert model is not None


def test_build_model_raises_configuration_error_without_key(monkeypatch):
    """Verify a missing provider key surfaces as a ModelConfigurationError, not a raw error."""
    # Given no Anthropic key in the environment
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # When building a model that needs that key, then a domain error is raised
    with pytest.raises(ModelConfigurationError):
        build_model("anthropic:claude-opus-4-8")
