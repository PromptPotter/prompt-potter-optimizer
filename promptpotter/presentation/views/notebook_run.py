"""Notebook-only orchestration — needs ``print``, can't live in application/."""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING, Any

from promptpotter.application.bootstrap.session import Session
from promptpotter.application.bootstrap.wiring import init_services as _init_services
from promptpotter.application.jobs.mint import prepare_fresh_cycle
from promptpotter.application.origin import (
    CampaignOrigin,
)
from promptpotter.application.origin import (
    prepare_scoring_context as _prepare_scoring_context,
)
from promptpotter.application.run_observers import (
    RunObservers,
    build_run_observers,
)
from promptpotter.application.runner.entry import run_optimization as _run_optimization
from promptpotter.application.scoring.formula import split_scoring_block
from promptpotter.config.logging import setup_logging
from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
)
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import CycleResult
from promptpotter.infrastructure.tracing.langfuse_client import langfuse_trace_url
from promptpotter.presentation.views.display import (
    BOLD,
    GREEN,
    RESET,
    YELLOW,
    _dbox_block,
    render_pipeline_overrides,
)
from promptpotter.presentation.views.live.display import LiveDisplay

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import TaskDecomposition

__all__ = [
    "init_notebook_session",
    "prepare_origin_notebook",
    "render_completion",
    "render_completion_html",
    "run_optimization_notebook",
]


def render_completion_html(result: CycleResult) -> str:
    """HTML render of the final-winner summary; ``""`` when no winner."""
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
    """Render ``html_body`` inline inside IPython; ``False`` outside."""
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


def render_completion(
    result: CycleResult,
    *,
    best_round: dict[str, Any],
    pipeline_schema: PipelineSchema | None = None,
    dataset_name: str | None = None,
    campaign_id: str | None = None,
) -> str:
    """Render the closing summary box (paused vs complete) + pipeline overrides."""
    paused = result.stop_reason == StopReason.PAUSED
    title = (
        f"{YELLOW}{BOLD}PAUSED{RESET} — resumable (re-run resume to continue)"
        if paused
        else f"{GREEN}{BOLD}OPTIMIZATION COMPLETE{RESET}"
    )

    fields: list[str] = [
        f"Rounds       {result.n_l1_rounds:<15d}"
        f"Best         {best_round['accuracy']:.1%} (round {best_round['round']})",
        f"Stop reason  {result.stop_reason}",
    ]
    if paused:
        fields.append("Resume: re-run this cell -- rounds auto-restore")
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


async def init_notebook_session(
    dataset_name: str,
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
) -> Session:
    """Init store, client, pipeline schema, scoring data — with notebook-friendly logging."""
    setup_logging()
    session = await _init_services(
        dataset_name=dataset_name,
        backend_url=backend_url,
        backend_id=backend_id,
        on_status=print,
    )
    print(f"Dataset    : {dataset_name} ({len(session.samples)} queries)")
    print(f"Session terms: {len(session.index_terms)}")
    return session


async def prepare_origin_notebook(
    session: Session,
    train_data: list[Sample],
    campaign_config: CampaignConfig,
    *,
    pipeline_params: dict[str, Any] | None = None,
) -> tuple[RunObservers, list[Sample], CampaignOrigin]:
    """Mint the campaign, build observers, score the origin — one notebook cell.

    Mints through ``prepare_fresh_cycle``, the same prologue ``new`` and the web mint
    run. ``build_run_observers`` used to auto-mint for this caller alone, on a second
    code path that skipped the pipeline overlay and the cycle seed."""
    scoring_formula = split_scoring_block(campaign_config.scoring).per_sample
    display = LiveDisplay(
        origin_acc=0.0,
        l1_patience=campaign_config.optimization.l1_patience,
        pipeline_schema=session.pipeline_schema,
        scoring_formula=scoring_formula,
    )

    if not session.campaign_id:
        prepare_fresh_cycle(session, campaign_config, train_data)

    observers = build_run_observers(
        session=session,
        campaign_config=campaign_config,
        dataset=train_data,
        display=display,
    )

    origin, dataset = await _prepare_scoring_context(
        train_data,
        campaign_config,
        pipeline_params=pipeline_params,
        pipeline_schema=session.pipeline_schema,
        svc=session,
        listener=observers.callbacks,
    )
    if hasattr(display, "set_origin"):
        display.set_origin(origin.report.accuracy)

    print(f"\nEvaluation data: {len(dataset)} queries  |  Origin: {origin.report.accuracy:.1%}")
    return observers, dataset, origin


async def run_optimization_notebook(
    observers: RunObservers,
    dataset: list[Sample],
    origin: CampaignOrigin,
    campaign_config: CampaignConfig,
    *,
    session: Session,
    langfuse_session_id: str | None = None,
    task_context: TaskDecomposition | dict[str, Any] | None = None,
) -> CycleResult | None:
    """Run optimization with the observers built in the prior cell."""
    print(f"  {YELLOW}Interrupt of cells can take up to 60 seconds!{RESET}")
    print(f"  {YELLOW}If a dialog pops up, click 'Cancel' and wait 20 seconds.{RESET}")

    result = await _run_optimization(
        dataset,
        campaign_config,
        session=session,
        observers=observers,
        origin=origin,
        task_context=task_context,
        langfuse_session_id=langfuse_session_id,
    )

    if result is None or not result.rounds:
        print(f"\n{YELLOW}{BOLD}[PAUSED]{RESET} Feedback cycle ended before any rounds completed.")
        print("  Resume: re-run this cell to restart.")
        return result

    best = max((r.model_dump() for r in result.rounds), key=lambda r: r["accuracy"])
    print(
        render_completion(
            result,
            best_round=best,
            pipeline_schema=session.pipeline_schema,
            dataset_name=session.dataset_name,
            campaign_id=session.campaign_id or None,
        )
    )
    _try_display_html(render_completion_html(result))

    if result.stop_reason == StopReason.PAUSED:
        print(
            f"  {YELLOW}{BOLD}[PAUSED]{RESET} after {result.n_l1_rounds} rounds — "
            "artifacts saved, cycle resumable. Caller decides whether to continue."
        )

    return result
