"""Markdown box renderer — pure function, used for journal/notes display."""

from __future__ import annotations

from promptpotter.presentation.views.display_primitives import (
    DIM,
    RESET,
    _box_bottom,
    _box_line,
    _box_top,
)

__all__ = ["render_markdown_box"]


def render_markdown_box(title: str, content: str, empty_label: str, *, width: int = 74) -> str:
    """Render a titled box around ``content``, or a dim empty label."""
    if not content:
        return f"  {DIM}{empty_label}{RESET}"
    out = [f"  {_box_top(title, width=width)}"]
    for line in content.split("\n"):
        out.append(f"  {_box_line(line, width=width)}")
    out.append(f"  {_box_bottom(width=width)}")
    return "\n".join(out)
