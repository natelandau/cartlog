"""Tests for the Anthropic-backed LLM receipt parser."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from cartlog.parsing.llm_parser import LLMReceiptParser
from cartlog.parsing.schema import ParsedReceipt


def test_llm_parser_returns_parsed_output(tmp_path, sample_parsed_receipt):
    """Verify an image is sent as a base64 image block and parsed_output is returned."""
    # Given an image file and a client that returns a parsed receipt
    image = tmp_path / "receipt.png"
    image.write_bytes(b"\x89PNG fake bytes")

    fake_response = MagicMock()
    fake_response.parsed_output = sample_parsed_receipt
    client = MagicMock()
    client.messages.parse.return_value = fake_response

    # When parsing the image
    parser = LLMReceiptParser(client=client, model="claude-opus-4-8")
    result = parser.parse(image)

    # Then the structured output is returned and the call carries a base64 image block
    assert isinstance(result, ParsedReceipt)
    assert result.store_name == "Safeway"

    kwargs = client.messages.parse.call_args.kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["output_format"] is ParsedReceipt
    source_block = kwargs["messages"][0]["content"][0]
    assert source_block["type"] == "image"
    assert source_block["source"]["media_type"] == "image/png"


def test_llm_parser_sends_pdf_as_document(tmp_path, sample_parsed_receipt):
    """Verify a PDF is sent as a base64 document block rather than an image block."""
    # Given a PDF file and a client that returns a parsed receipt
    pdf = tmp_path / "receipt.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")

    fake_response = MagicMock()
    fake_response.parsed_output = sample_parsed_receipt
    client = MagicMock()
    client.messages.parse.return_value = fake_response

    # When parsing the PDF
    parser = LLMReceiptParser(client=client, model="claude-opus-4-8")
    result = parser.parse(pdf)

    # Then the structured output is returned and the call carries a base64 document block
    assert isinstance(result, ParsedReceipt)

    kwargs = client.messages.parse.call_args.kwargs
    source_block = kwargs["messages"][0]["content"][0]
    assert source_block["type"] == "document"
    assert source_block["source"]["media_type"] == "application/pdf"


def test_llm_parser_rejects_unknown_extension(tmp_path):
    """Verify an unsupported file extension raises ValueError before any API call."""
    # Given a file with an unsupported extension
    bad = tmp_path / "receipt.tiff"
    bad.write_bytes(b"x")
    parser = LLMReceiptParser(client=MagicMock(), model="claude-opus-4-8")

    # When parsing it, then a ValueError is raised
    with pytest.raises(ValueError, match=r"(?i)unsupported file type"):
        parser.parse(bad)


def test_prompt_includes_allowed_categories(tmp_path) -> None:
    """Verify the prompt lists the allowed taxonomy paths when provided."""
    # Given a receipt image and a client that captures kwargs but returns no structured output
    image = tmp_path / "receipt.png"
    image.write_bytes(b"\x89PNG fake bytes")
    captured: dict[str, Any] = {}

    class _Messages:
        def parse(self, **kwargs: object) -> object:
            captured.update(kwargs)

            class _Resp:
                parsed_output = None

            return _Resp()

    class _Client:
        messages = _Messages()

    # When building a parser with allowed categories and parsing the image
    parser = LLMReceiptParser(
        client=_Client(), model="m", allowed_categories=["dairy & eggs", "produce"]
    )
    with pytest.raises(ValueError, match="no structured"):
        parser.parse(image)

    # Then both allowed category names appear in the prompt text
    prompt_text = captured["messages"][0]["content"][1]["text"]
    assert "dairy & eggs" in prompt_text
    assert "produce" in prompt_text


@pytest.mark.parametrize("allowed", [None, []])
def test_prompt_falls_back_when_no_categories(tmp_path, allowed: list[str] | None) -> None:
    """Verify the prompt is unconstrained (no allowed list) when no categories are given."""
    # Given a receipt image and a client that captures kwargs but returns no structured output
    image = tmp_path / "receipt.png"
    image.write_bytes(b"\x89PNG fake bytes")
    captured: dict[str, Any] = {}

    class _Messages:
        def parse(self, **kwargs: object) -> object:
            captured.update(kwargs)

            class _Resp:
                parsed_output = None

            return _Resp()

    class _Client:
        messages = _Messages()

    # When building a parser with no allowed categories and parsing the image
    parser = LLMReceiptParser(client=_Client(), model="m", allowed_categories=allowed)
    with pytest.raises(ValueError, match="no structured"):
        parser.parse(image)

    # Then the prompt does not contain the constrained allowed-categories section
    prompt_text = captured["messages"][0]["content"][1]["text"]
    assert "Allowed categories:" not in prompt_text
