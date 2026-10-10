from __future__ import annotations

import html
import json
import textwrap
from typing import TYPE_CHECKING, assert_never

from promptpotter.application.views.render.primitives import (
    BOLD,
    BOX_WIDTH,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _dbox_block,
    render_pipeline_overlay,
)
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    StopOutcome,
    stop_reason_outcome,
)
from promptpotter.infrastructure.tracing.langfuse_client import langfuse_trace_url

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import CycleResult

__all__ = ["render_completion", "render_completion_html", "report_completion"]


def render_completion(
    result: CycleResult,
    *,
    pipeline_schema: PipelineSchema | None = None,
    dataset_name: str | None = None,
    campaign_dir: Path | None = None,
) -> str:
    # The outcome, never the member: more than one stop reason is PAUSED.
    info = STOP_REASON_INFO[result.stop_reason]
    match info.outcome:
        case StopOutcome.PAUSED:
            title = f"{YELLOW}{BOLD}PAUSED{RESET} — resumable"
        case StopOutcome.FAILED:
            title = f"{RED}{BOLD}{info.label.upper()}{RESET} — no result"
        case StopOutcome.HALTED:
            title = f"{YELLOW}{BOLD}HALTED{RESET} — {info.label}; best so far kept"
        case StopOutcome.SUCCESS:
            title = f"{GREEN}{BOLD}OPTIMIZATION COMPLETE{RESET}"
        case _:
            assert_never(info.outcome)

    headline = f"Rounds       {result.n_rounds_after_origin:<15d}"
    if result.result_accuracy is not None:
        headline += f"Selected     {result.result_accuracy:.1%} (round {result.result_round})"
    fields: list[str] = []
    fields += [f"Bench        {result.bench.line}", headline, f"Stop reason  {info.label}"]
    if result.error is not None:
        fields.append(f"Error        {result.error.kind}: {result.error.message}")
    if result.spend is not None:
        fields.append(f"Spend        {result.spend.billed_beside_incurred()}")
    if info.next_step:
        fields.append(f"Next         {info.next_step}")
    if dataset_name:
        fields.append(f"Dataset      {dataset_name}")
    if campaign_dir is not None:
        fields.append(f"Campaign     {campaign_dir.name}")
    if result.cycle_id:
        fields.append(f"Cycle ID     {result.cycle_id}")
    if trace_url := langfuse_trace_url(result.langfuse_trace_id):
        fields.append(f"Langfuse     {trace_url}")

    wrapped = [
        line
        for field in fields
        for line in textwrap.wrap(field, width=BOX_WIDTH - 4, subsequent_indent=" " * 13)
    ]
    out = ["", _dbox_block(title, *wrapped)]
    if overlay_block := render_pipeline_overlay(result.result_pipeline_params, pipeline_schema):
        out.append("")
        out.append(overlay_block)
    if campaign_dir is not None:
        out += [
            "",
            f"Directory: {campaign_dir}",
            "  campaign.json          — manifest",
            "  log.md                 — campaign digest",
            f"  cycles/{result.cycle_id or '?'}/  — session telemetry (dashboard.json) + rounds"
            " + readout.log",
        ]
    return "\n".join(out)


def render_completion_html(result: CycleResult) -> str:
    if not result.result_prompt_fields:
        return ""
    prompt_json = html.escape(
        json.dumps(dict(result.result_prompt_fields), indent=2, ensure_ascii=False, default=str)
    )
    pp_json = html.escape(
        json.dumps(
            dict(result.result_pipeline_params or {}), indent=2, ensure_ascii=False, default=str
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
    # Pause only: a cycle refused or crashed at run init also holds no round, and its box says why.
    if not result.rounds and stop_reason_outcome(result.stop_reason) is StopOutcome.PAUSED:
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
            campaign_dir=(
                session.store.campaigns.campaign_root_dir(session.campaign_id)
                if session.campaign_id
                else None
            ),
        )
    )
    _try_display_html(render_completion_html(result))
