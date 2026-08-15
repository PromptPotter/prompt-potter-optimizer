"""What a terminal or a notebook cell does with a finished cycle. The launch half is
``application/embedded_run.py``; this is the read-out of what it returned."""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING

from promptpotter.domain.phases import StopReason
from promptpotter.infrastructure.tracing.langfuse_client import langfuse_trace_url
from promptpotter.presentation.views.display import (
    BOLD,
    GREEN,
    RESET,
    YELLOW,
    _dbox_block,
    render_pipeline_overrides,
)

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import CycleResult

__all__ = ["render_completion", "render_completion_html", "report_completion"]


def render_completion(
    result: CycleResult,
    *,
    pipeline_schema: PipelineSchema | None = None,
    dataset_name: str | None = None,
    campaign_id: str | None = None,
) -> str:
    paused = result.stop_reason == StopReason.PAUSED
    title = (
        f"{YELLOW}{BOLD}PAUSED{RESET} — resumable"
        if paused
        else f"{GREEN}{BOLD}OPTIMIZATION COMPLETE{RESET}"
    )

    # The best round is derived here rather than taken as an argument: every caller computed the
    # same `max` over `result.rounds`, and one of them keyed it off a `model_dump()` dict.
    best = max(result.rounds, key=lambda r: r.accuracy, default=None)
    headline = f"Rounds       {result.n_l1_rounds:<15d}"
    if best is not None:
        headline += f"Best         {best.accuracy:.1%} (round {best.round})"
    fields: list[str] = [headline, f"Stop reason  {result.stop_reason}"]
    if paused:
        fields.append("Resume: python -m promptpotter resume")
    if dataset_name:
        fields.append(f"Dataset      {dataset_name}")
    if campaign_id:
        fields.append(f"Campaign     {campaign_id}")
    if result.cycle_id:
        fields.append(f"Cycle ID     {result.cycle_id}")
    if result.session_id:
        fields.append(f"Session      {result.session_id}")
    if trace_url := langfuse_trace_url(result.langfuse_trace_id):
        fields.append(f"Langfuse     {trace_url}")

    out = ["", _dbox_block(title, *fields)]
    if overrides_block := render_pipeline_overrides(result.winner_pipeline_params, pipeline_schema):
        out.append("")
        out.append(overrides_block)
    return "\n".join(out)


def render_completion_html(result: CycleResult) -> str:
    if not result.winner_prompt_fields:
        return ""
    prompt_json = html.escape(
        json.dumps(dict(result.winner_prompt_fields), indent=2, ensure_ascii=False, default=str)
    )
    pp_json = html.escape(
        json.dumps(
            dict(result.winner_pipeline_params or {}), indent=2, ensure_ascii=False, default=str
        )
    )
    return (
        "<div style='font-family:monospace'>"
        "<h3 style='margin:8px 0 4px'>Final Winner</h3>"
        "<details><summary>Prompt fields</summary>"
        f"<pre>{prompt_json}</pre></details>"
        "<details><summary>Pipeline params</summary>"
        f"<pre>{pp_json}</pre></details></div>"
    )


def _try_display_html(html_body: str) -> bool:
    if not html_body:
        return False
    try:
        from IPython import get_ipython
        from IPython.display import HTML, display
    except ImportError:
        return False
    if get_ipython() is None:  # type: ignore[no-untyped-call, unused-ignore]
        return False
    display(HTML(html_body))  # type: ignore[no-untyped-call, unused-ignore]
    return True


def report_completion(result: CycleResult, *, session: Session) -> None:
    """Print the box, and render the winner inline when the caller is a notebook."""
    if not result.rounds:
        print(
            f"\n{YELLOW}{BOLD}[PAUSED]{RESET} Cycle ended before any rounds completed — "
            "resume with `python -m promptpotter resume`."
        )
        return
    print(
        render_completion(
            result,
            pipeline_schema=session.pipeline_schema,
            dataset_name=session.dataset_name,
            campaign_id=session.campaign_id or None,
        )
    )
    _try_display_html(render_completion_html(result))
