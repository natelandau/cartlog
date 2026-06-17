"""Tests for the CLI stage checklist."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from cartlog.cli_progress import StageChecklist


def _capturing_console() -> tuple[Console, StringIO]:
    """Build a non-terminal console writing to an in-memory buffer."""
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, width=80)
    return console, buffer


def test_stage_checklist_renders_stage_labels():
    """Verify the checklist renders each stage label and a completion check."""
    # Given a console capturing output and a two-stage checklist
    console, buffer = _capturing_console()
    stages = [("a", "First stage"), ("b", "Second stage")]

    # When advancing through both stages and finishing
    with StageChecklist(console, stages) as checklist:
        checklist.start("a")
        checklist.start("b")
        checklist.finish()

    # Then both labels and a green check marker appear in the output
    output = buffer.getvalue()
    assert "First stage" in output
    assert "Second stage" in output
    assert "✓" in output


def test_stage_checklist_marks_active_stage_failed_on_exit():
    """Verify a stage left active when the context exits is marked failed."""
    # Given a checklist with one stage started but never finished
    console, buffer = _capturing_console()

    # When the context exits with the stage still active
    with StageChecklist(console, [("a", "Only stage")]) as checklist:
        checklist.start("a")

    # Then the failure marker is rendered
    assert "✗" in buffer.getvalue()


def test_stage_checklist_start_finishes_previous_stage():
    """Verify starting a new stage marks the prior active stage done."""
    # Given a two-stage checklist
    console, buffer = _capturing_console()

    # When stage a is started then stage b is started (without an explicit finish)
    with StageChecklist(console, [("a", "First stage"), ("b", "Second stage")]) as checklist:
        checklist.start("a")
        checklist.start("b")
        checklist.finish()

    # Then a completion check and the first stage label are both present
    output = buffer.getvalue()
    assert "✓" in output
    assert "First stage" in output
