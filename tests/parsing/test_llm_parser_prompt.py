"""The parser prompt must instruct the model to lift embedded sizes into structured fields."""

from cartlog.parsing.llm_parser import _build_prompt


def test_prompt_instructs_lifting_embedded_sizes():
    """Verify the parser prompt contains guidance for lifting embedded sizes and per-each produce."""
    prompt = _build_prompt(["produce", "dairy & eggs"])
    lowered = prompt.lower()
    assert "anywhere in the line" in lowered
    assert "per count" in lowered or "per each" in lowered
