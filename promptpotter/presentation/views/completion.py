"""Render the final box printed after a feedback cycle finishes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.presentation.views.display_primitives import (
    BOLD,
    GREEN,
    RESET,
    YELLOW,
    _dbox_block,
)
from promptpotter.presentation.views.formatting import render_pipeline_overrides

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RunResult
    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = ["render_completion"]


def render_completion(
    result: RunResult,
    *,
    best_round: dict,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Render the closing summary box (interrupted vs complete) + pipeline overrides."""
    interrupted = result.stop_reason == "interrupted"
    title = (
        f"{YELLOW}{BOLD}INTERRUPTED{RESET} — stopped by user"
        if interrupted
        else f"{GREEN}{BOLD}OPTIMIZATION COMPLETE{RESET}"
    )

    fields: list[str] = [
        f"Rounds       {result.n_rounds:<15d}"
        f"Best         {best_round['accuracy']:.1%} (round {best_round['round']})",
        f"Stop reason  {result.stop_reason}",
    ]
    if interrupted:
        fields.append("Resume: re-run this cell -- rounds auto-restore")
    if result.cycle_id:
        fields.append(f"Cycle ID     {result.cycle_id}")
    if result.session_id:
        fields.append(f"Session      {result.session_id}")
    if result.langfuse_trace_id:
        fields.append(f"Langfuse     {result.langfuse_trace_id}")

    out = ["", _dbox_block(title, *fields)]

    if overrides_block := render_pipeline_overrides(result.winner_pipeline_params, pipeline_schema):
        out.append("")
        out.append(overrides_block)

    return "\n".join(out)
