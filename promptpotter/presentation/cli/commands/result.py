from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from promptpotter.domain.said import Said


def said_text(row: Said) -> str:
    """A served row as one terminal line: every part it carries, worded as served."""
    text = row.label if row.value is None else f"{row.label} {row.value}"
    text = text if row.sub is None else f"{text} ({row.sub})"
    return text if row.detail is None else f"{text} — {row.detail}"


@dataclass
class CommandResult:
    data: dict[str, Any] | None = None
    human: str | None = None
    # What the process exits: all a shell, a deploy script or a spawning server reads.
    exit_code: int = 0
