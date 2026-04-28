"""Notebook-only orchestration — needs ``print``, can't live in application/."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.application.campaign.campaign_setup import (
    Session,
)
from promptpotter.application.campaign.campaign_setup import (
    init_services as _init_services,
)
from promptpotter.application.campaign.data import (
    extract_campaign_baseline,
)
from promptpotter.application.campaign.data import (
    prepare_scoring_context as _prepare_scoring_context,
)
from promptpotter.application.campaign.runner import (
    run_optimization as _run_optimization,
)
from promptpotter.application.scoring.formula import split_scoring_block
from promptpotter.config.logging import setup_logging
from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.domain.results import RunResult
from promptpotter.infrastructure.tracing import ObservabilityBridge
from promptpotter.presentation.views.display_primitives import BOLD, RESET, YELLOW
from promptpotter.presentation.views.live import LiveDisplay
from promptpotter.presentation.views.reports import render_completion

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.search_point import TaskDecomposition

__all__ = [
    "init_notebook_session",
    "prepare_scoring_context_notebook",
    "run_optimization_notebook",
]


async def init_notebook_session(
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    dataset_name: str | None = None,
    take_over: bool = False,
) -> Session:
    """Init store, client, pipeline schema, scoring data — with notebook-friendly logging."""
    setup_logging()
    # views/notebook_run.py → views → presentation → promptpotter → repo_root
    project_root = Path(__file__).resolve().parent.parent.parent.parent

    session = await _init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        on_status=print,
        take_over=take_over,
    )

    if dataset_name and session.queries:
        print(f"Dataset    : {dataset_name} ({len(session.queries)} queries)")
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
    print(f"Queries    : {len(session.queries)}  |  Session terms: {len(session.index_terms)}")
    return session


async def prepare_scoring_context_notebook(
    session: Session,
    train_data: list | None,
    campaign_config: CampaignConfig | None = None,
    pipeline_params: dict | None = None,
) -> tuple[OptSearchPoint, list, list, list]:
    """Notebook variant of ``prepare_scoring_context`` — adds tqdm + per-query lines via ``LiveDisplay``."""
    scoring_formula: str | None = (
        split_scoring_block(campaign_config.scoring).per_query if campaign_config else None
    )
    n_samples = len(train_data) if train_data else 0
    baseline_display = LiveDisplay(
        baseline_acc=0.0,
        l1_patience=campaign_config.optimization.l1_patience if campaign_config else 1,
        pipeline_schema=session.pipeline_schema,
        scoring_formula=scoring_formula,
        sp_budget_ttest=n_samples or 1,
    )

    obs = ObservabilityBridge.file_only(session.store.base_dir, session.backend_id)

    baseline, dataset, campaign_rounds, baseline_results = await _prepare_scoring_context(
        session.experiment_extract,
        train_data,
        campaign_config,
        pipeline_params=pipeline_params,
        pipeline_schema=session.pipeline_schema,
        svc=session,
        listener=baseline_display,
        obs=obs,
    )

    print(f"\nEvaluation data: {len(dataset)} queries")
    return baseline, dataset, campaign_rounds, baseline_results


async def run_optimization_notebook(
    campaign_rounds: list,
    dataset: list,
    campaign_config: CampaignConfig,
    *,
    session: Session,
    langfuse_session_id: str | None = None,
    experiment_id: str | None = None,
    task_context: TaskDecomposition | dict | None = None,
    session_id: str = "",
) -> tuple[list, RunResult | None]:
    """Run optimization with ``LiveDisplay``. Prints final completion box."""
    from promptpotter.application.campaign.cycle_store import resolve_campaign_id

    bl = extract_campaign_baseline(campaign_rounds)
    baseline_acc = bl.baseline_acc

    display = LiveDisplay(
        campaign_rounds=campaign_rounds,
        baseline_acc=baseline_acc,
        l1_patience=campaign_config.optimization.l1_patience,
        pipeline_schema=session.pipeline_schema,
        store=session.store,
        scoring_formula=split_scoring_block(
            campaign_config.scoring if campaign_config else None
        ).per_query,
    )

    resolved_cycle_id: str | None = None
    if experiment_id and session.store:
        resolved_cycle_id = resolve_campaign_id(session.store, session.backend_id, experiment_id)
        if resolved_cycle_id is None:
            print(f"  No campaign matching '{experiment_id}' — starting fresh")
        else:
            stored = session.store.campaigns.load(session.backend_id, resolved_cycle_id)
            if stored:
                stored_bl = stored.get("baseline_accuracy")
                if stored_bl is not None and stored_bl != baseline_acc:
                    print(
                        f"  {YELLOW}Using stored baseline {stored_bl:.1%}"
                        f" (notebook had {baseline_acc:.1%}){RESET}"
                    )
                    baseline_acc = stored_bl

    print(f"  {YELLOW}Interrupt of cells can take up to 60 seconds!{RESET}")
    print(f"  {YELLOW}If a dialog pops up, click 'Cancel' and wait 20 seconds.{RESET}")

    result = await _run_optimization(
        dataset,
        campaign_config,
        baseline=bl,
        session=session,
        experiment_id=experiment_id,
        task_context=task_context,
        session_id=session_id,
        display=display,
        langfuse_session_id=langfuse_session_id,
        cycle_id=resolved_cycle_id,
    )

    if not campaign_rounds:
        print(
            f"\n{YELLOW}{BOLD}[INTERRUPTED]{RESET} Feedback cycle "
            f"stopped before any rounds completed."
        )
        print("  Resume: re-run this cell to restart.")
        raise KeyboardInterrupt("optimization interrupted before any rounds completed")

    if result is None:
        return campaign_rounds, result

    best = max(campaign_rounds, key=lambda r: r["accuracy"])
    print(render_completion(result, best_round=best, pipeline_schema=session.pipeline_schema))

    if result.stop_reason == "interrupted":
        raise KeyboardInterrupt(f"optimization interrupted after {result.n_rounds} rounds")

    return campaign_rounds, result
