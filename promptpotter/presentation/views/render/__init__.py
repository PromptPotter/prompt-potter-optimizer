"""Render targets — terminal ANSI display (``to_text`` / ``render_sp_diff``, in ``.text``).

The markdown / heatmap / sweep-summary renderers are the application's emit contract and
live in ``promptpotter.application.views.render``; callers import those from there directly.
"""

from __future__ import annotations

from promptpotter.presentation.views.render.text import to_text

__all__ = ["to_text"]
