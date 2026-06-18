"""Tests for the provider-agnostic LLM receipt parser."""

from unittest.mock import MagicMock

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel

from cartlog.parsing.llm_parser import LLMReceiptParser, _build_prompt
from cartlog.parsing.schema import ParsedReceipt


def test_parse_returns_structured_output(tmp_path, sample_parsed_receipt):
    """Verify parsing an image returns the model's structured ParsedReceipt."""
    # Given an image and a TestModel that returns a fixed receipt
    image = tmp_path / "receipt.png"
    image.write_bytes(b"\x89PNG fake bytes")
    model = TestModel(custom_output_args=sample_parsed_receipt.model_dump(mode="json"))

    # When parsing the image
    parser = LLMReceiptParser(model=model)
    result = parser.parse(image)

    # Then the structured receipt is returned
    assert isinstance(result, ParsedReceipt)
    assert result.store_name == "Safeway"


def test_build_binary_content_uses_pdf_media_type(tmp_path):
    """Verify a PDF becomes BinaryContent with the application/pdf media type."""
    # Given a PDF file
    pdf = tmp_path / "receipt.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")

    # When building its content block
    content = LLMReceiptParser._build_binary_content(pdf)

    # Then the media type marks it as a PDF document
    assert content.media_type == "application/pdf"


def test_build_binary_content_uses_image_media_type(tmp_path):
    """Verify a PNG becomes BinaryContent with the image/png media type."""
    # Given a PNG file
    image = tmp_path / "receipt.png"
    image.write_bytes(b"\x89PNG fake bytes")

    # When building its content block
    content = LLMReceiptParser._build_binary_content(image)

    # Then the media type marks it as a PNG image
    assert content.media_type == "image/png"


def test_parse_rejects_unknown_extension(tmp_path):
    """Verify an unsupported file extension raises ValueError before any API call."""
    # Given a file with an unsupported extension
    bad = tmp_path / "receipt.tiff"
    bad.write_bytes(b"x")
    parser = LLMReceiptParser(model=TestModel())

    # When parsing it, then a ValueError is raised
    with pytest.raises(ValueError, match=r"(?i)unsupported file type"):
        parser.parse(bad)


def test_parse_wraps_model_failure_as_value_error(tmp_path):
    """Verify a model failure is re-raised as the no-structured-output ValueError."""
    # Given a parser whose agent raises a model-behavior error
    image = tmp_path / "receipt.png"
    image.write_bytes(b"\x89PNG fake bytes")
    parser = LLMReceiptParser(model=TestModel())
    parser._agent.run_sync = MagicMock(side_effect=UnexpectedModelBehavior("boom"))  # type: ignore[invalid-assignment]  # ty:ignore[invalid-assignment]

    # When parsing, then the failure surfaces as the expected ValueError
    with pytest.raises(ValueError, match="no structured"):
        parser.parse(image)


def test_build_prompt_includes_allowed_categories():
    """Verify the prompt lists the allowed taxonomy names when provided."""
    # Given an allowed taxonomy
    prompt = _build_prompt(["dairy & eggs", "produce"])

    # Then both names and the constrained section appear
    assert "dairy & eggs" in prompt
    assert "produce" in prompt
    assert "Allowed categories:" in prompt


@pytest.mark.parametrize("allowed", [None, []])
def test_build_prompt_falls_back_when_no_categories(allowed):
    """Verify the prompt omits the constrained section when no categories are given."""
    # Given no allowed taxonomy
    prompt = _build_prompt(allowed)

    # Then the constrained section is absent
    assert "Allowed categories:" not in prompt


def test_prompt_lists_allowed_measure_units():
    """Verify the prompt contains measure field names and an allowed unit token."""
    # Given categories for a constrained prompt
    prompt = _build_prompt(["produce", "dairy & eggs"])

    # Then measure guidance and at least one canonical unit token appear
    assert "measure_value" in prompt
    assert "floz" in prompt  # an allowed unit token appears in the guidance
