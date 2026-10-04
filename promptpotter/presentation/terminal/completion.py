"""What a terminal or a notebook cell does with a finished cycle. The launch half is
``application/embedded_run.py``; this is the read-out of what it returned."""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING, assert_never, get_args

from promptpotter.application.views.render.primitives import (
    BOLD,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _dbox_block,
    render_pipeline_overlay,
)
from promptpotter.domain.bench import BenchColumn
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    StopOutcome,
    stop_reason_outcome,
)
from promptpotter.infrastructure.tracing.langfuse_client import langfuse_trace_url

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.bench import BenchReading, BenchScore
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
    # The OUTCOME, never the member: `StopOutcome.PAUSED` is the one non-terminal class, and a
    # second reason in it (a panel the bounds cut) read as COMPLETE against a name comparison.
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
    # First, because it is the headline: the selection graded on rows it never read. `Selected`
    # below is the optimizer's own reading on the rows that chose it.
    if (bench := result.bench) is not None:
        fields.append(f"Bench        {_bench_text(bench)}")
    fields += [headline, f"Stop reason  {info.label}"]
    if result.error is not None:
        fields.append(f"Error        {result.error.kind}: {result.error.message}")
    if result.spend is not None:
        fields.append(f"Spend        {result.spend.billed_beside_incurred()}")
    # The reason's OWN next step, off the one table, so the terminal advises what `log.md`,
    # `review.md` and the browser advise; `""` is a stated answer and prints nothing.
    if info.next_step:
        fields.append(f"Next         {info.next_step}")
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
    if overlay_block := render_pipeline_overlay(result.result_pipeline_params, pipeline_schema):
        out.append("")
        out.append(overlay_block)
    return "\n".join(out)


def _bench_text(bench: BenchScore) -> str:
    def _level(reading: BenchReading | None) -> str:
        level = None if reading is None else reading.level
        return "—" if level is None else f"{level.value:.3f}"

    lift = "—" if bench.headline_lift is None else f"{bench.headline_lift.value:+.3f}"
    for column in get_args(BenchColumn):
        beside = bench.lift.of(column)
        if column != bench.headline and beside is not None:
            lift += f" ({column} {beside.value:+.3f})"
    selected = bench.selected
    missing = "" if bench.missing_reason is None else f" · missing: {bench.missing_reason}"
    return (
        f"{bench.headline} {_level(selected)} selected"
        f"{'' if selected is None else f' (round {selected.round})'} · "
        f"{_level(bench.origin)} origin · lift {lift} · "
        f"{bench.bench_size} held-out rows{missing}"
    )


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
    """Print the box, and render the winner inline when the caller is a notebook."""
    # Only a pause is resumable-with-nothing-to-show; a cycle refused or crashed at run init
    # also holds no round, and its box names the cause.
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
            campaign_id=session.campaign_id or None,
        )
    )
    _try_display_html(render_completion_html(result))
