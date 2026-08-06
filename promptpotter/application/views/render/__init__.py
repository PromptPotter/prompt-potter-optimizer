from __future__ import annotations

from promptpotter.application.views.render.heatmap import render_hard_sample_heatmap
from promptpotter.application.views.render.markdown import to_markdown
from promptpotter.application.views.view_models import SweepSummaryView


def render_sweep_summary(view: SweepSummaryView) -> str:
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


__all__ = [
    "SweepSummaryView",
    "render_hard_sample_heatmap",
    "render_sweep_summary",
    "to_markdown",
]
