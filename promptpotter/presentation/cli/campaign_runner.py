"""CLI campaign runner — terminal orchestration for PromptPotter optimization.

Single dispatch file: argparse schema, every ``cmd_*`` function, the
``COMMANDS`` registry, and ``main()``. Sibling modules carry only the
narrow surfaces a command body shouldn't have to know about:

- ``result.py`` — ``CommandResult``
- ``session.py`` — ``SessionCtx``, ``load_session``, ``load_campaign_config``
- ``bootstrap.py`` — service init, pipeline config, scoring setup, verbose toggle
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Windows consoles default to cp1252 which can't print Unicode symbols.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from promptpotter.application.campaign.campaign_setup import new_session_state
from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.presentation.cli.bootstrap import (
    configure_pipeline,
    init_services_cli,
    load_cli_baseline,
    log_startup_summary,
    prepare_scoring_context,
    set_verbose,
)
from promptpotter.presentation.cli.result import CommandResult
from promptpotter.presentation.cli.session import (
    load_campaign_config,
    load_session,
)

logger = logging.getLogger("promptpotter.presentation.cli")


# Signal name → ``control.json`` (key, value). Source of truth for both the
# ``control`` subparser ``choices=`` list and ``cmd_control`` dispatch.
SIGNAL_ACTIONS: dict[str, tuple[str, str | bool]] = {
    "pause": ("requested_state", "pause"),
    "resume": ("requested_state", "resume"),
    "stop": ("requested_state", "stop"),
}


def build_parser() -> argparse.ArgumentParser:
    """Argparse schema for every CLI subcommand. Pure data."""
    parser = argparse.ArgumentParser(
        prog="python -m promptpotter",
        description="CLI campaign runner for PromptPotter optimization",
    )
    parser.add_argument("--session", default=None, help="Session ID (default: active)")
    parser.add_argument(
        "--tenant",
        default="default",
        help="Tenant partition under .promptpotter/projects/ (default: 'default')",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logs (timestamps, module tags, every INFO line)",
    )
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

    p_tc = sub.add_parser("set-task", help="Decompose and set task description")
    p_tc.add_argument("--task-file", default=None)
    p_tc.add_argument("--task-text", default=None)

    p_opt = sub.add_parser("optimize", help="Run optimization loop")
    p_opt.add_argument(
        "--from",
        dest="resume_from_round",
        type=int,
        default=None,
        metavar="ROUND",
        help="Resume the active cycle from after round N. Archives trial "
        "files for rounds > N into archived/resumed_at_<ts>/, rebuilds the "
        "trial index, and loads trial_N as the restart baseline. Omit to "
        "resume from the latest completed round (default).",
    )
    p_opt.add_argument(
        "--no-divergence-check",
        dest="no_divergence_check",
        action="store_true",
        help="On resume, rescore cached traces under the current scorer "
        "but skip the decision-replay halt — continue even if a prior "
        "round's winner would flip under the new policy. Use when you "
        "accept that historical trajectory stays as-recorded.",
    )
    p_opt.add_argument(
        "--fork-on-divergence",
        dest="fork_on_divergence",
        action="store_true",
        help="On divergence, mint a sibling cycle that inherits trials "
        "before the divergence point (with parent_cycle_id) and re-run "
        "the divergent round under the current scorer. Without this "
        "flag, divergence halts so you can review and decide.",
    )

    p_ctl = sub.add_parser("control", help="Write control signal to dashboard")
    p_ctl.add_argument(
        "signal",
        choices=list(SIGNAL_ACTIONS.keys()),
        help="Signal to send to the running optimizer",
    )

    p_prof = sub.add_parser("profile", help="Manage backend profile (per-backend defaults)")
    p_prof.add_argument("--backend-id", default="local")
    prof_mode = p_prof.add_mutually_exclusive_group()
    prof_mode.add_argument(
        "--show", action="store_true", default=True, help="Show profile (default)"
    )
    prof_mode.add_argument(
        "--save", action="store_true", help="Save active session config as profile"
    )
    prof_mode.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="Set a profile field")

    p_res = sub.add_parser("show-results", help="Show results and optionally save")
    p_res.add_argument("--save", action="store_true")

    sub.add_parser("show-status", help="Emit raw JSON dashboard state")

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# Campaign lifecycle commands
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_init(args: argparse.Namespace) -> CommandResult:
    """Initialize services, load datasets, configure pipeline, create session.

    Pure prep: no backend calls, no baseline scoring. The baseline runs as
    phase 0 of ``optimize`` on the ``sp_budget_ttest`` slice.
    """
    from promptpotter.application.campaign.data import prepare_datasets

    file_config = load_campaign_config(args.config)
    dataset_name = args.dataset_name or file_config.get("dataset_name")

    if not dataset_name:
        from promptpotter.presentation.cli.session import no_dataset_hint

        raise SystemExit(
            "ERROR: init requires a dataset name. Silent defaults to the "
            "TermNorm production experiment are no longer allowed.\n\n" + no_dataset_hint()
        )

    # Auto-load the dataset's campaign.json when --config wasn't given.
    # Without this, the session persists with scoring=null and default
    # optimization knobs — the dataset's own file is the intended source of truth.
    if not args.config:
        default_config_path = Path("datasets") / dataset_name / "campaign.json"
        if default_config_path.exists():
            file_config = load_campaign_config(str(default_config_path))

    session = await init_services_cli(
        backend_url=args.backend_url,
        backend_id=args.backend_id,
        experiment_id=args.experiment_id,
        dataset_name=dataset_name,
        take_over=True,  # cmd_init always rewrites the pointer
        tenant_id=getattr(args, "tenant", "default"),
    )
    backend_id = session.backend_id  # may have been derived from dataset_name

    from promptpotter.application.campaign.config import load_campaign_config as _load_cfg

    profile = session.store.backends.load_connector_profile(backend_id) or {}
    raw_config = {**profile, **file_config}
    campaign_config = _load_cfg(raw_config)

    pipeline_params = configure_pipeline(session, campaign_config)
    active = list(pipeline_params.get("steps", [])) if pipeline_params else []
    excluded = list(campaign_config.exclude_nodes)

    train_data: list = []
    if args.excel_path:
        ds_result = prepare_datasets(session.store, args.excel_path)
        train_data = ds_result.train_data or []
    elif session.queries:
        train_data = session.queries

    baseline = load_cli_baseline(session)
    dataset = train_data

    from promptpotter.domain.cycle_identity import cycle_hash_suffix

    active_steps_for_hash = (
        list(session.pipeline_schema.active_steps) if session.pipeline_schema else []
    )
    cycle_hash = cycle_hash_suffix(
        campaign_config,
        baseline.render(),
        dataset,
        active_steps_for_hash,
    )

    from promptpotter.infrastructure.store import mint_session_id, save_active_pointer

    session_id = mint_session_id()
    cycle_id = f"cycle_{cycle_hash}"

    state = new_session_state(
        init_params={
            "backend_url": args.backend_url,
            "backend_id": backend_id,
            "experiment_id": args.experiment_id,
            "dataset_name": dataset_name,
        },
        campaign_config=campaign_config.model_dump(),
        pipeline_params=pipeline_params,
        active_steps=active,
    )
    state["baseline_prompt_fields"] = baseline.prompt_field_dict()
    state["dataset_count"] = len(dataset)
    state["baseline_accuracy"] = 0.0

    session.store.sessions.create(session_id, state)
    session.store.sessions.ensure_narrative_files(session_id)
    session.store.campaigns.create(
        backend_id,
        cycle_id,
        {"parent_session_id": session_id},
    )
    save_active_pointer(session.store.tenant_id, session_id, cycle_id)
    session.session_id = session_id
    session.cycle_id = cycle_id

    return CommandResult(
        data={
            "session_id": session_id,
            "cycle_id": cycle_id,
            "backend_id": backend_id,
            "phase": state["phase"],
            "dataset_count": len(dataset),
            "active_steps": active,
            "excluded_nodes": excluded,
        },
        human=(
            f"\nSession created: {session_id}\n"
            f"Cycle: {cycle_id}\n"
            f"Dataset: {len(dataset)} queries (baseline runs in optimize phase 0)"
        ),
    )


def _build_live_display(
    args: argparse.Namespace,
    *,
    session,
    campaign_config,
    baseline_acc: float,
):
    """Pick the live display: full notebook parity in ``-v``, concise otherwise."""
    from promptpotter.presentation.views.display_primitives import set_display_tags
    from promptpotter.shared.scoring import split_scoring_block

    set_display_tags(session.pipeline_schema)
    scoring_formula = split_scoring_block(campaign_config.scoring).per_query

    opt = campaign_config.optimization
    max_rounds = opt.max_rounds or 999
    if getattr(args, "verbose", False):
        from promptpotter.presentation.ui.campaign.notebook_display import NotebookDisplay

        return NotebookDisplay(
            campaign_rounds=[],
            baseline_acc=baseline_acc,
            l1_patience=opt.l1_patience,
            pipeline_schema=session.pipeline_schema,
            store=session.store,
            scoring_formula=scoring_formula,
        )
    from promptpotter.presentation.views.live_cli import CliDisplay

    return CliDisplay(
        baseline_acc=baseline_acc,
        max_rounds=max_rounds,
        l1_patience=opt.l1_patience,
        sp_budget_ttest=campaign_config.sp_budget_ttest,
        scoring_formula=scoring_formula,
        pipeline_schema=session.pipeline_schema,
    )


async def cmd_optimize(args: argparse.Namespace) -> CommandResult:
    """Run optimization loop. Dashboard is dashboard.json in the cycle dir."""
    from promptpotter.application.campaign.callbacks import RunListener
    from promptpotter.application.campaign.data import extract_campaign_baseline
    from promptpotter.application.campaign.runner import (
        run_optimization as _orch_run_optimization,
    )
    from promptpotter.application.optimization.pipeline import (
        get_round_recorder,
        set_round_recorder,
    )
    from promptpotter.infrastructure.persistence.control import make_control_check
    from promptpotter.infrastructure.persistence.round_recorder import RoundRecorder
    from promptpotter.infrastructure.persistence.session_emitter import (
        CampaignPersistenceEmitter,
    )

    ctx = load_session(args)
    campaign_config = ctx.campaign_config

    session = await init_services_cli(**ctx.init_params)
    session.session_id = ctx.session_id
    session.cycle_id = ctx.cycle_id
    pipeline_params = configure_pipeline(session, campaign_config)
    train_data = session.queries or []

    resume_from_round: int | None = getattr(args, "resume_from_round", None)
    if resume_from_round is not None:
        if not ctx.cycle_id:
            raise ValueError(
                "--from requires an active cycle on this session; run `optimize` first"
            )
        if resume_from_round < 0:
            raise ValueError(f"--from must be >= 0, got {resume_from_round}")
        logger.info("Resuming cycle %s from after round %d", ctx.cycle_id, resume_from_round)
    else:
        if ctx.cycle_id:
            logger.info("Resuming cycle %s", ctx.cycle_id)

    log_startup_summary(
        session,
        pipeline_params,
        len(train_data),
        ctx.backend_url,
        ctx.init_params["dataset_name"],
    )
    session_dir = session.store.sessions.session_dir(ctx.session_id)
    campaign_dir = session.store.campaigns.campaign_dir(ctx.cycle_id)
    logger.info("Session: %s", session_dir)
    logger.info("Campaign: %s", campaign_dir)

    # Build the emitter BEFORE baseline so the dashboard ticks through
    # the BASELINE phase, not just the L1 rounds.  The emitter reads any
    # prior dashboard.json for resume continuity; baseline accuracy is
    # stamped later during the INIT exit phase.
    pre_baseline_acc = ctx.state.get("baseline_accuracy", 0.0)
    opt = campaign_config.optimization
    active_steps = list(session.pipeline_schema.active_steps) if session.pipeline_schema else []
    emitter = CampaignPersistenceEmitter.for_session(
        pre_baseline_acc,
        ctx.cycle_id,
        project_root=str(session.store.base_dir),
        session_id=ctx.session_id,
        max_rounds=opt.max_rounds or 999,
        l1_patience=opt.l1_patience,
        active_nodes=active_steps,
        model=campaign_config.optimizer_llm.model or "",
        n_variants=opt.n_variants,
        sp_budget_ttest=campaign_config.sp_budget_ttest,
        resumed_from_round=resume_from_round,
        dataset_count=ctx.state["dataset_count"],
        backend_id=ctx.backend_id,
        recorder_provider=get_round_recorder,
    )
    control_reader = make_control_check(session_dir)
    listener = RunListener(emitter=emitter, control=control_reader)

    # Build the display BEFORE baseline runs so baseline's per-query output
    # reaches the terminal. Without this, ``listener.display`` is None during
    # baseline, ``RunListener.on_sample_scored`` silently drops, and the CLI
    # goes dark for the entire BASELINE phase. The post-baseline re-assignment
    # keeps the display wired across the handoff into L1 (idempotent).
    display = _build_live_display(
        args,
        session=session,
        campaign_config=campaign_config,
        baseline_acc=pre_baseline_acc,
    )
    listener.display = display

    # Re-run baseline (fast — cached) to populate baseline_results for L1 critique
    _baseline, dataset, campaign_rounds, _baseline_results = await prepare_scoring_context(
        session,
        train_data,
        campaign_config,
        pipeline_params=pipeline_params,
        listener=listener,
    )
    ctx.save_phase("optimizing")

    set_round_recorder(RoundRecorder(campaign_dir / "rounds"))

    baseline = extract_campaign_baseline(campaign_rounds)
    # Display was built with ``pre_baseline_acc`` from resume state (0.0 on
    # first run or when prior kill happened before baseline finished). The
    # re-run above produced the fresh value — push it in now so per-candidate
    # deltas, round headers, and best-tracking render against the actual
    # baseline instead of 0 %.
    display.set_baseline(baseline.baseline_acc)
    state_path = campaign_dir / "dashboard.json"
    from promptpotter.shared.errors import ResumeDivergenceError

    try:
        cycle_result = await _orch_run_optimization(
            dataset,
            campaign_config,
            baseline=baseline,
            session=session,
            experiment_id=ctx.state["experiment_id"],
            task_context=ctx.task_context,
            session_id=ctx.session_id,
            display=display,
            control=control_reader,
            cycle_id=ctx.cycle_id,
            resume_from_round_override=resume_from_round,
            emitter=emitter,
            no_divergence_check=getattr(args, "no_divergence_check", False),
            fork_on_divergence=getattr(args, "fork_on_divergence", False),
        )
    except ResumeDivergenceError as div:
        return CommandResult(
            data={
                "error": "resume_divergence",
                "round": div.round_num,
                "kind": div.kind,
                "recorded_outcome": div.recorded_outcome,
                "current_outcome": div.current_outcome,
            },
            human=(
                f"{div}\n\n"
                f"Checked decisions: round_winner, elimination_cut, "
                f"l2_escalation_trigger, l3_escalation_trigger. "
                f"(probe_round_commitment is recorded but not divergence-gated — "
                f"it depends on L2's LLM output, which is invariant under a "
                f"pure scorer swap.)\n\n"
                f"Rerun with `--fork-on-divergence` to branch a sibling cycle "
                f"here under the current scorer, revert "
                f"`campaign.json::scoring` to continue the original trajectory, "
                f"or pass `--no-divergence-check` to accept the divergence."
            ),
        )
    finally:
        set_round_recorder(None)

    ctx.state["best_accuracy"] = cycle_result.best_accuracy
    ctx.save_phase("optimize")

    result_path = campaign_dir / "optimize_result.json"
    return CommandResult(
        data=cycle_result.model_dump(),
        human=(
            f"Campaign: {cycle_result.cycle_id}\nDashboard: {state_path}\nResult: {result_path}"
        ),
    )


async def cmd_results(args: argparse.Namespace) -> CommandResult:
    """Show campaign results, optionally save winner."""
    from promptpotter.application.campaign.utils import save_campaign_winner
    from promptpotter.presentation.views import (
        collect_prefix_events,
        render_adaptive_prefix,
        render_campaign_summary,
        render_flip_tracking,
        render_hard_sample_heatmap,
        render_lineage,
        render_progress,
    )

    ctx = load_session(args)
    session = await init_services_cli(**ctx.init_params)

    campaigns = session.store.campaigns.list_all(ctx.backend_id)
    if not campaigns:
        return CommandResult(data={"error": "no_campaigns"}, human="No campaigns found.")

    latest = campaigns[-1]
    cycle_id = latest.get("campaign_id", "")
    campaign_rounds = []
    for i in range(latest.get("n_trials", 0)):
        trial = session.store.campaigns.load_trial(ctx.backend_id, cycle_id, i)
        if trial:
            campaign_rounds.append(trial)

    best = max(campaign_rounds, key=lambda r: r.get("accuracy", 0)) if campaign_rounds else {}

    human_parts = [render_campaign_summary(campaign_rounds)]
    progress = render_progress(campaign_rounds)
    if progress:
        human_parts.append(progress)
    flips = render_flip_tracking(campaign_rounds)
    if flips:
        human_parts.append(flips)
    lineage = render_lineage(campaign_rounds)
    if lineage:
        human_parts.append(lineage)

    prefix_events = collect_prefix_events(campaign_rounds)
    if prefix_events:
        human_parts.append(render_adaptive_prefix(prefix_events))

    hard_samples_path = session.store.campaigns.campaign_dir(cycle_id) / "hard_samples.json"
    if hard_samples_path.exists():
        try:
            heatmap = render_hard_sample_heatmap(
                json.loads(hard_samples_path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError):
            heatmap = ""
        if heatmap:
            human_parts.append(heatmap)

    if args.save:
        save_campaign_winner(
            campaign_rounds,
            ctx.campaign_config,
            session.store,
            ctx.backend_id,
            campaign_id=ctx.state.get("experiment_id", ""),
        )
        human_parts.append("Winner saved.")

    return CommandResult(
        data={
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
        human="\n".join(human_parts),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Auxiliary commands (HITL control, profile, status, task context)
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_control(args: argparse.Namespace) -> CommandResult:
    """Write a HITL control signal to ``control.json``."""
    from promptpotter.infrastructure.store.base import write_json

    ctx = load_session(args)
    control_path = ctx.store.sessions.control_path(ctx.session_id)
    if not control_path.exists():
        sys.exit(f"ERROR: No {control_path.name} — run 'optimize' first.")

    control = json.loads(control_path.read_text(encoding="utf-8"))
    key, value = SIGNAL_ACTIONS[args.signal]
    control[key] = value
    write_json(control_path, control)

    return CommandResult(
        data={"signal": args.signal, "control": control},
        human=f"Control: {args.signal} requested.",
    )


async def cmd_profile(args: argparse.Namespace) -> CommandResult:
    """Manage connector profile — persistent per-backend defaults."""
    from promptpotter.infrastructure.store import build_stores, read_active_pointer

    store = build_stores(tenant_id=getattr(args, "tenant", "default"))
    backend_id = args.backend_id

    if args.save:
        _tid, pointer_sid, _cid = read_active_pointer()
        sid = getattr(args, "session", None) or pointer_sid
        state = store.sessions.read(sid) if sid else None
        if not state:
            return CommandResult(
                data={"saved": False, "error": "no_active_session"},
                human="ERROR: No active session — run `init` first.",
            )
        backend_id = backend_id or state.get("init_params", {}).get("backend_id", "")
        profile = state.get("campaign_config", {})
        store.backends.save_connector_profile(backend_id, profile)
        return CommandResult(
            data={"saved": True, "backend_id": backend_id},
            human=f"Profile saved for '{backend_id}'.",
        )

    if args.set:
        key, raw_value = args.set
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        profile = store.backends.load_connector_profile(backend_id) or {}
        profile[key] = value
        store.backends.save_connector_profile(backend_id, profile)
        return CommandResult(
            data={"backend_id": backend_id, "key": key, "value": value},
            human=f"Profile '{backend_id}': {key} = {json.dumps(value)}",
        )

    profile = store.backends.load_connector_profile(backend_id)
    if not profile:
        return CommandResult(
            data={"backend_id": backend_id, "profile": None},
            human=f"No connector profile for '{backend_id}'. Use --save or --set to create one.",
        )
    return CommandResult(
        data={"backend_id": backend_id, "profile": profile},
        human=json.dumps(profile, indent=2, default=str),
    )


async def cmd_status(args: argparse.Namespace) -> CommandResult:
    """Emit dashboard + control + last result.

    ``dashboard.json`` is the live single-source-of-truth (see
    ``infrastructure/persistence/session_emitter.py``). JSON mode cats it
    alongside ``control.json`` and ``optimize_result.json`` so a human can
    ``jq`` the same shape; human mode delegates to ``render_status``.
    """
    from promptpotter.infrastructure.persistence.control import CONTROL_FILENAME
    from promptpotter.presentation.views import render_status

    ctx = load_session(args)
    session_dir = ctx.store.sessions.session_dir(ctx.session_id)
    campaign_dir = ctx.store.campaigns.campaign_dir(ctx.cycle_id) if ctx.cycle_id else None

    payload: dict[str, Any] = {
        "session_id": ctx.session_id,
        "cycle_id": ctx.cycle_id,
        "backend_id": ctx.backend_id,
        "phase": ctx.state["phase"],
    }
    sources = [("control", session_dir / CONTROL_FILENAME)]
    if campaign_dir is not None:
        sources.extend(
            [
                ("dashboard", campaign_dir / "dashboard.json"),
                ("optimize_result", campaign_dir / "optimize_result.json"),
            ]
        )
    for key, path in sources:
        if path.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                payload[key] = json.loads(path.read_text(encoding="utf-8"))

    human = render_status(
        payload.get("dashboard", {}),
        payload.get("control"),
        payload.get("optimize_result"),
    )
    return CommandResult(data=payload, human=human)


async def cmd_task_context(args: argparse.Namespace) -> CommandResult:
    """Decompose task description into structured domain context."""
    from promptpotter.application.campaign.config import create_llm_client
    from promptpotter.application.optimization.pipeline import (
        decompose_task_context as _svc_decompose,
    )

    ctx = load_session(args)

    if args.task_file:
        task_description = Path(args.task_file).read_text(encoding="utf-8")
    elif args.task_text:
        task_description = args.task_text
    else:
        sys.exit("ERROR: Provide --task-file or --task-text")

    session = await init_services_cli(**ctx.init_params)
    llm_client, model = create_llm_client(ctx.campaign_config)
    task_context, _consultation, was_cached = await _svc_decompose(
        task_description,
        llm_client,
        model,
        store_base_dir=session.store.base_dir if session.store else None,
        backend_id=session.backend_id,
    )
    cache_tag = " (cached)" if was_cached else ""
    logger.info("Task context decomposed%s: %d fields", cache_tag, len(task_context))

    ctx.state["task_context"] = task_context.to_dict()
    ctx.save_phase("task-context")
    return CommandResult(data={"task_context": task_context.to_dict(), "cached": was_cached})


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────────────────────────────


COMMANDS = {
    "init": cmd_init,
    "set-task": cmd_task_context,
    "optimize": cmd_optimize,
    "control": cmd_control,
    "profile": cmd_profile,
    "show-results": cmd_results,
    "show-status": cmd_status,
}


def main() -> None:
    from promptpotter.shared.errors import RequestTooLargeError

    parser = build_parser()
    args = parser.parse_args()
    set_verbose(bool(getattr(args, "verbose", False)))

    try:
        result = asyncio.run(COMMANDS[args.command](args))
    except RequestTooLargeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if result is None:
        return
    if args.json_output or result.human is None:
        print(json.dumps(result.data, indent=2, default=str))
    else:
        print(result.human)


if __name__ == "__main__":
    main()
