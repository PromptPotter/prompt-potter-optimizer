"""CLI campaign runner — terminal-based optimization with HITL.

Parallel orchestration layer to the Jupyter notebook. Each subcommand
reconstructs services, runs its step, persists to SessionStore, and
appends to the campaign log. The ``campaign_state.json`` file in the
session directory is the bidirectional dashboard: it shows live state
during optimization and accepts control signals (pause/resume/stop)
written by the user or a webapp.

Usage:
    python -m promptpotter.cli.campaign_runner init [options]
    python -m promptpotter.cli.campaign_runner task-context --task-file PATH
    python -m promptpotter.cli.campaign_runner scan --variants-file PATH
    python -m promptpotter.cli.campaign_runner scan-results
    python -m promptpotter.cli.campaign_runner optimize [--round | --auto]
    python -m promptpotter.cli.campaign_runner control --pause | --resume | --stop
    python -m promptpotter.cli.campaign_runner results [--save]
    python -m promptpotter.cli.campaign_runner status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.services.campaign.bootstrap import BackendContext
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.campaign.state import RunResult
    from promptpotter.services.search.scan_results import ScanContext

# Windows consoles default to cp1252 which can't print Unicode symbols.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SessionCtx:
    """Loaded session state — eliminates repeated store/state/id boilerplate."""

    __slots__ = ("backend_id", "session_id", "state", "store")

    def __init__(self, store: ProjectStore, state: dict, backend_id: str, session_id: str):
        self.store = store
        self.state = state
        self.backend_id = backend_id
        self.session_id = session_id


def _load_session(args: argparse.Namespace) -> _SessionCtx:
    """Load active session from disk."""
    store = ProjectStore()
    state, backend_id, session_id = store.sessions.load_active(args.session)
    return _SessionCtx(store, state, backend_id, session_id)


def _die(msg: str) -> None:
    sys.exit(f"ERROR: {msg}")


async def _init_services(
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    dataset_name: str | None = None,
) -> BackendContext:
    """Initialize services (logging + service init)."""
    from promptpotter.config.logging import setup_logging
    from promptpotter.services.campaign.bootstrap import init_services as _bootstrap_init

    setup_logging()
    project_root = Path(__file__).resolve().parent.parent.parent
    return await _bootstrap_init(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        on_status=logger.info,
    )


def _configure_pipeline(
    session: BackendContext,
    campaign_config: CampaignConfig,
) -> dict:
    """Configure pipeline, apply filtered schema to session. Returns pipeline_params."""
    from promptpotter.services.campaign.config import (
        configure_pipeline as _svc_configure,
    )

    result = _svc_configure(
        session.pipeline_schema,
        campaign_config,
        exp_data=getattr(session, "exp_data", None),
    )

    # Apply filtered schema (replaces manual filter_to_steps)
    if result.filtered_schema:
        session.pipeline_schema = result.filtered_schema

    nodes_str = ", ".join(result.active_nodes)
    excl_str = f" (excluded: {', '.join(result.excluded_nodes)})" if result.excluded_nodes else ""
    logger.info("Active nodes: %s%s", nodes_str, excl_str)

    return result.pipeline_params


async def _prepare_eval_context(
    session: BackendContext,
    train_data: list[dict] | None,
    campaign_config: CampaignConfig | None = None,
    run_baseline: bool = False,
    pipeline_params: dict | None = None,
):
    """Load baseline + dataset, optionally run baseline eval."""
    from promptpotter.services.campaign.bootstrap import (
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

    logger.info("Evaluation data: %d queries", len(dataset))
    return baseline, dataset, campaign_rounds, baseline_results


async def _run_scan(
    baseline,
    campaign_config: CampaignConfig,
    scan_variants: dict,
    dataset: list,
    *,
    scan_sample_size: int = 0,
    session: BackendContext,
    experiment_id: str = "",
    session_id: str = "",
):
    """Prepare baseline, run sensitivity scan, persist results.

    Returns (scan_baseline_sp, scan_df, axis_profiles).
    """
    from promptpotter.services.campaign.config import create_llm_client
    from promptpotter.services.search.scan_results import (
        decompose_scan_baseline as _decompose_scan_baseline,
    )
    from promptpotter.services.search.sensitivity_scanner import (
        sensitivity_scan as _sensitivity_scan,
    )

    # Prepare scan baseline
    llm_client, llm_model = create_llm_client(campaign_config)
    prompt_node = ""
    pipeline_params = _configure_pipeline(session, campaign_config)
    ps = session.pipeline_schema
    if ps:
        active_steps = set((pipeline_params or {}).get("steps", []))
        for name in ps.prompt_node_names():
            if name in active_steps:
                prompt_node = name
                break

    result = await _decompose_scan_baseline(
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


async def _run_optimization(
    campaign_rounds: list,
    dataset: list,
    campaign_config: CampaignConfig,
    *,
    session: BackendContext,
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
    from promptpotter.services.campaign.bootstrap import extract_campaign_baseline
    from promptpotter.services.campaign.config import RunConfig
    from promptpotter.services.campaign.optimization_loop import (
        run_optimization as _svc_run_optimization,
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

    result = await _svc_run_optimization(
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
# Subcommands
# ---------------------------------------------------------------------------


async def cmd_init(args: argparse.Namespace) -> None:
    """Initialize services, load datasets, configure pipeline, create session."""
    import httpx

    from promptpotter.services.campaign.bootstrap import (
        prepare_datasets as _prepare_datasets,
    )

    session = await _init_services(
        backend_url=args.backend_url,
        backend_id=args.backend_id,
        experiment_id=args.experiment_id,
        dataset_name=args.dataset_name,
    )

    # Priority: --config file > connector profile > empty dict
    profile = session.store.backends.load_connector_profile(args.backend_id) or {}
    config_data: dict = {}
    if args.config:
        with open(args.config) as f:
            config_data = json.load(f)
    file_config = config_data.get("campaign_config", config_data) or {}
    campaign_config = {**profile, **file_config}

    # Fetch and log pipeline snapshot
    try:
        pipeline_raw = await session.backend_client.fetch_pipeline()
    except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
        logger.warning("Backend unreachable: %s", exc)
        pipeline_raw = {}
    pipeline_cfg = pipeline_raw.get("data", pipeline_raw)
    if pipeline_cfg:
        p_name = pipeline_cfg.get("name", "?")
        p_version = pipeline_cfg.get("version", "?")
        p_nodes = list(pipeline_cfg.get("nodes", {}).keys())
        logger.info("Pipeline: %s %s (%d nodes: %s)", p_name, p_version, len(p_nodes), p_nodes)

    pipeline_params = _configure_pipeline(session, campaign_config)

    active = list(pipeline_params.get("steps", [])) if pipeline_params else []
    excluded = campaign_config.get("exclude_nodes", [])

    # Create session early so partial progress survives interrupts
    state: dict[str, Any] = {
        "phase": "init",
        "init_params": {
            "backend_url": args.backend_url,
            "backend_id": args.backend_id,
            "experiment_id": args.experiment_id,
            "dataset_name": args.dataset_name,
        },
        "campaign_config": campaign_config,
        "pipeline_params": pipeline_params,
        "active_steps": active,
        "baseline_prompt_fields": {},
        "dataset_count": 0,
        "baseline_accuracy": 0.0,
        "task_context": None,
        "scan_variants": None,
        "cycle_id": None,
        "experiment_id": None,
    }

    backend_id = session.backend_id
    session_id = session.store.sessions.create(backend_id, state)
    session.store.sessions.save_active_pointer(backend_id, session_id)

    # Load data and run baseline (may be long-running)
    train_data = None
    if args.excel_path:
        ds_result = _prepare_datasets(session.store, session.backend_id, args.excel_path)
        train_data = ds_result.train_data
    elif session.queries:
        train_data = session.queries

    baseline, dataset, campaign_rounds, _br = await _prepare_eval_context(
        session,
        train_data,
        campaign_config,
        run_baseline=args.run_baseline,
        pipeline_params=pipeline_params,
    )

    # Update session with baseline results
    baseline_acc = campaign_rounds[-1]["accuracy"] if campaign_rounds else 0.0
    state["baseline_prompt_fields"] = baseline.prompt_field_dict()
    state["dataset_count"] = len(dataset)
    state["baseline_accuracy"] = baseline_acc
    session.store.sessions.save(backend_id, session_id, state)

    excl_str = f"{', '.join(excluded)} excluded" if excluded else "none excluded"
    session.store.sessions.append_log(
        backend_id,
        session_id,
        f"""# Campaign Report — {session_id}

