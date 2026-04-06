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
from typing import Any

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


def _die(msg: str) -> None:
    sys.exit(f"ERROR: {msg}")


async def _reconstruct_svc(init_params: dict):
    """Reconstruct BackendSession from stored init params (~1s)."""
    from promptpotter.cli._services import init_services

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
    from promptpotter.cli._services import (
        configure_pipeline,
        init_services,
        prepare_datasets,
        prepare_eval_context,
        show_pipeline_snapshot,
    )

    session = await init_services(
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

    await show_pipeline_snapshot(session)
    pipeline_params = configure_pipeline(session, campaign_config)

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

    bid = session.backend_id
    sid = session.store.sessions.create(bid, state)
    session.store.sessions.save_active_pointer(bid, sid)

    # Load data and run baseline (may be long-running)
    train_data = None
    if args.excel_path:
        train_data, _st = prepare_datasets(
            session.store, session.backend_id, excel_path=args.excel_path
        )
    elif session.queries:
        train_data = session.queries

    baseline, dataset, campaign_rounds, _br = await prepare_eval_context(
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
    session.store.sessions.save(bid, sid, state)

    excl_str = f"{', '.join(excluded)} excluded" if excluded else "none excluded"
    session.store.sessions.append_log(
        bid,
        sid,
        f"""# Campaign Report — {sid}

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
                    "session_id": sid,
                    "backend_id": bid,
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
        print(f"\nSession created: {sid}")
        print(f"Baseline: {baseline_acc:.1%} ({len(dataset)} queries)")


async def cmd_task_context(args: argparse.Namespace) -> None:
    """Decompose task description into structured domain context."""
    from promptpotter.cli._services import decompose_task_context

    store = ProjectStore()
    state, bid, sid = store.sessions.load_active(args.session)

    task_description = ""
    if args.task_file:
        task_description = Path(args.task_file).read_text(encoding="utf-8")
    elif args.task_text:
        task_description = args.task_text
    if not task_description:
        _die("Provide --task-file or --task-text")

    session = await _reconstruct_svc(state["init_params"])
    task_context = await decompose_task_context(
        task_description,
        state["campaign_config"],
        session,
    )

    state["task_context"] = task_context
    state["phase"] = "task-context"
    session.store.sessions.save(bid, sid, state)
    session.store.sessions.append_log(
        bid, sid, f"## Task Context\n{json.dumps(task_context, indent=2)}"
    )


async def cmd_scan(args: argparse.Namespace) -> None:
    """Run sensitivity scan with provided variants."""
    from promptpotter.cli._services import (
        configure_pipeline,
        resolve_scan_variants,
        run_sensitivity_scan,
    )
    from promptpotter.services.campaign.init import load_baseline_prompt

    store = ProjectStore()
    state, bid, sid = store.sessions.load_active(args.session)

    if not args.variants_file:
        _die("--variants-file required")
    with open(args.variants_file) as f:
        scan_variants = json.load(f)

    session = await _reconstruct_svc(state["init_params"])
    resolve_scan_variants(scan_variants, session=session)
    configure_pipeline(session, state["campaign_config"])

    _pn = session.pipeline_schema.prompt_node_names() if session.pipeline_schema else []
    baseline = load_baseline_prompt(session.exp_data, prompt_node_names=_pn)
    train_data = session.queries or []

    sample_size = (
        args.sample_size
        if args.sample_size is not None
        else state["campaign_config"].get("exploration_sample_size", 0)
    )
    _scan_bl, scan_df, _axis_profiles = await run_sensitivity_scan(
        baseline,
        state["campaign_config"],
        scan_variants,
        train_data,
        scan_sample_size=sample_size,
        session=session,
        experiment_id=state["init_params"]["experiment_id"],
        session_id=sid,
    )

    state["scan_variants"] = scan_variants
    state["phase"] = "scan"
    session.store.sessions.save(bid, sid, state)

    records = scan_df.to_dict(orient="records") if scan_df is not None else []
    best = max(records, key=lambda r: r.get("accuracy", 0)) if records else {}
    session.store.sessions.append_log(
        bid,
        sid,
        f"""## Scan
- Axes: {len(scan_variants)}, variants: {len(records)}
- Best: {best.get("axis", "?")}={best.get("value_preview", "?")} → {best.get("accuracy", 0):.1%} (delta: {best.get("delta", 0):+.1%})""",
    )


async def cmd_scan_results(args: argparse.Namespace) -> None:
    """Show scan analytics and seed campaign from scan winner."""
    from promptpotter.cli._services import seed_campaign_from_scan
    from promptpotter.services.campaign.init import load_baseline_prompt

    store = ProjectStore()
    state, bid, sid = store.sessions.load_active(args.session)
    session = await _reconstruct_svc(state["init_params"])

    scan_data = session.store.sessions.load_scan_results(bid, sid)
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
    seed_campaign_from_scan(
        scan_df,
        axis_profiles,
        scan_baseline_sp,
        state.get("scan_variants", {}),
        campaign_rounds,
        state["campaign_config"],
    )

    if campaign_rounds:
        state["baseline_accuracy"] = campaign_rounds[-1].get("accuracy", 0.0)
        state["baseline_prompt_fields"] = campaign_rounds[-1].get("prompt_fields", {})

    state["phase"] = "scan-results"
    session.store.sessions.save(bid, sid, state)


async def cmd_optimize(args: argparse.Namespace) -> None:
    """Run optimization loop. Dashboard is campaign_state.json in session dir."""
    from promptpotter.cli._services import (
        build_scan_context,
        configure_pipeline,
        prepare_eval_context,
        run_optimization,
    )
    from promptpotter.models.opt_search_point import OptSearchPoint

    store = ProjectStore()
    state, bid, sid = store.sessions.load_active(args.session)
    campaign_config = state["campaign_config"]

    session = await _reconstruct_svc(state["init_params"])
    pipeline_params = configure_pipeline(session, campaign_config)
    train_data = session.queries or []

    # Re-run baseline (fast — cached) to populate baseline_results for critique
    _has_baseline = state.get("baseline_accuracy", 0) > 0
    _baseline, dataset, campaign_rounds, baseline_results = await prepare_eval_context(
        session,
        train_data,
        campaign_config,
        pipeline_params=pipeline_params,
        run_baseline=_has_baseline,
    )

    # Seed campaign_rounds with stored baseline when no eval has been run yet
    if not campaign_rounds and state.get("baseline_prompt_fields"):
        bl_ps = OptSearchPoint.from_prompt_fields(state["baseline_prompt_fields"])
        campaign_rounds.append(
            {
                "round": "baseline",
                "accuracy": state.get("baseline_accuracy", 0.0),
                "prompt_fields": bl_ps,
                "results": baseline_results or [],
            }
        )

    # Load scan context if available
    state["_session_id"] = sid  # for build_scan_context
    scan_context = build_scan_context(session, state, campaign_rounds, pipeline_params)

    # HITL: --round sets pause_before_eval via the control surface
    if args.round:
        campaign_config.setdefault("optimization", {})["pause_before_eval"] = True
    else:
        campaign_config.setdefault("optimization", {}).pop("pause_before_eval", None)

    state["phase"] = "optimizing"
    session.store.sessions.save(bid, sid, state)

    # Bidirectional control — CLI provides FileControlSurface as on_checkpoint
    from promptpotter.services.campaign.persistence_emitter import FileControlSurface
    from promptpotter.services.campaign.state import RunCallbacks

    session_dir = session.store.sessions._session_dir(bid, sid)
    control = FileControlSurface(session_dir / "campaign_state.json")
    control_cb = RunCallbacks(on_checkpoint=control.check)

    # Round recorder — write rounds/round_NNN.json with full action traces
    from promptpotter.config.optimizer_pipeline import set_round_recorder
    from promptpotter.services.campaign.persistence_emitter import RoundRecorder

    recorder = RoundRecorder(session_dir / "rounds")
    set_round_recorder(recorder)

    try:
        campaign_rounds, cycle_result = await run_optimization(
            campaign_rounds,
            dataset,
            campaign_config,
            session=session,
            pipeline_params=pipeline_params,
            scan_context=scan_context,
            experiment_id=state.get("experiment_id"),
            task_context=state.get("task_context"),
            session_id=sid,
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

    state["phase"] = "optimize"
    state["cycle_id"] = _emitter_state.get("cycle_id")
    state["best_accuracy"] = _emitter_state.get("best", state.get("baseline_accuracy", 0.0))
    session.store.sessions.save(bid, sid, state)

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
    store = ProjectStore()
    _state, bid, sid = store.sessions.load_active(args.session)
    session_dir = store.sessions._session_dir(bid, sid)
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
    bid = args.backend_id

    if args.save:
        # Save active session's campaign_config as the backend profile
        state, bid, _sid = store.sessions.load_active(args.session)
        profile = state.get("campaign_config", {})
        store.backends.save_connector_profile(bid, profile)
        print(f"Profile saved for '{bid}'.")
        return

    if args.set:
        key, raw_value = args.set
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        profile = store.backends.load_connector_profile(bid) or {}
        profile[key] = value
        store.backends.save_connector_profile(bid, profile)
        print(f"Profile '{bid}': {key} = {json.dumps(value)}")
        return

    # Default: --show
    profile = store.backends.load_connector_profile(bid)
    if not profile:
        print(f"No connector profile for '{bid}'. Use --save or --set to create one.")
        return
    print(json.dumps(profile, indent=2, default=str))


async def cmd_results(args: argparse.Namespace) -> None:
    """Show campaign results, optionally save winner."""
    from promptpotter.cli._services import (
        show_campaign_summary,
        show_flip_tracking,
        show_lineage_chain,
    )
    from promptpotter.services.campaign.persistence import save_campaign_winner

    store = ProjectStore()
    state, bid, sid = store.sessions.load_active(args.session)
    session = await _reconstruct_svc(state["init_params"])

    campaigns = session.store.campaigns.list_campaigns(bid)
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
        trial = session.store.campaigns.load_trial(bid, cycle_id, i)
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
                    "baseline_accuracy": state.get("baseline_accuracy", 0),
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
        show_campaign_summary(campaign_rounds)
        show_flip_tracking(campaign_rounds)
        show_lineage_chain(campaign_rounds)

    if args.save:
        save_campaign_winner(
            campaign_rounds,
            state["campaign_config"],
            session.store,
            bid,
            experiment_id=state.get("experiment_id", ""),
        )
        if not getattr(args, "json_output", False):
            print("Winner saved.")

    best = max(campaign_rounds, key=lambda r: r.get("accuracy", 0)) if campaign_rounds else {}
    session.store.sessions.append_log(
        bid,
        sid,
        f"""## Results
- Rounds: {len(campaign_rounds)}
- Best: {best.get("accuracy", 0):.1%} (round {best.get("round", "?")})
- Saved: {args.save}""",
    )


async def cmd_status(args: argparse.Namespace) -> None:
    """Print live dashboard from campaign_state.json (or session state if idle)."""
    store = ProjectStore()
    state, bid, sid = store.sessions.load_active(args.session)
    session_dir = store.sessions._session_dir(bid, sid)
    state_path = session_dir / "campaign_state.json"

    # Machine-readable JSON mode
    if getattr(args, "json_output", False):
        output: dict[str, Any] = {
            "session_id": sid,
            "phase": state["phase"],
            "backend_id": bid,
            "backend_url": state["init_params"]["backend_url"],
            "baseline_accuracy": state.get("baseline_accuracy", 0),
            "dataset_count": state.get("dataset_count"),
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
    print(f"Session: {sid}")
    print(f"Phase: {state['phase']}")
    print(f"Backend: {bid} @ {state['init_params']['backend_url']}")
    print(f"Eval data: {state.get('dataset_count', '?')} queries")
    print(f"Baseline: {state.get('baseline_accuracy', 0):.1%}")

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

    log = store.sessions.load_log(bid, sid)
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
