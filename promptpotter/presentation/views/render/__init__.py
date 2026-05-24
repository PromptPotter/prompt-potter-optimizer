"""Render targets — typed View → ANSI text or markdown."""

from __future__ import annotations

from promptpotter.presentation.views.render.heatmap import render_hard_sample_heatmap
from promptpotter.presentation.views.render.markdown import to_markdown
from promptpotter.presentation.views.render.text import to_text
from promptpotter.presentation.views.view_models import SweepSummaryView


def render_sweep_summary(view: SweepSummaryView) -> str:
    """Markdown summary for a sweep batch — header + payload table."""
    lines = [
        f"# Sweep batch {view.batch_id}",
        "",
        f"- Parent cycle: `{view.parent_cycle_id}`",
        f"- Family root: `{view.family_root}`",
        f"- Started: {view.started_at}",
        f"- Completed: {view.completed_at}",
        f"- Forks minted: {view.n_minted} of {view.n_payloads}",
        "",
        "## Payloads",
        "",
        "| Source | Status | Cycle |",
        "|---|---|---|",
    ]
    for row in view.payloads:
        lines.append(f"| `{row.source_file}` | {row.status} | `{row.cycle_id}` |")
    return "\n".join(lines) + "\n"


__all__ = ["render_hard_sample_heatmap", "render_sweep_summary", "to_markdown", "to_text"]
