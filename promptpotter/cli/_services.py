"""CLI service helpers — call promptpotter.services directly, no notebook dependency.

Each function here replaces a notebook wrapper that mixed service logic with
IPython/print display.  CLI versions use logging for status and return data
for the command to format however it likes (text, JSON, etc.).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)

if TYPE_CHECKING:
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.campaign.init import BackendSession
    from promptpotter.services.campaign.state import RunResult
    from promptpotter.services.search.scan_results import ScanContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Init & pipeline
# ---------------------------------------------------------------------------


async def init_services(
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    dataset_name: str | None = None,
) -> BackendSession:
    """Initialize services (logging + service init)."""
    from promptpotter.config.logging import setup_logging
    from promptpotter.services.campaign.init import init_services as _init_services

    setup_logging()

    project_root = Path(__file__).resolve().parent.parent.parent

    return await _init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        on_status=logger.info,
    )


def configure_pipeline(
    session: BackendSession,
    campaign_config: CampaignConfig,
) -> dict:
    """Configure pipeline and filter schema.  Returns pipeline_params."""
    from promptpotter.services.campaign.config import (
        configure_pipeline as _configure_pipeline,
    )

    result = _configure_pipeline(
        session.pipeline_schema,
        campaign_config,
        exp_data=getattr(session, "exp_data", None),
    )

    # Filter schema to active nodes only
    if session.pipeline_schema and result.excluded_nodes:
        session.pipeline_schema = session.pipeline_schema.filter_to_steps(
            result.active_nodes,
        )

    nodes_str = ", ".join(result.active_nodes)
    excl_str = f" (excluded: {', '.join(result.excluded_nodes)})" if result.excluded_nodes else ""
    logger.info("Active nodes: %s%s", nodes_str, excl_str)

    return result.pipeline_params


async def show_pipeline_snapshot(session: BackendSession) -> dict:
    """Fetch and log pipeline config.  Returns raw config dict."""
    import httpx

    try:
        pipeline_raw = await session.backend_client.fetch_pipeline()
    except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
        logger.warning("Backend unreachable: %s", exc)
        return {}

    config = pipeline_raw.get("data", pipeline_raw)
    name = config.get("name", "?")
    version = config.get("version", "?")
    nodes = list(config.get("nodes", {}).keys())
    logger.info("Pipeline: %s %s (%d nodes: %s)", name, version, len(nodes), nodes)
    return config


# ---------------------------------------------------------------------------
# Datasets & eval context
# ---------------------------------------------------------------------------


def prepare_datasets(
    store,
    backend_id: str,
    excel_path: str | None = None,
    *,
    force: bool = False,
) -> tuple[list[dict] | None, list[str]]:
    """Load datasets and return (train_data, index_terms)."""
    from promptpotter.services.campaign.init import (
        prepare_datasets as _prepare_datasets,
    )

    result = _prepare_datasets(store, backend_id, excel_path, force=force)
    logger.info(
        "Datasets: %d train, %d unique queries, %d session terms",
        len(result.splits.get("train", [])),
        result.n_unique_queries,
        len(result.index_terms),
    )
    return result.train_data, result.index_terms


async def prepare_eval_context(
    session: BackendSession,
    train_data: list[dict] | None,
    campaign_config: CampaignConfig | None = None,
    run_baseline: bool = False,
    pipeline_params: dict | None = None,
):
    """Load baseline + dataset, optionally run baseline eval."""
    from promptpotter.services.campaign.init import (
        prepare_eval_context as _prepare_eval_context,
    )

    baseline, dataset, campaign_rounds, baseline_results = await _prepare_eval_context(
        session.exp_data,
        train_data,
        campaign_config,
        run_baseline=run_baseline,
        pipeline_params=pipeline_params,
        pipeline_schema=session.pipeline_schema,
        svc=session,
    )

    logger.info("Evaluation data: %d queries", len(dataset))
    return baseline, dataset, campaign_rounds, baseline_results


# ---------------------------------------------------------------------------
# Task context
# ---------------------------------------------------------------------------


async def decompose_task_context(
    task_description: str,
    campaign_config: CampaignConfig,
    session: BackendSession,
) -> dict:
    """Decompose task description into structured context fields."""
    from promptpotter.services.campaign.config import create_llm_client
    from promptpotter.services.search.context import (
        decompose_task_context as _decompose_task_context,
    )

    if not task_description:
        return {}

    llm_client, model = create_llm_client(campaign_config)

    result = await _decompose_task_context(
        task_description,
        llm_client,
        model,
        store_base_dir=session.store.base_dir if session.store else None,
        backend_id=session.backend_id,
    )

    cache_tag = " (cached)" if result.was_cached else ""
    logger.info("Task context decomposed%s: %d fields", cache_tag, len(result.task_context))
    return result.task_context


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def resolve_scan_variants(
    scan_variants: dict,
    session: BackendSession,
) -> None:
    """Resolve schema mutation tuples and log the result."""
    from promptpotter.services.search.scan_advisor import resolve_schema_axes
    from promptpotter.shared.constants import PROMPT_STRING_FIELDS

    pipeline_schema = session.pipeline_schema

    flat_for_resolve: dict[str, list] = {}
    for key, spec in scan_variants.items():
        if isinstance(spec, list):
            flat_for_resolve[key] = spec
        elif isinstance(spec, dict):
            for param, vals in spec.items():
                if isinstance(vals, list):
                    flat_for_resolve[param] = vals

    _resolved, _schema_labels = resolve_schema_axes(flat_for_resolve, pipeline_schema)

    # Log summary
    prompt_axes = [k for k in scan_variants if k in PROMPT_STRING_FIELDS]
    node_axes = [k for k in scan_variants if k not in PROMPT_STRING_FIELDS]
    logger.info(
        "Scan variants resolved: %d prompt axes, %d node axes",
        len(prompt_axes),
        len(node_axes),
    )


async def run_sensitivity_scan(
    baseline,
    campaign_config: CampaignConfig,
    scan_variants: dict,
    dataset: list,
    *,
    scan_sample_size: int = 0,
    session: BackendSession | None = None,
    experiment_id: str = "",
    session_id: str = "",
):
    """Prepare baseline, run sensitivity scan, persist results.

    Returns (scan_baseline_sp, scan_df, axis_profiles).
    """
    from promptpotter.services.campaign.config import create_llm_client
    from promptpotter.services.search.scan_baseline import (
        prepare_scan_baseline as _prepare_scan_baseline,
    )
    from promptpotter.services.search.sensitivity_scanner import (
        sensitivity_scan as _sensitivity_scan,
    )

    if session is None:
        raise ValueError("session is required for CLI scan")

    # Prepare scan baseline
    llm_client, llm_model = create_llm_client(campaign_config)
    prompt_node = ""
    pipeline_params = configure_pipeline(session, campaign_config)
    ps = session.pipeline_schema
    if ps:
        active_steps = set((pipeline_params or {}).get("steps", []))
        for name in ps.prompt_node_names():
            if name in active_steps:
                prompt_node = name
                break

    result = await _prepare_scan_baseline(
        baseline,
        campaign_config,
        llm_client,
        llm_model,
        pipeline_params=pipeline_params,
        store=session.store,
        backend_id=session.backend_id,
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
    logger.info("Running sensitivity scan (%d axes) ...", len(scan_variants))
    df, profiles = await _sensitivity_scan(
        scan_baseline_sp,
        scan_variants,
        dataset,
        session.backend_client,
        baseline_opt=baseline_opt,
        sample_size=scan_sample_size,
        store=session.store,
        backend_id=session.backend_id,
        pipeline_schema=ps,
        experiment_id=experiment_id,
    )

    if df is None or (hasattr(df, "empty") and df.empty):
        logger.warning("Scan returned no results")
        return scan_baseline_sp, None, []

    logger.info("Sensitivity scan complete: %d variants evaluated", len(df))

    # Persist scan results
    if session_id and session.store and session.backend_id:
        session.store.sessions.save_scan_results(
            session.backend_id,
            session_id,
            df.to_dict(orient="records"),
            profiles,
        )
        logger.info("Scan results persisted to session %s", session_id)

    return scan_baseline_sp, df, profiles


def seed_campaign_from_scan(
    scan_df,
    axis_profiles: list,
    baseline,
    scan_variants: dict[str, list],
    campaign_rounds: list,
    campaign_config: CampaignConfig,
):
    """Select scan winner and seed campaign_rounds.  Returns best_sp."""
    from promptpotter.services.search.scan_results import (
        seed_campaign_from_scan as _seed_campaign_from_scan,
    )

    if scan_df is None or (hasattr(scan_df, "empty") and scan_df.empty):
        logger.info("No scan data — using baseline as-is")
        return baseline

    result = _seed_campaign_from_scan(
        scan_df,
        axis_profiles,
        baseline,
        scan_variants,
        campaign_rounds,
        campaign_config,
    )

    if result.improving_axes:
        logger.info(
            "Seeded from %d improving axes, best delta=+%.1f%%",
            len(result.improving_axes),
            max(a["best_delta"] for a in result.improving_axes) * 100,
        )
    else:
        logger.info("No improving axes — using baseline")

    return result.best_sp


# ---------------------------------------------------------------------------
# Optimization
# ---------------------------------------------------------------------------


def build_scan_context(
    session: BackendSession,
    state: dict[str, Any],
    campaign_rounds: list,
    pipeline_params: dict | None = None,
) -> ScanContext | None:
    """Build ScanContext from stored scan data (if available)."""
    scan_data = session.store.sessions.load_scan_results(
        session.backend_id,
        state.get("_session_id", ""),
    )
    if not scan_data:
        return None

    import pandas as pd

    from promptpotter.services.search import prepare_scan_context

    scan_df = pd.DataFrame(scan_data["scan_df"])
    axis_profiles = scan_data["axis_profiles"]
    scan_variants = state.get("scan_variants") or {}

    baseline_acc = 0.0
    if campaign_rounds:
        baseline_acc = campaign_rounds[-1].get("accuracy", 0.0)

    return prepare_scan_context(
        scan_df,
        axis_profiles,
        scan_variants,
        baseline_acc,
    )


async def run_optimization(
    campaign_rounds: list,
    dataset: list,
    campaign_config: CampaignConfig,
    *,
    session: BackendSession,
    pipeline_params: dict | None = None,
    scan_context: ScanContext | None = None,
    experiment_id: str | None = None,
    task_context: dict | None = None,
    session_id: str = "",
    display_callbacks=None,
) -> tuple[list, RunResult | None]:
    """Build RunConfig and run the optimization loop.

    Returns (campaign_rounds, RunResult | None).
    """
    from promptpotter.models.opt_search_point import OptSearchPoint
    from promptpotter.services.campaign.config import RunConfig
    from promptpotter.services.campaign.init import extract_campaign_baseline
    from promptpotter.services.campaign.optimization_loop import (
        run_optimization as _run_optimization,
    )
    from promptpotter.services.campaign.state import RunCallbacks

    config = RunConfig.from_campaign_config(
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

    bl = extract_campaign_baseline(campaign_rounds)

    cb = display_callbacks or RunCallbacks()

    def _on_round(round_result, stall_count):
        """Append round entry to campaign_rounds."""
        round_entry = round_result.model_dump()
        ps_raw = round_entry.get("prompt_fields", {})
        round_entry["prompt_fields"] = (
            OptSearchPoint.from_prompt_fields(ps_raw) if isinstance(ps_raw, dict) else ps_raw
        )
        round_entry["round"] = len(campaign_rounds)
        campaign_rounds.append(round_entry)

    # Chain the round callback with any display callbacks
    _original_on_round = cb.on_round_complete

    def _chained_on_round(round_result, stall_count):
        _on_round(round_result, stall_count)
        if _original_on_round:
            _original_on_round(round_result, stall_count)

    cb = RunCallbacks(
        on_round_complete=_chained_on_round,
        on_candidate_eval=cb.on_candidate_eval,
        on_query_eval=cb.on_query_eval,
        on_phase=cb.on_phase,
        on_checkpoint=cb.on_checkpoint,
    )

    result = await _run_optimization(
        bl.instruction,
        dataset,
        config,
        baseline_prompt_fields=bl.baseline_ps,
        baseline_accuracy=bl.baseline_acc,
        baseline_results=bl.baseline_results,
        callbacks=cb,
        scan_context=scan_context,
        experiment_id=experiment_id or "",
        backend_client=session.backend_client,
    )

    return campaign_rounds, result


# ---------------------------------------------------------------------------
# Results display (CLI text output — no IPython/pandas dependency)
# ---------------------------------------------------------------------------


def show_campaign_summary(campaign_rounds: list) -> None:
    """Print campaign results as a text table."""
    if not campaign_rounds:
        print("No campaign rounds.")
        return

    print(f"\nCAMPAIGN SUMMARY ({len(campaign_rounds)} rounds)")
    print("=" * 70)
    print(f"  {'Round':<7s} {'Accuracy':>9s} {'Hits':>6s} {'Label':<40s}")
    print(f"  {'-' * 7} {'-' * 9} {'-' * 6} {'-' * 40}")
    for rd in campaign_rounds:
        rnd = str(rd.get("round", "?"))
        acc = rd.get("accuracy", 0)
        hits = rd.get("hits", 0)
        total = rd.get("total", 0)
        label = str(rd.get("label", ""))[:40]
        print(f"  {rnd:<7s} {acc:>8.1%} {hits:>3d}/{total:<3d} {label}")


def show_flip_tracking(campaign_rounds: list) -> None:
    """Print query flips between first and last round."""
    if len(campaign_rounds) < 2:
        return

    base_r = campaign_rounds[0].get("results", [])
    final_r = campaign_rounds[-1].get("results", [])
    if not base_r or not final_r:
        return

    gained = lost = 0
    for br, fr in zip(base_r, final_r, strict=False):
        if br["hit"] != fr["hit"]:
            if fr["hit"]:
                gained += 1
            else:
                lost += 1

    print(f"\nFLIP TRACKING (baseline -> round {campaign_rounds[-1].get('round', '?')})")
    print(f"  Gained (MISS->HIT): {gained}")
    print(f"  Lost   (HIT->MISS): {lost}")
    print(f"  Net change:         {gained - lost:+d}")


def show_lineage_chain(campaign_rounds: list) -> None:
    """Print prompt lineage chain."""
    if not campaign_rounds:
        return

    print("\nLINEAGE CHAIN")
    print("=" * 50)
    for i, rd in enumerate(campaign_rounds):
        ps = rd.get("prompt_fields")
        if ps is None:
            continue
        pid = getattr(ps, "id", "")[:12] if hasattr(ps, "id") else "?"
        parent = getattr(ps, "parent_id", "")
        parent_str = parent[:12] if parent else "root"
        arrow = "  " if i == 0 else "  -> "
        acc = rd.get("accuracy", 0)
        label = str(rd.get("label", ""))[:40]
        print(f"{arrow}[{pid}] Round {rd.get('round', '?')}: {label} ({acc:.1%})")
        if parent:
            changes = getattr(ps, "changes_description", "") or "none"
            print(f"       parent: {parent_str}  |  changes: {changes}")
