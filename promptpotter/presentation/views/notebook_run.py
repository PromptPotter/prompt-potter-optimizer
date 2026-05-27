"""Notebook-only orchestration — needs ``print``, can't live in application/."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.bootstrap import (
    init_services as _init_services,
)
from promptpotter.application.bootstrap.session import Session
from promptpotter.application.origin import (
    CampaignOrigin,
    extract_campaign_origin,
)
from promptpotter.application.origin import (
    prepare_scoring_context as _prepare_scoring_context,
)
from promptpotter.application.run_observers import (
    RunObservers,
    build_run_observers,
)
from promptpotter.application.runner import (
    run_optimization as _run_optimization,
)
from promptpotter.application.scoring.formula import split_scoring_block
from promptpotter.config.logging import setup_logging
from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.domain.results import CycleResult
from promptpotter.presentation.views.display import (
    BOLD,
    GREEN,
    RESET,
    YELLOW,
    _dbox_block,
    render_pipeline_overrides,
)
from promptpotter.presentation.views.live import LiveDisplay

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
    if dataset_name:
        fields.append(f"Dataset      {dataset_name}")
    if campaign_id:
        fields.append(f"Campaign     {campaign_id}")
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


async def init_notebook_session(
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    dataset_name: str | None = None,
) -> Session:
    """Init store, client, pipeline schema, scoring data — with notebook-friendly logging."""
    setup_logging()
    project_root = Path(__file__).resolve().parent.parent.parent.parent

    session = await _init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        on_status=print,
    )

    if dataset_name and session.samples:
        print(f"Dataset    : {dataset_name} ({len(session.samples)} queries)")
        print(f"Session terms: {len(session.index_terms)}")
        return session

    if not session.experiment_extract:
        print(
            "WARNING: No experiment data available. "
            "Use prepare_datasets() to load from Excel, "
            "or sync from backend."
        )
        return session

    mappings = session.experiment_extract.get("mappings", [])
    verified = sum(1 for m in mappings if m.get("dataset_entry", "").strip() not in ("", "--"))
    print(
        f"Experiment : {session.experiment_extract.get('experiment', {}).get('name', experiment_id)}"
    )
    print(f"Mappings   : {len(mappings)} total, {verified} with verified ground truth")
    print(f"Samples    : {len(session.samples)}  |  Session terms: {len(session.index_terms)}")
    return session


async def prepare_origin_notebook(
    session: Session,
    train_data: list[Sample],
    campaign_config: CampaignConfig,
    *,
    pipeline_params: dict[str, Any] | None = None,
    experiment_id: str | None = None,
) -> tuple[RunObservers, list[Sample], CampaignOrigin]:
    """Build observers + score origin in a separate notebook cell; auto-mints session + cycle."""
    scoring_formula = split_scoring_block(campaign_config.scoring).per_sample
    display = LiveDisplay(
        origin_acc=0.0,
        l1_patience=campaign_config.optimization.l1_patience,
        pipeline_schema=session.pipeline_schema,
        scoring_formula=scoring_formula,
    )

    observers = build_run_observers(
        session=session,
        campaign_config=campaign_config,
        dataset=train_data,
        display=display,
        experiment_id=experiment_id,
    )

    _origin_osp, dataset, campaign_rounds, _results = await _prepare_scoring_context(
        session.experiment_extract,
        train_data,
        campaign_config,
        pipeline_params=pipeline_params,
        pipeline_schema=session.pipeline_schema,
        svc=session,
        listener=observers.callbacks,
    )
    origin = extract_campaign_origin(campaign_rounds)
    if hasattr(display, "set_origin"):
        display.set_origin(origin.origin_acc)

    print(f"\nEvaluation data: {len(dataset)} queries  |  Origin: {origin.origin_acc:.1%}")
    return observers, dataset, origin


async def run_optimization_notebook(
    observers: RunObservers,
    dataset: list[Sample],
    origin: CampaignOrigin,
    campaign_config: CampaignConfig,
    *,
    session: Session,
    langfuse_session_id: str | None = None,
    experiment_id: str | None = None,
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
        experiment_id=experiment_id,
        task_context=task_context,
        langfuse_session_id=langfuse_session_id,
    )

    if result is None or not result.rounds:
        print(
            f"\n{YELLOW}{BOLD}[INTERRUPTED]{RESET} Feedback cycle "
            f"stopped before any rounds completed."
        )
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

    if result.stop_reason == "interrupted":
        print(
            f"  {YELLOW}{BOLD}[INTERRUPTED]{RESET} after {result.n_rounds} rounds — "
            "artifacts saved. Caller decides whether to continue downstream phases."
        )

    return result
