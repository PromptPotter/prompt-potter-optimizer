"""Render targets — ANSI text (terminal) here; markdown lives one layer down.

``to_text`` / ``render_sp_diff`` are genuinely-terminal display. The markdown +
heatmap + sweep-summary renderers are part of the application's emit contract
and live in ``promptpotter.application.views.render``; they're re-exported here
(allowed up-direction) so terminal callers keep one import surface.
"""

from __future__ import annotations

from promptpotter.application.views.render import (
    render_hard_sample_heatmap,
    render_sweep_summary,
    to_markdown,
)
from promptpotter.presentation.views.render.text import to_text

__all__ = ["render_hard_sample_heatmap", "render_sweep_summary", "to_markdown", "to_text"]
