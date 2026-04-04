"""CLI campaign runner — terminal-based optimization with HITL.

Parallel orchestration layer to the Jupyter notebook. Each subcommand
reconstructs services, runs its step, persists to SessionStore, and
appends to the campaign log. Designed for both Claude Code and humans.

Usage:
    python -m api.cli.campaign_runner init [options]
    python -m api.cli.campaign_runner task-context --task-file PATH
    python -m api.cli.campaign_runner scan --variants-file PATH
    python -m api.cli.campaign_runner scan-results
    python -m api.cli.campaign_runner optimize --round | --evaluate | --auto
    python -m api.cli.campaign_runner results [--save]
    python -m api.cli.campaign_runner status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Windows consoles default to cp1252 which can't print Unicode symbols (→, ✓, ⚠).
# Reconfigure stdout/stderr to UTF-8 so display code works unchanged.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)

# Thin pointer to the active session (bootstrap before svc is available)
_ACTIVE_SESSION_PATH = Path(".promptpotter") / "active_session.json"
SUMMARY_START = "--- CLI_SUMMARY ---"
SUMMARY_END = "--- END_SUMMARY ---"


# ---------------------------------------------------------------------------
# Session bootstrap
# ---------------------------------------------------------------------------


def _save_active_pointer(backend_id: str, session_id: str) -> None:
    _ACTIVE_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_ACTIVE_SESSION_PATH, "w") as f:
        json.dump({"backend_id": backend_id, "session_id": session_id}, f)


def _load_active_pointer(session_override: str | None = None) -> dict:
    if not _ACTIVE_SESSION_PATH.exists():
        print("ERROR: No active session. Run 'init' first.")
        sys.exit(1)
    with open(_ACTIVE_SESSION_PATH) as f:
        ptr = json.load(f)
    if session_override:
        ptr["session_id"] = session_override
    return ptr


def _load_state(session_override: str | None = None) -> tuple[dict, str, str]:
    """Load session state + backend_id + session_id from active pointer.

    Returns (state, backend_id, session_id).
    """
    ptr = _load_active_pointer(session_override)
    bid, sid = ptr["backend_id"], ptr["session_id"]
    store = ProjectStore()
    state = store.sessions.load(bid, sid)
    if not state:
        print(f"ERROR: Session '{sid}' not found for backend '{bid}'.")
        sys.exit(1)
    return state, bid, sid


def _print_summary(data: dict) -> None:
    """Print JSON summary block for programmatic consumption (Claude Code)."""
    print(f"\n{SUMMARY_START}")
    print(json.dumps(data, default=str))
    print(SUMMARY_END)


# ---------------------------------------------------------------------------
# Service reconstruction
# ---------------------------------------------------------------------------


async def _reconstruct_svc(init_params: dict):
    """Reconstruct InitResult from stored init params (~1s)."""
    from notebooks.campaign_lib.setup import init_services

    return await init_services(
        backend_url=init_params["backend_url"],
        backend_id=init_params["backend_id"],
        experiment_id=init_params["experiment_id"],
        dataset_name=init_params.get("dataset_name"),
    )


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


async def cmd_init(args: argparse.Namespace) -> None:
    """Initialize services, load datasets, configure pipeline, create session."""
    from notebooks.campaign_lib import (
        configure_pipeline,
        init_services,
        prepare_datasets,
        prepare_eval_context,
        show_pipeline_snapshot,
    )

    config_data: dict = {}
    if args.config:
        with open(args.config) as f:
            config_data = json.load(f)
    campaign_config = config_data.get("campaign_config", config_data) or _default_campaign_config()

    svc = await init_services(
        backend_url=args.backend_url,
        backend_id=args.backend_id,
        experiment_id=args.experiment_id,
        dataset_name=args.dataset_name,
    )

    await show_pipeline_snapshot(svc)
    pipeline_params = configure_pipeline(svc, campaign_config)

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
        "eval_data_count": 0,
        "baseline_accuracy": 0.0,
        "task_context": None,
        "scan_variants": None,
        "cycle_id": None,
        "experiment_id": None,
    }

    bid = svc.backend_id
    sid = svc.store.sessions.create(bid, state)
    _save_active_pointer(bid, sid)
    print(f"\nSession created: {sid}")

    # Load data and run baseline (may be long-running)
    train_data = None
    if args.excel_path:
        train_data, _st = prepare_datasets(svc.store, svc.backend_id, excel_path=args.excel_path)
    elif svc.queries:
        train_data = svc.queries

    baseline, eval_data, campaign_rounds, _br = await prepare_eval_context(
        svc, train_data, campaign_config, run_baseline=args.run_baseline,
    )

    # Update session with baseline results
    baseline_acc = campaign_rounds[-1]["accuracy"] if campaign_rounds else 0.0
    state["baseline_prompt_fields"] = baseline.prompt_field_dict()
    state["eval_data_count"] = len(eval_data)
    state["baseline_accuracy"] = baseline_acc
    svc.store.sessions.save(bid, sid, state)

    excl_str = f"{', '.join(excluded)} excluded" if excluded else "none excluded"
    svc.store.sessions.append_log(bid, sid, f"""# Campaign Report — {sid}