## Setup
- Backend: {args.backend_id} @ {args.backend_url}
- Active steps: {", ".join(active)} ({excl_str})
- Eval data: {len(dataset)} queries
- Baseline: {baseline_acc:.1%}""",
    )

    if getattr(args, "json_output", False):
        print(
            json.dumps(
                {
                    "session_id": session_id,
                    "backend_id": backend_id,
                    "phase": state["phase"],
                    "baseline_accuracy": baseline_acc,
                    "dataset_count": len(dataset),
                    "active_steps": active,
                    "excluded_nodes": excluded,
                },
                indent=2,
                default=str,
            )
        )
    else:
        print(f"\nSession created: {session_id}")
        print(f"Baseline: {baseline_acc:.1%} ({len(dataset)} queries)")


async def cmd_task_context(args: argparse.Namespace) -> None:
    """Decompose task description into structured domain context."""
    from promptpotter.services.campaign.config import create_llm_client
    from promptpotter.services.search.context import (
        decompose_task_context as _svc_decompose,
    )

    ctx = _load_session(args)

    task_description = ""
    if args.task_file:
        task_description = Path(args.task_file).read_text(encoding="utf-8")
    elif args.task_text:
        task_description = args.task_text
    if not task_description:
        _die("Provide --task-file or --task-text")

    session = await _init_services(**ctx.state["init_params"])

    # Inline decompose_task_context
    llm_client, model = create_llm_client(ctx.state["campaign_config"])
    result = await _svc_decompose(
        task_description,
        llm_client,
        model,
        store_base_dir=session.store.base_dir if session.store else None,
        backend_id=session.backend_id,
    )
    cache_tag = " (cached)" if result.was_cached else ""
    logger.info("Task context decomposed%s: %d fields", cache_tag, len(result.task_context))
    task_context = result.task_context

    ctx.state["task_context"] = task_context
    ctx.state["phase"] = "task-context"
    session.store.sessions.save(ctx.backend_id, ctx.session_id, ctx.state)
    session.store.sessions.append_log(
        ctx.backend_id, ctx.session_id, f"## Task Context\n{json.dumps(task_context, indent=2)}"
    )


async def cmd_scan(args: argparse.Namespace) -> None:
    """Run sensitivity scan with provided variants."""
    from promptpotter.services.campaign.bootstrap import load_baseline_prompt
    from promptpotter.services.search.scan_advisor import resolve_schema_axes
    from promptpotter.shared.constants import PROMPT_STRING_FIELDS

    ctx = _load_session(args)

    if not args.variants_file:
        _die("--variants-file required")
    with open(args.variants_file) as f:
        scan_variants = json.load(f)

    session = await _init_services(**ctx.state["init_params"])

    # Inline resolve_scan_variants
    flat_for_resolve: dict[str, list] = {}
    for key, spec in scan_variants.items():
        if isinstance(spec, list):
            flat_for_resolve[key] = spec
        elif isinstance(spec, dict):
            for param, vals in spec.items():
                if isinstance(vals, list):
                    flat_for_resolve[param] = vals
    resolve_schema_axes(flat_for_resolve, session.pipeline_schema)
    prompt_axes = [k for k in scan_variants if k in PROMPT_STRING_FIELDS]
    node_axes = [k for k in scan_variants if k not in PROMPT_STRING_FIELDS]
    logger.info(
        "Scan variants resolved: %d prompt axes, %d node axes",
        len(prompt_axes),
        len(node_axes),
    )

    _configure_pipeline(session, ctx.state["campaign_config"])

    _pn = session.pipeline_schema.prompt_node_names() if session.pipeline_schema else []
    baseline = load_baseline_prompt(session.exp_data, prompt_node_names=_pn)
    train_data = session.queries or []

    sample_size = (
        args.sample_size
        if args.sample_size is not None
        else ctx.state["campaign_config"].get("exploration_sample_size", 0)
    )
    _scan_bl, scan_df, _axis_profiles = await _run_scan(
        baseline,
        ctx.state["campaign_config"],
        scan_variants,
        train_data,
        scan_sample_size=sample_size,
        session=session,
        experiment_id=ctx.state["init_params"]["experiment_id"],
        session_id=ctx.session_id,
    )

    ctx.state["scan_variants"] = scan_variants
    ctx.state["phase"] = "scan"
    session.store.sessions.save(ctx.backend_id, ctx.session_id, ctx.state)

    records = scan_df.to_dict(orient="records") if scan_df is not None else []
    best = max(records, key=lambda r: r.get("accuracy", 0)) if records else {}
    session.store.sessions.append_log(
        ctx.backend_id,
        ctx.session_id,
        f"""## Scan
