"""Shared orchestration — wires services together for CLI, notebook, and API.

Eliminates duplicated orchestration logic between entry points.  Each function
accepts a ``log`` callback (default: ``logger.info``) so callers can inject
``print`` (notebook) or ``logger.info`` (CLI) without changing logic.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.models.task_context import TaskContext
from promptpotter.services.campaign.bootstrap import BackendContext
from promptpotter.services.campaign.campaign_data import extract_campaign_baseline
from promptpotter.services.campaign.config import (
    RunConfig,
)
from promptpotter.services.campaign.config import (
    configure_pipeline as _svc_configure_pipeline,
)
from promptpotter.services.campaign.state import RunCallbacks

if TYPE_CHECKING:
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.campaign.optimization_loop import RunResult
    from promptpotter.services.search.scan_results import ScanContext

logger = logging.getLogger(__name__)

__all__ = [
    "build_run_config",
    "configure_and_apply_pipeline",
    "prepare_eval_context",
    "run_optimization",
    "run_scan_and_persist",
]


# ---------------------------------------------------------------------------
# 1. Configure pipeline + apply to session
# ---------------------------------------------------------------------------


def configure_and_apply_pipeline(
    session: BackendContext,
    campaign_config: CampaignConfig,
    *,
    log: Callable[[str], None] = logger.info,
) -> dict:
    """Configure pipeline, apply filtered schema to *session*, log summary.

    Returns ``pipeline_params`` dict ready for evaluation.
    """
    result = _svc_configure_pipeline(
        session.pipeline_schema,
        campaign_config,
        exp_data=getattr(session, "exp_data", None),
    )

    if result.filtered_schema is not None:
        session.pipeline_schema = result.filtered_schema

    nodes_str = ", ".join(result.active_nodes)
    excl_str = f"  Excluded: {', '.join(result.excluded_nodes)}" if result.excluded_nodes else ""
    log(f"Active nodes: {nodes_str}{excl_str}")

    return result.pipeline_params


# ---------------------------------------------------------------------------
# 2. Prepare eval context
# ---------------------------------------------------------------------------


async def prepare_eval_context(
    session: BackendContext,
    train_data: list[dict] | None,
    campaign_config: CampaignConfig | None = None,
    *,
    run_baseline: bool = False,
    pipeline_params: dict | None = None,
    log: Callable[[str], None] = logger.info,
) -> tuple[OptSearchPoint, list[dict], list, list]:
    """Load baseline prompt, build dataset, optionally run baseline eval.

    Returns ``(baseline, dataset, campaign_rounds, baseline_results)``.
    """
    from promptpotter.services.campaign.campaign_data import (
        prepare_eval_context as _svc_prepare,
    )

    baseline, dataset, campaign_rounds, baseline_results = await _svc_prepare(
        session.exp_data,
        train_data,
        campaign_config,
        run_baseline=run_baseline,
        pipeline_params=pipeline_params,
        pipeline_schema=session.pipeline_schema,
        svc=session,
    )

    log(f"Evaluation data: {len(dataset)} queries")
    return baseline, dataset, campaign_rounds, baseline_results


# ---------------------------------------------------------------------------
# 3. Build RunConfig from session
# ---------------------------------------------------------------------------


def build_run_config(
    campaign_config: CampaignConfig,
    session: BackendContext,
    *,
    pipeline_params: dict | None = None,
    scan_context: ScanContext | None = None,
    task_context: TaskContext | dict | None = None,
    session_id: str = "",
) -> RunConfig:
    """Assemble a ``RunConfig`` from *campaign_config* and *session* context."""
    return RunConfig.from_campaign_config(
        campaign_config,
        backend_url=session.backend_client.base_url,
        backend_id=session.backend_id,
        project_root=str(session.store.base_dir),
        pipeline_params=pipeline_params,
        index_terms=session.index_terms,
        session_id=session_id,
        scan_context=scan_context,
        pipeline_schema=session.pipeline_schema,
        task_context=task_context,
    )


# ---------------------------------------------------------------------------
# 4. Run optimization (config + baseline + round-append + loop)
# ---------------------------------------------------------------------------


def _make_round_append_callback(
    campaign_rounds: list,
) -> Callable:
    """Build the round-entry append callback shared by all entry points."""

    def _on_round(round_result: Any, stall_count: int) -> None:
        round_entry = round_result.model_dump()
        ps_raw = round_entry.get("prompt_fields", {})
        round_entry["prompt_fields"] = (
            OptSearchPoint.from_prompt_fields(ps_raw) if isinstance(ps_raw, dict) else ps_raw
        )
        round_entry["round"] = len(campaign_rounds)
        campaign_rounds.append(round_entry)

    return _on_round


async def run_optimization(
    campaign_rounds: list,
    dataset: list,
    campaign_config: CampaignConfig,
    *,
    session: BackendContext,
    pipeline_params: dict | None = None,
    scan_context: ScanContext | None = None,
    experiment_id: str | None = None,
    task_context: TaskContext | dict | None = None,
    session_id: str = "",
    callbacks: RunCallbacks | None = None,
    langfuse_session_id: str | None = None,
    cycle_id: str | None = None,
) -> RunResult | None:
    """Build config, extract baseline, wire round-append callback, and run loop.

    Callers pass display-specific callbacks via *callbacks*; the round-entry
    append is handled internally and chained before the caller's
    ``on_round_complete``.

    Returns ``RunResult`` (or ``None`` if interrupted before any rounds).
    """
    from promptpotter.services.campaign.optimization_loop import (
        run_optimization as _svc_run_optimization,
    )

    config = build_run_config(
        campaign_config,
        session,
        pipeline_params=pipeline_params,
        scan_context=scan_context,
        task_context=task_context,
        session_id=session_id,
    )

    bl = extract_campaign_baseline(campaign_rounds)

    # Chain: round-append fires first, then caller's display callback
    append_cb = _make_round_append_callback(campaign_rounds)
    caller_cb = callbacks or RunCallbacks()

    original_on_round = caller_cb.on_round_complete

    def _chained_on_round(round_result: Any, stall_count: int) -> None:
        append_cb(round_result, stall_count)
        if original_on_round:
            original_on_round(round_result, stall_count)

    merged_cb = RunCallbacks(
        on_round_complete=_chained_on_round,
        on_candidate_eval=caller_cb.on_candidate_eval,
        on_query_eval=caller_cb.on_query_eval,
        on_phase=caller_cb.on_phase,
        on_checkpoint=caller_cb.on_checkpoint,
    )

    return await _svc_run_optimization(
        bl.instruction,
        dataset,
        config,
        baseline_prompt_fields=bl.baseline_ps,
        baseline_accuracy=bl.baseline_acc,
        baseline_results=bl.baseline_results,
        callbacks=merged_cb,
        langfuse_session_id=langfuse_session_id,
        scan_context=scan_context,
        cycle_id=cycle_id,
        experiment_id=experiment_id or "",
        backend_client=session.backend_client,
    )


# ---------------------------------------------------------------------------
# 5. Run sensitivity scan + persist
# ---------------------------------------------------------------------------


async def run_scan_and_persist(
    baseline: OptSearchPoint,
    campaign_config: CampaignConfig,
    scan_variants: dict,
    dataset: list,
    *,
    session: BackendContext,
    scan_sample_size: int = 0,
    experiment_id: str = "",
    session_id: str = "",
    log: Callable[[str], None] = logger.info,
    progress_cb: Callable | None = None,
    on_result: Callable | None = None,
):
    """Decompose scan baseline, run sensitivity scan, persist results.

    Returns ``(scan_baseline_sp, baseline_opt, df, profiles)``.
    """
    from promptpotter.services.campaign.config import create_llm_client
    from promptpotter.services.search.scan_results import (
        decompose_scan_baseline as _decompose_scan_baseline,
    )
    from promptpotter.services.search.sensitivity_scanner import (
        sensitivity_scan as _sensitivity_scan,
    )

    # Configure pipeline (ensures filtered schema is applied)
    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=log)

    # Resolve prompt node from pipeline schema
    prompt_node = ""
    ps = session.pipeline_schema
    if ps:
        active_steps = (pipeline_params or {}).get("steps", [])
        prompt_node = ps.resolve_active_prompt_node(active_steps)

    # Decompose scan baseline
    llm_client, llm_model = create_llm_client(campaign_config)
    result = await _decompose_scan_baseline(
        baseline,
        campaign_config,
        llm_client,
        llm_model,
        pipeline_params=pipeline_params,
        session=session,
        scan_variants=scan_variants,
        prompt_node=prompt_node,
        pipeline_schema=ps,
    )
    scan_baseline_sp = result.baseline_jsp
    baseline_opt = result.search_baseline

    # Init backend session
    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)

    # Run scan
    log(f"Running sensitivity scan ({len(scan_variants)} axes) ...")
    scan_kwargs: dict[str, Any] = {
        "sample_size": scan_sample_size,
        "pipeline_schema": ps,
        "experiment_id": experiment_id,
    }
    if progress_cb is not None:
        scan_kwargs["progress_cb"] = progress_cb
    if on_result is not None:
        scan_kwargs["on_result"] = on_result

    df, profiles = await _sensitivity_scan(
        scan_baseline_sp,
        scan_variants,
        dataset,
        session,
        baseline_opt=baseline_opt,
        **scan_kwargs,
    )

    if df is None or (hasattr(df, "empty") and df.empty):
        log("Scan returned no results")
        return scan_baseline_sp, baseline_opt, None, []

    log(f"Sensitivity scan complete: {len(df)} variants evaluated")

    # Persist results
    if session_id and session.store and session.backend_id:
        session.store.sessions.save_scan_results(
            session.backend_id,
            session_id,
            df.to_dict(orient="records"),
            profiles,
        )
        log(f"Scan results persisted to session {session_id}")

    return scan_baseline_sp, baseline_opt, df, profiles