## Setup
- Backend: {args.backend_id} @ {args.backend_url}
- Active steps: {', '.join(active)} ({excl_str})
- Eval data: {len(eval_data)} queries
- Baseline: {baseline_acc:.1%}""")

    _print_summary({
        "step": "init", "session_id": sid,
        "eval_data_count": len(eval_data),
        "baseline_accuracy": baseline_acc, "active_steps": active,
    })


async def cmd_task_context(args: argparse.Namespace) -> None:
    """Decompose task description into structured domain context."""
    from notebooks.campaign_lib import decompose_task_context

    state, bid, sid = _load_state(args.session)

    task_description = ""
    if args.task_file:
        task_description = Path(args.task_file).read_text(encoding="utf-8")
    elif args.task_text:
        task_description = args.task_text
    if not task_description:
        print("ERROR: Provide --task-file or --task-text")
        sys.exit(1)

    svc = await _reconstruct_svc(state["init_params"])
    task_context = await decompose_task_context(
        task_description, state["campaign_config"], svc,
    )

    state["task_context"] = task_context
    state["phase"] = "task-context"
    svc.store.sessions.save(bid, sid, state)
    svc.store.sessions.append_log(bid, sid, f"## Task Context\n{json.dumps(task_context, indent=2)}")

    _print_summary({"step": "task-context", "fields": list(task_context.keys())})


async def cmd_scan(args: argparse.Namespace) -> None:
    """Run sensitivity scan with provided variants."""
    from notebooks.campaign_lib import (
        configure_pipeline,
        resolve_scan_variants,
        run_sensitivity_scan,
    )

    state, bid, sid = _load_state(args.session)

    if not args.variants_file:
        print("ERROR: --variants-file required")
        sys.exit(1)
    with open(args.variants_file) as f:
        scan_variants = json.load(f)

    svc = await _reconstruct_svc(state["init_params"])
    resolve_scan_variants(scan_variants, svc=svc)
    configure_pipeline(svc, state["campaign_config"])

    from api.services.campaign.campaign_init import load_baseline_prompt

    _pn = svc.pipeline_schema.prompt_node_names() if svc.pipeline_schema else []
    baseline = load_baseline_prompt(svc.exp_data, prompt_node_names=_pn)
    train_data = svc.queries or []

    sample_size = (
        args.sample_size
        if args.sample_size is not None
        else state["campaign_config"].get("exploration_sample_size", 0)
    )
    strict_params = json.loads(args.strict_params) if args.strict_params else {}

    _scan_bl, scan_df, _axis_profiles = await run_sensitivity_scan(
        baseline, state["campaign_config"], scan_variants, train_data,
        scan_sample_size=sample_size, svc=svc,
        experiment_id=state["init_params"]["experiment_id"],
        strict_params=strict_params,
        session_id=sid,
    )

    state["scan_variants"] = scan_variants
    state["phase"] = "scan"
    svc.store.sessions.save(bid, sid, state)

    records = scan_df.to_dict(orient="records") if scan_df is not None else []
    best = max(records, key=lambda r: r.get("accuracy", 0)) if records else {}
    svc.store.sessions.append_log(bid, sid, f"""## Scan