- Axes: {len(scan_variants)}, variants: {len(records)}
- Best: {best.get("axis", "?")}={best.get("value_preview", "?")} \u2192 {best.get("accuracy", 0):.1%} (delta: {best.get("delta", 0):+.1%})""",
    )


async def cmd_scan_results(args: argparse.Namespace) -> None:
    """Show scan analytics and seed campaign from scan winner."""
    from promptpotter.services.campaign.bootstrap import load_baseline_prompt
    from promptpotter.services.search.scan_results import (
        seed_campaign_from_scan as _seed_campaign_from_scan,
    )

    ctx = _load_session(args)
    session = await _init_services(**ctx.state["init_params"])

    scan_data = session.store.sessions.load_scan_results(ctx.backend_id, ctx.session_id)
    if not scan_data:
        _die("No scan results. Run 'scan' first.")

    import pandas as pd

    scan_df = pd.DataFrame(scan_data["scan_df"])
    axis_profiles = scan_data["axis_profiles"]

    # Print scan summary (CLI-friendly, no IPython)
    if not scan_df.empty:
        best_row = scan_df.loc[scan_df["accuracy"].idxmax()]
        print(f"Scan results: {len(scan_df)} variants across {scan_df['axis'].nunique()} axes")
        print(
            f"Best: {best_row['axis']}={best_row.get('value_preview', '?')} "
            f"({best_row['accuracy']:.1%}, delta={best_row.get('delta', 0):+.1%})"
        )

    _pn = session.pipeline_schema.prompt_node_names() if session.pipeline_schema else []
    scan_baseline_sp = load_baseline_prompt(session.exp_data, prompt_node_names=_pn)

    campaign_rounds: list = []
    _seed_result = _seed_campaign_from_scan(
        scan_df,
        axis_profiles,
        scan_baseline_sp,
        ctx.state.get("scan_variants", {}),
        campaign_rounds,
        ctx.state["campaign_config"],
    )

    if campaign_rounds:
        ctx.state["baseline_accuracy"] = campaign_rounds[-1].get("accuracy", 0.0)
        ctx.state["baseline_prompt_fields"] = campaign_rounds[-1].get("prompt_fields", {})

    ctx.state["phase"] = "scan-results"
    session.store.sessions.save(ctx.backend_id, ctx.session_id, ctx.state)


async def cmd_optimize(args: argparse.Namespace) -> None:
    """Run optimization loop. Dashboard is campaign_state.json in session dir."""
    from promptpotter.models.opt_search_point import OptSearchPoint
    from promptpotter.services.search import prepare_scan_context

    ctx = _load_session(args)
    campaign_config = ctx.state["campaign_config"]

    session = await _init_services(**ctx.state["init_params"])
    pipeline_params = _configure_pipeline(session, campaign_config)
    train_data = session.queries or []

    # Re-run baseline (fast — cached) to populate baseline_results for critique
    _has_baseline = ctx.state.get("baseline_accuracy", 0) > 0
    _baseline, dataset, campaign_rounds, baseline_results = await _prepare_eval_context(
        session,
        train_data,
        campaign_config,
        pipeline_params=pipeline_params,
        run_baseline=_has_baseline,
    )

    # Seed campaign_rounds with stored baseline when no eval has been run yet
    if not campaign_rounds and ctx.state.get("baseline_prompt_fields"):
        bl_ps = OptSearchPoint.from_prompt_fields(ctx.state["baseline_prompt_fields"])
        campaign_rounds.append(
            {
                "round": "baseline",
                "accuracy": ctx.state.get("baseline_accuracy", 0.0),
                "prompt_fields": bl_ps,
                "results": baseline_results or [],
            }
        )

    # Load scan context if available (inline build_scan_context)
    scan_context: ScanContext | None = None
    scan_data = session.store.sessions.load_scan_results(
        session.backend_id,
        ctx.session_id,
    )
    if scan_data:
        import pandas as pd

        scan_df = pd.DataFrame(scan_data["scan_df"])
        axis_profiles = scan_data["axis_profiles"]
        scan_variants = ctx.state.get("scan_variants") or {}
        baseline_acc = 0.0
        if campaign_rounds:
            baseline_acc = campaign_rounds[-1].get("accuracy", 0.0)
        scan_context = prepare_scan_context(
            scan_df,
            axis_profiles,
            scan_variants,
            baseline_acc,
        )

    # HITL: --round sets pause_before_eval via the control surface
    if args.round:
        campaign_config.setdefault("optimization", {})["pause_before_eval"] = True
    else:
        campaign_config.setdefault("optimization", {}).pop("pause_before_eval", None)

    ctx.state["phase"] = "optimizing"
    session.store.sessions.save(ctx.backend_id, ctx.session_id, ctx.state)

    # Bidirectional control — CLI provides FileControlSurface as on_checkpoint
    from promptpotter.services.campaign.persistence_emitter import FileControlSurface
    from promptpotter.services.campaign.state import RunCallbacks

    session_dir = session.store.sessions._session_dir(ctx.backend_id, ctx.session_id)
    control = FileControlSurface(session_dir / "campaign_state.json")
    control_cb = RunCallbacks(on_checkpoint=control.check)

    # Round recorder — write rounds/round_NNN.json with full action traces
    from promptpotter.config.optimizer_pipeline import set_round_recorder
    from promptpotter.services.campaign.persistence_emitter import RoundRecorder

    recorder = RoundRecorder(session_dir / "rounds")
    set_round_recorder(recorder)

    try:
        campaign_rounds, cycle_result = await _run_optimization(
            campaign_rounds,
            dataset,
            campaign_config,
            session=session,
            pipeline_params=pipeline_params,
            scan_context=scan_context,
            experiment_id=ctx.state.get("experiment_id"),
            task_context=ctx.state.get("task_context"),
            session_id=ctx.session_id,
            display_callbacks=control_cb,
        )
    finally:
        set_round_recorder(None)

    # Read back cycle_id and best from persisted campaign_state.json
    import contextlib

    _state_path = session_dir / "campaign_state.json"
    _emitter_state = {}
    if _state_path.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            _emitter_state = json.loads(_state_path.read_text(encoding="utf-8"))

    ctx.state["phase"] = "optimize"
    ctx.state["cycle_id"] = _emitter_state.get("cycle_id")
    ctx.state["best_accuracy"] = _emitter_state.get("best", ctx.state.get("baseline_accuracy", 0.0))
    session.store.sessions.save(ctx.backend_id, ctx.session_id, ctx.state)

    # Write structured result for AI/machine consumption
    result_path = session_dir / "optimize_result.json"
    if cycle_result:
        result_path.write_text(
            json.dumps(cycle_result.model_dump(), indent=2, default=str),
            encoding="utf-8",
        )

    if getattr(args, "json_output", False):
        _summary = cycle_result.model_dump() if cycle_result else {"status": "interrupted"}
        print(json.dumps(_summary, indent=2, default=str))
    else:
        print(f"Dashboard: {_state_path}")
        if cycle_result:
            print(f"Result: {result_path}")


async def cmd_control(args: argparse.Namespace) -> None:
    """Write control signals to campaign_state.json (bidirectional dashboard)."""
    ctx = _load_session(args)
    session_dir = ctx.store.sessions._session_dir(ctx.backend_id, ctx.session_id)
    state_path = session_dir / "campaign_state.json"

    if not state_path.exists():
        _die("No campaign_state.json — run 'optimize' first.")

    data = json.loads(state_path.read_text(encoding="utf-8"))
    control = data.get("control", {})

    action = ""
    if args.pause:
        control["requested_state"] = "pause"
        action = "pause"
    elif args.resume:
        control["requested_state"] = "resume"
        action = "resume"
    elif args.stop:
        control["requested_state"] = "stop"
        action = "stop"
    elif args.pause_before_l2:
        control["pause_before_l2_eval"] = True
        action = "pause_before_l2_enabled"
    elif args.no_pause_l2:
        control["pause_before_l2_eval"] = False
        action = "pause_before_l2_disabled"

    data["control"] = control
    state_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    if getattr(args, "json_output", False):
        print(json.dumps({"action": action, "control": control}, indent=2))
    else:
        print(f"Control: {action} requested.")


async def cmd_profile(args: argparse.Namespace) -> None:
    """Manage connector profile — persistent per-backend defaults."""
    store = ProjectStore()
    backend_id = args.backend_id

    if args.save:
        # Save active session's campaign_config as the backend profile
        state, backend_id, _sid = store.sessions.load_active(args.session)
        profile = state.get("campaign_config", {})
        store.backends.save_connector_profile(backend_id, profile)
        print(f"Profile saved for '{backend_id}'.")
        return

    if args.set:
        key, raw_value = args.set
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        profile = store.backends.load_connector_profile(backend_id) or {}
        profile[key] = value
        store.backends.save_connector_profile(backend_id, profile)
        print(f"Profile '{backend_id}': {key} = {json.dumps(value)}")
        return

    # Default: --show
    profile = store.backends.load_connector_profile(backend_id)
    if not profile:
        print(f"No connector profile for '{backend_id}'. Use --save or --set to create one.")
        return
    print(json.dumps(profile, indent=2, default=str))


async def cmd_results(args: argparse.Namespace) -> None:
    """Show campaign results, optionally save winner."""
    from promptpotter.services.campaign.bootstrap import save_campaign_winner

    ctx = _load_session(args)
    session = await _init_services(**ctx.state["init_params"])

    campaigns = session.store.campaigns.list_campaigns(ctx.backend_id)
    if not campaigns:
        if getattr(args, "json_output", False):
            print(json.dumps({"error": "no_campaigns"}, indent=2))
        else:
            print("No campaigns found.")
        return

    latest = campaigns[-1]
    cycle_id = latest.get("campaign_id", "")
    campaign_rounds = []
    for i in range(latest.get("n_trials", 0)):
        trial = session.store.campaigns.load_trial(ctx.backend_id, cycle_id, i)
        if trial:
            campaign_rounds.append(trial)

    if getattr(args, "json_output", False):
        best = max(campaign_rounds, key=lambda r: r.get("accuracy", 0)) if campaign_rounds else {}
        print(
            json.dumps(
                {
                    "cycle_id": cycle_id,
                    "n_rounds": len(campaign_rounds),
                    "best_accuracy": best.get("accuracy", 0),
                    "best_round": best.get("round"),
                    "baseline_accuracy": ctx.state.get("baseline_accuracy", 0),
                    "rounds": [
                        {
                            "round": r.get("round"),
                            "accuracy": r.get("accuracy", 0),
                            "label": r.get("label", ""),
                        }
                        for r in campaign_rounds
                    ],
                },
                indent=2,
                default=str,
            )
        )
    else:
        # Inline show_campaign_summary
        if not campaign_rounds:
            print("No campaign rounds.")
        else:
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

        # Inline show_flip_tracking
        if len(campaign_rounds) >= 2:
            base_r = campaign_rounds[0].get("results", [])
            final_r = campaign_rounds[-1].get("results", [])
            if base_r and final_r:
                gained = lost = 0
                for br, fr in zip(base_r, final_r, strict=False):
                    if br["hit"] != fr["hit"]:
                        if fr["hit"]:
                            gained += 1
                        else:
                            lost += 1
                print(
                    f"\nFLIP TRACKING (baseline -> round {campaign_rounds[-1].get('round', '?')})"
                )
                print(f"  Gained (MISS->HIT): {gained}")
                print(f"  Lost   (HIT->MISS): {lost}")
                print(f"  Net change:         {gained - lost:+d}")

        # Inline show_lineage_chain
        if campaign_rounds:
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

    if args.save:
        save_campaign_winner(
            campaign_rounds,
            ctx.state["campaign_config"],
            session.store,
            ctx.backend_id,
            experiment_id=ctx.state.get("experiment_id", ""),
        )
        if not getattr(args, "json_output", False):
            print("Winner saved.")

    best = max(campaign_rounds, key=lambda r: r.get("accuracy", 0)) if campaign_rounds else {}
    session.store.sessions.append_log(
        ctx.backend_id,
        ctx.session_id,
        f"""## Results
