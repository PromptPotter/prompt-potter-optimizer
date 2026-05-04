"""Session narrative helpers — journal.md (operator log) + notes.md (Claude → operator)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from promptpotter.infrastructure.store.base import read_text_optional

__all__ = ["append_journal", "read_claude_notes"]


def append_journal(session_dir: Path, action: str, body: str = "") -> None:
    """Append a timestamped entry to ``{session_dir}/journal.md``."""
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    entry = f"## {ts} — {action}\n"
    if body:
        entry += f"\n{body}\n"
    with (session_dir / "journal.md").open("a", encoding="utf-8") as f:
        f.write(entry + "\n")


def read_claude_notes(session_dir: Path) -> str:
    """Read ``{session_dir}/notes.md``; empty string when absent."""
    return read_text_optional(session_dir / "notes.md")