- Axes: {len(scan_variants)}, variants: {len(records)}
- Best: {best.get('axis', '?')}={best.get('value_preview', '?')} → {best.get('accuracy', 0):.1%} (delta: {best.get('delta', 0):+.1%})""")

    _print_summary({
        "step": "scan", "n_variants": len(records),
        "best_axis": best.get("axis"), "best_accuracy": best.get("accuracy"),
    })


async def cmd_scan_results(args: argparse.Namespace) -> None:
    """Show scan analytics and seed campaign from scan winner."""
    from notebooks.campaign_lib import seed_campaign_from_scan, show_scan_analytics

    state, bid, sid = _load_state(args.session)
    svc = await _reconstruct_svc(state["init_params"])

    scan_data = svc.store.sessions.load_scan_results(bid, sid)
    if not scan_data:
        print("ERROR: No scan results. Run 'scan' first.")
        sys.exit(1)

    import pandas as pd

    scan_df = pd.DataFrame(scan_data["scan_df"])
    axis_profiles = scan_data["axis_profiles"]

    show_scan_analytics(scan_df, axis_profiles, svc)

    from api.services.campaign.campaign_init import load_baseline_prompt

    _pn = svc.pipeline_schema.prompt_node_names() if svc.pipeline_schema else []
    scan_baseline_sp = load_baseline_prompt(svc.exp_data, prompt_node_names=_pn)

    campaign_rounds: list = []
    seed_campaign_from_scan(
        scan_df, axis_profiles, scan_baseline_sp,
        state.get("scan_variants", {}), campaign_rounds,
        state["campaign_config"], pipeline_schema=svc.pipeline_schema,
    )

    if campaign_rounds:
        state["baseline_accuracy"] = campaign_rounds[-1].get("accuracy", 0.0)
        state["baseline_prompt_fields"] = campaign_rounds[-1].get("prompt_fields", {})

    state["phase"] = "scan-results"
    svc.store.sessions.save(bid, sid, state)

    _print_summary({
        "step": "scan-results", "seeded": bool(campaign_rounds),
        "baseline_accuracy": state["baseline_accuracy"],
    })


async def cmd_optimize(args: argparse.Namespace) -> None:
    """Run optimization loop (--round, --evaluate, or --auto)."""
    from notebooks.campaign_lib import (
        configure_pipeline,
        prepare_eval_context,
        run_optimization_notebook,
        show_feedback_preflight,
    )

    state, bid, sid = _load_state(args.session)
    campaign_config = state["campaign_config"]

    svc = await _reconstruct_svc(state["init_params"])
    pipeline_params = configure_pipeline(svc, campaign_config)
    train_data = svc.queries or []

    _baseline, eval_data, campaign_rounds, _ = await prepare_eval_context(
        svc, train_data, campaign_config,
    )

    # Seed campaign_rounds with stored baseline when no eval has been run yet
    if not campaign_rounds and state.get("baseline_prompt_fields"):
        from api.models.opt_search_point import OptSearchPoint

        bl_ps = OptSearchPoint.from_prompt_fields(state["baseline_prompt_fields"])
        campaign_rounds.append({
            "round": "baseline",
            "accuracy": state.get("baseline_accuracy", 0.0),
            "prompt_fields": bl_ps,
            "results": [],
        })

    # Load scan context if available
    scan_df, axis_profiles = None, None
    scan_data = svc.store.sessions.load_scan_results(bid, sid)
    if scan_data:
        import pandas as pd

        scan_df = pd.DataFrame(scan_data["scan_df"])
        axis_profiles = scan_data["axis_profiles"]

    scan_context = show_feedback_preflight(
        campaign_rounds, eval_data, campaign_config,
        pipeline_params=pipeline_params, pipeline_schema=svc.pipeline_schema,
        scan_df=scan_df, axis_profiles=axis_profiles,
        scan_variants=state.get("scan_variants"),
    )

    # HITL: set pause_before_eval for --round mode
    if args.round:
        campaign_config.setdefault("optimization", {})["pause_before_eval"] = True
    else:
        campaign_config.setdefault("optimization", {}).pop("pause_before_eval", None)

    # Save phase before loop starts so in-progress state is visible
    state["phase"] = "optimizing"
    svc.store.sessions.save(bid, sid, state)

    # File emitter: writes campaign_state.jsonl + campaign_output.log
    from api.cli.file_emitter import CampaignFileEmitter
    from api.services.campaign.callbacks import CycleCallbacks

    session_dir = svc.store.sessions._session_dir(bid, sid)
    opt_cfg = campaign_config.get("optimization", {})
    active = state.get("active_steps", [])
    excluded = campaign_config.get("exclude_nodes", [])

    emitter = CampaignFileEmitter(
        session_dir,
        max_rounds=opt_cfg.get("max_rounds", 10),
        active_nodes=active,
        excluded_nodes=excluded,
        config=campaign_config,
    )
    emitter_cb = CycleCallbacks(
        on_phase=emitter.on_phase,
        on_query_eval=emitter.on_query_eval,
        on_candidate_eval=emitter.on_candidate_eval,
        on_round_complete=emitter.on_round_complete,
    )

    campaign_rounds = await run_optimization_notebook(
        campaign_rounds, eval_data, campaign_config,
        svc=svc, pipeline_params=pipeline_params,
        scan_context=scan_context,
        experiment_id=state.get("experiment_id"),
        task_context=state.get("task_context"),
        session_id=sid,
        extra_callbacks=emitter_cb,
    )

    emitter.set_stop_reason("completed")
    state["phase"] = "optimize"
    svc.store.sessions.save(bid, sid, state)

    best = max(campaign_rounds, key=lambda r: r.get("accuracy", 0)) if campaign_rounds else {}
    _print_summary({
        "step": "optimize",
        "mode": "round" if args.round else "evaluate" if args.evaluate else "auto",
        "n_rounds": len(campaign_rounds),
        "best_accuracy": best.get("accuracy", 0),
    })


async def cmd_results(args: argparse.Namespace) -> None:
    """Show campaign results, optionally save winner."""
    from notebooks.campaign_lib import (
        save_campaign_winner,
        show_campaign_summary,
        show_flip_tracking,
        show_lineage_chain,
    )

    state, bid, sid = _load_state(args.session)
    svc = await _reconstruct_svc(state["init_params"])

    campaigns = svc.store.campaigns.list_campaigns(bid)
    if not campaigns:
        print("No campaigns found.")
        return

    latest = campaigns[-1]
    cycle_id = latest.get("campaign_id", "")
    campaign_rounds = []
    for i in range(latest.get("n_trials", 0)):
        trial = svc.store.campaigns.load_trial(bid, cycle_id, i)
        if trial:
            campaign_rounds.append(trial)

    show_campaign_summary(campaign_rounds)
    show_flip_tracking(campaign_rounds)
    show_lineage_chain(campaign_rounds)

    if args.save:
        save_campaign_winner(
            campaign_rounds, state["campaign_config"],
            svc.store, bid, experiment_id=state.get("experiment_id", ""),
        )
        print("Winner saved.")

    best = max(campaign_rounds, key=lambda r: r.get("accuracy", 0)) if campaign_rounds else {}
    svc.store.sessions.append_log(bid, sid, f"""## Results