- Rounds: {len(campaign_rounds)}
- Best: {best.get("accuracy", 0):.1%} (round {best.get("round", "?")})
- Saved: {args.save}""",
    )


async def cmd_status(args: argparse.Namespace) -> None:
    """Print live dashboard from campaign_state.json (or session state if idle)."""
    ctx = _load_session(args)
    session_dir = ctx.store.sessions._session_dir(ctx.backend_id, ctx.session_id)
    state_path = session_dir / "campaign_state.json"

    # Machine-readable JSON mode
    if getattr(args, "json_output", False):
        output: dict[str, Any] = {
            "session_id": ctx.session_id,
            "phase": ctx.state["phase"],
            "backend_id": ctx.backend_id,
            "backend_url": ctx.state["init_params"]["backend_url"],
            "baseline_accuracy": ctx.state.get("baseline_accuracy", 0),
            "dataset_count": ctx.state.get("dataset_count"),
        }
        import contextlib

        if state_path.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                output["live_dashboard"] = json.loads(state_path.read_text(encoding="utf-8"))
        result_path = session_dir / "optimize_result.json"
        if result_path.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                output["optimize_result"] = json.loads(result_path.read_text(encoding="utf-8"))
        rounds_dir = session_dir / "rounds"
        if rounds_dir.exists():
            output["round_count"] = len(list(rounds_dir.glob("round_*.json")))
        print(json.dumps(output, indent=2, default=str))
        return

    # Human-readable mode
    print(f"Session: {ctx.session_id}")
    print(f"Phase: {ctx.state['phase']}")
    print(f"Backend: {ctx.backend_id} @ {ctx.state['init_params']['backend_url']}")
    print(f"Eval data: {ctx.state.get('dataset_count', '?')} queries")
    print(f"Baseline: {ctx.state.get('baseline_accuracy', 0):.1%}")

    # Live dashboard from campaign_state.json
    if state_path.exists():
        try:
            live = json.loads(state_path.read_text(encoding="utf-8"))
            print("\n--- Live Dashboard ---")
            print(f"Workflow: {live.get('workflow', '?')}")
            print(f"Phase: {live.get('phase', '?')}")
            print(f"Round: {live.get('round', 0)}/{live.get('max_rounds', '?')}")
            print(f"Layer: {live.get('layer', 'L1')}")
            print(f"Patience: {live.get('patience', '?')}")
            print(f"Best: {live.get('best', 0):.1%} (round {live.get('best_round', '?')})")
            print(f"Current: {live.get('candidate', '')} {live.get('query', '')}")
            print(f"Hit rate: {live.get('hit_rate', 0):.1%}")
            print(f"Cache: {live.get('cache_hit_rate', 0):.1%}")
            print(f"ETA: {live.get('eta_s', 0):.0f}s")
            print(f"Elapsed: {live.get('elapsed_s', 0):.0f}s")
            control = live.get("control", {})
            print(f"Control: {control.get('requested_state', 'running')}")
            if control.get("pause_before_l2_eval"):
                print("  pause_before_l2_eval: enabled")
            if live.get("stop_reason"):
                print(f"Stop reason: {live['stop_reason']}")
            if live.get("pause_point"):
                print(f"Pause point: {live['pause_point']}")
        except (json.JSONDecodeError, OSError):
            pass

    log = ctx.store.sessions.load_log(ctx.backend_id, ctx.session_id)
    if log:
        lines = log.splitlines()
        tail = lines[-20:] if len(lines) > 20 else lines
        print(f"\n--- campaign_log.md (last {len(tail)} lines) ---")
        for line in tail:
            print(line)


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="campaign_runner",
        description="CLI campaign runner for PromptPotter optimization",
    )
    parser.add_argument("--session", default=None, help="Session ID (default: active)")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON instead of human-formatted text",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize services and create session")
    p_init.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    p_init.add_argument("--backend-id", default=DEFAULT_BACKEND_ID)
    p_init.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    p_init.add_argument("--dataset-name", default=None)
    p_init.add_argument("--excel-path", default=None)
    p_init.add_argument("--config", default=None, help="Campaign config JSON file")
    p_init.add_argument("--run-baseline", action="store_true")

    p_tc = sub.add_parser("task-context", help="Decompose task description")
    p_tc.add_argument("--task-file", default=None)
    p_tc.add_argument("--task-text", default=None)

    p_scan = sub.add_parser("scan", help="Run sensitivity scan")
    p_scan.add_argument("--variants-file", required=True, help="Scan variants JSON")
    p_scan.add_argument("--sample-size", type=int, default=None)

    sub.add_parser("scan-results", help="Show scan analytics and seed campaign")

    p_opt = sub.add_parser("optimize", help="Run optimization loop")
    p_opt.add_argument("--round", action="store_true", help="Pause after L1 generate for review")
    p_opt.add_argument(
        "--auto", action="store_true", help="Full loop (default — use dashboard to control)"
    )

    p_ctl = sub.add_parser("control", help="Write control signals to dashboard")
    ctl_mode = p_ctl.add_mutually_exclusive_group(required=True)
    ctl_mode.add_argument("--pause", action="store_true", help="Pause at next checkpoint")
    ctl_mode.add_argument("--resume", action="store_true", help="Resume from pause")
    ctl_mode.add_argument("--stop", action="store_true", help="Stop at next checkpoint")
    ctl_mode.add_argument("--pause-before-l2", action="store_true", help="Enable L2 pause")
    ctl_mode.add_argument("--no-pause-l2", action="store_true", help="Disable L2 pause")

    p_prof = sub.add_parser("profile", help="Manage connector profile (per-backend defaults)")
    p_prof.add_argument("--backend-id", default="local")
    prof_mode = p_prof.add_mutually_exclusive_group()
    prof_mode.add_argument(
        "--show", action="store_true", default=True, help="Show profile (default)"
    )
    prof_mode.add_argument(
        "--save", action="store_true", help="Save active session config as profile"
    )
    prof_mode.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="Set a profile field")

    p_res = sub.add_parser("results", help="Show results and optionally save")
    p_res.add_argument("--save", action="store_true")

    sub.add_parser("status", help="Print live dashboard + session state")

    return parser


COMMANDS = {
    "init": cmd_init,
    "task-context": cmd_task_context,
    "scan": cmd_scan,
    "scan-results": cmd_scan_results,
    "optimize": cmd_optimize,
    "control": cmd_control,
    "profile": cmd_profile,
    "results": cmd_results,
    "status": cmd_status,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(COMMANDS[args.command](args))


if __name__ == "__main__":
    main()
