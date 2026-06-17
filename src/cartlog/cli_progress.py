"""Live multi-stage progress checklist for the CLI, rendered with rich.

Renders an ordered list of named stages where each line shows pending (dim), active
(animated spinner), done (green check), or failed (red cross). Used by the ingest command
to give synchronous terminal feedback while a receipt is parsed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from rich.console import Group
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

if TYPE_CHECKING:
    from collections.abc import Hashable, Iterable

    from rich.console import Console, RenderableType


class StageChecklist:
    """A live, updating checklist of named stages for one CLI operation.

    Use as a context manager so the live display is always stopped, even on error. Any stage
    still active when the context exits is marked failed, so a crash never leaves a frozen
    spinner on screen.
    """

    def __init__(self, console: Console, stages: Iterable[tuple[Hashable, str]]) -> None:
        """Build a checklist over ordered (key, label) stages.

        Args:
            console: Rich console to render on.
            stages: Ordered (key, label) pairs; keys identify stages when advancing them.
        """
        self._labels: dict[Hashable, str] = dict(stages)
        self._order: list[Hashable] = list(self._labels)
        self._state: dict[Hashable, str] = dict.fromkeys(self._order, "pending")
        # One persistent spinner so its animation is continuous across redraws.
        self._spinner = Spinner("dots")
        self._live = Live(self._render(), console=console, refresh_per_second=12)

    def __enter__(self) -> Self:
        """Start the live display."""
        self._live.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Mark any still-active stage failed and stop the live display."""
        for key, state in self._state.items():
            if state == "active":
                self._state[key] = "failed"
        self._live.update(self._render(), refresh=True)
        self._live.stop()

    def start(self, key: Hashable) -> None:
        """Mark `key` active, finishing any previously active stage.

        Args:
            key: A stage key passed to the constructor. An unknown key raises KeyError.
        """
        for other, state in self._state.items():
            if state == "active":
                self._state[other] = "done"
        self._state[key] = "active"
        self._spinner.update(text=self._labels[key])
        self._live.update(self._render(), refresh=True)

    def finish(self) -> None:
        """Mark the currently active stage done without starting a new one."""
        for key, state in self._state.items():
            if state == "active":
                self._state[key] = "done"
        self._live.update(self._render(), refresh=True)

    def _render(self) -> RenderableType:
        """Build the renderable for the current stage states."""
        lines: list[RenderableType] = []
        for key in self._order:
            label = self._labels[key]
            state = self._state[key]
            if state == "done":
                lines.append(Text.assemble(("✓ ", "green"), label))
            elif state == "failed":
                lines.append(Text.assemble(("✗ ", "red"), (label, "red")))
            elif state == "active":
                lines.append(self._spinner)
            else:
                lines.append(Text(f"  {label}", style="dim"))
        return Group(*lines)