- Rounds: {len(campaign_rounds)}
- Best: {best.get('accuracy', 0):.1%} (round {best.get('round', '?')})
- Saved: {args.save}""")

    _print_summary({
        "step": "results", "n_rounds": len(campaign_rounds),
        "best_accuracy": best.get("accuracy", 0), "saved": args.save,
    })


async def cmd_status(args: argparse.Namespace) -> None:
    """Print current session state and campaign log tail."""
    state, bid, sid = _load_state(args.session)

    print(f"Session: {sid}")
    print(f"Phase: {state['phase']}")
    print(f"Created: {state.get('created_at', '?')}")
    print(f"Backend: {bid} @ {state['init_params']['backend_url']}")
    print(f"Eval data: {state.get('eval_data_count', '?')} queries")
    print(f"Baseline: {state.get('baseline_accuracy', 0):.1%}")
    print(f"Active steps: {state.get('active_steps', [])}")

    if state.get("task_context"):
        print(f"Task context: {len(state['task_context'])} fields")
    if state.get("scan_variants"):
        print(f"Scan variants: {len(state['scan_variants'])} axes")

    store = ProjectStore()
    log = store.sessions.load_log(bid, sid)
    if log:
        lines = log.splitlines()
        tail = lines[-30:] if len(lines) > 30 else lines
        print(f"\n--- campaign_log.md (last {len(tail)} lines) ---")
        for line in tail:
            print(line)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def _default_campaign_config() -> dict:
    """Sensible defaults — override via --config JSON file."""
    return {
        "sample_size": 0,
        "exploration_sample_size": 0,
        "exclude_nodes": [],
        "pipeline_overrides": {},
        "optimization": {
            "patience": 3,
            "max_rounds": 10,
            "n_variants": 5,
            "creativity": 0.7,
            "improvement_threshold": 0.01,
            "seed": 42,
            "enable_l2": True,
            "enable_l3": True,
            "l2_patience": 2,
            "l3_patience": 1,
            "l2_temperature": 0.3,
            "l3_temperature": 0.5,
            "enable_critique": True,
            "degradation_threshold": 0.4,
            "backend_warning_threshold": 2,
            "max_failures": 15,
        },
        "eval_llm": {
            "model": "openai/gpt-oss-120b",
            "provider": "groq",
            "temperature": 0.4,
            "max_tokens": 2000,
        },
    }


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="campaign_runner",
        description="CLI campaign runner for PromptPotter optimization",
    )
    parser.add_argument("--session", default=None, help="Session ID (default: active)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize services and create session")
    p_init.add_argument("--backend-url", default="http://127.0.0.1:8000")
    p_init.add_argument("--backend-id", default="termnorm-local")
    p_init.add_argument("--experiment-id", default="1_production_historical")
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
    p_scan.add_argument("--strict-params", default=None, help="strict_params JSON")

    sub.add_parser("scan-results", help="Show scan analytics and seed campaign")

    p_opt = sub.add_parser("optimize", help="Run optimization loop")
    mode = p_opt.add_mutually_exclusive_group(required=True)
    mode.add_argument("--round", action="store_true", help="Generate then stop for review")
    mode.add_argument("--evaluate", action="store_true", help="Evaluate existing candidates")
    mode.add_argument("--auto", action="store_true", help="Full loop without pausing")

    p_res = sub.add_parser("results", help="Show results and optionally save")
    p_res.add_argument("--save", action="store_true")

    sub.add_parser("status", help="Print session state and campaign log")

    return parser


COMMANDS = {
    "init": cmd_init,
    "task-context": cmd_task_context,
    "scan": cmd_scan,
    "scan-results": cmd_scan_results,
    "optimize": cmd_optimize,
    "results": cmd_results,
    "status": cmd_status,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(COMMANDS[args.command](args))


if __name__ == "__main__":
    main()
