"""Render targets — typed View → ANSI text or markdown.

Two stateless dispatch entry points + two standalone renderers:

* :func:`to_text` — the live CLI/notebook path; ANSI primitives.
* :func:`to_markdown` — the per-cycle digest written to ``log.md``.
* :func:`render_hard_sample_heatmap` — plain-text heatmap (exposed for
  the standalone CLI hard-sample run; also used inside ``to_markdown``).
* :func:`render_sweep_summary` — sweep batch markdown.

Module map:

* :mod:`text` — text dispatch + per-view ANSI renderers
* :mod:`markdown` — ``to_markdown`` + per-round / forks / hard-samples
* :mod:`heatmap` — ``render_hard_sample_heatmap``
* :mod:`sweep` — ``render_sweep_summary``
* :mod:`sp_diff` — SearchPoint diff helper (used by text)
"""

from __future__ import annotations

from promptpotter.presentation.views.render.heatmap import render_hard_sample_heatmap
from promptpotter.presentation.views.render.markdown import to_markdown
from promptpotter.presentation.views.render.sweep import render_sweep_summary
from promptpotter.presentation.views.render.text import to_text

__all__ = ["render_hard_sample_heatmap", "render_sweep_summary", "to_markdown", "to_text"]
