"""Notebook display — ``LiveDisplay`` + Claude-notes / journal exchange.

The notebook subclass adds the bidirectional Claude ↔ notebook channel
(``note()``, ``render_claude_notes()``, ``render_journal()``) on top of
the shared rendering. Default ``_write`` (``print``) is correct here —
notebooks have no progress bars to trample.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.infrastructure.persistence.session_emitter import (
    append_journal,
    read_claude_notes,
)
from promptpotter.presentation.views.formatting import render_markdown_box
from promptpotter.presentation.views.live_display import LiveDisplay

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import Stores


class NotebookDisplay(LiveDisplay):
    """Notebook listener — shared rendering + Claude-notes API."""

    def __init__(self, *, store: Stores, **kwargs) -> None:
        super().__init__(**kwargs)
        self._store = store

    def _resolve_session_dir(self) -> Path:
        from promptpotter.infrastructure.store import read_active_pointer

        _, sid, _cid = read_active_pointer()
        if not sid:
            raise RuntimeError(
                "No active session - run init/auto-mint before calling "
                "display.note() or display.render_claude_notes()."
            )
        return self._store.sessions.session_dir(sid)

    def note(self, action: str, body: str = "") -> None:
        """Append a narrative note to ``journal.md`` for Claude."""
        append_journal(self._resolve_session_dir(), action, body)

    def render_claude_notes(self) -> None:
        """Render ``notes.md`` inline so Claude's notes appear in a cell."""
        content = read_claude_notes(self._resolve_session_dir()).rstrip()
        print(render_markdown_box("CLAUDE NOTES", content, "(no claude notes yet)"))

    def render_journal(self) -> None:
        """Render ``journal.md`` inline - mirror of notes."""
        path = self._resolve_session_dir() / "journal.md"
        content = path.read_text(encoding="utf-8").rstrip() if path.exists() else ""
        print(render_markdown_box("JOURNAL", content, "(no journal entries yet)"))
