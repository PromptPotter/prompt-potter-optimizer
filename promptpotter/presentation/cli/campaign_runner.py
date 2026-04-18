"""CLI campaign runner — terminal orchestration for PromptPotter optimization.

Dispatch file. Contains the five core campaign-lifecycle commands (init,
scan, show-scan, optimize, show-results), the ``COMMANDS`` registry, and
``main()``. Everything else lives in sibling modules:

- ``result.py`` — ``CommandResult``
- ``session.py`` — ``SessionCtx``, ``load_session``, ``load_campaign_config``
- ``bootstrap.py`` — service init, pipeline config, scoring setup, verbose toggle
- ``parser.py`` — argparse schema
- ``commands/`` — non-lifecycle commands (profile, control, status, set-task)
- ``renderers.py`` — plain-text display helpers for ``show-results``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig

# Windows consoles default to cp1252 which can't print Unicode symbols.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from promptpotter.application.campaign.session_state import new_session_state
from promptpotter.presentation.cli.bootstrap import (
    configure_pipeline,
    init_services_cli,
    load_cli_baseline,
    log_startup_summary,
    prepare_scoring_context,
    set_verbose,
)
from promptpotter.presentation.cli.commands.control import cmd_control
from promptpotter.presentation.cli.commands.profile import cmd_profile
from promptpotter.presentation.cli.commands.status import cmd_status
from promptpotter.presentation.cli.commands.task_context import cmd_task_context
from promptpotter.presentation.cli.parser import build_parser
from promptpotter.presentation.cli.result import CommandResult
from promptpotter.presentation.cli.session import (
    load_campaign_config,
    load_session,
)

logger = logging.getLogger("promptpotter.presentation.cli")


# ─────────────────────────────────────────────────────────────────────────────
# Campaign lifecycle commands
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_init(args: argparse.Namespace) -> CommandResult:
    """Initialize services, load datasets, configure pipeline, create session."""
    from promptpotter.application.campaign.data import prepare_datasets

    file_config = load_campaign_config(args.config)
    dataset_name = args.dataset_name or file_config.get("dataset_name")

    session = await init_services_cli(
        backend_url=args.backend_url,
        backend_id=args.backend_id,
        experiment_id=args.experiment_id,
        dataset_name=dataset_name,
        take_over=True,  # cmd_init always rewrites the pointer
        tenant_id=getattr(args, "tenant", "default"),
    )
    backend_id = session.backend_id  # may have been derived from dataset_name

    profile = session.store.backends.load_connector_profile(backend_id) or {}
    campaign_config = {**profile, **file_config}

    pipeline_params = configure_pipeline(session, cast("CampaignConfig", campaign_config))
    active = list(pipeline_params.get("steps", [])) if pipeline_params else []
    excluded = campaign_config.get("exclude_nodes", [])

    state = new_session_state(
        init_params={
            "backend_url": args.backend_url,
            "backend_id": backend_id,
            "experiment_id": args.experiment_id,
            "dataset_name": dataset_name,
        },
        campaign_config=campaign_config,
        pipeline_params=pipeline_params,
        active_steps=active,
    )
    session_id = session.store.campaigns.create_session(backend_id, state)
    session.store.campaigns.save_active_pointer(session.store.tenant_id, session_id)

    train_data: list[dict] | None = None
    if args.excel_path:
        ds_result = prepare_datasets(session.store, backend_id, args.excel_path)
        train_data = ds_result.train_data
    elif session.queries:
        train_data = session.queries

    baseline, dataset, campaign_rounds, _br = await prepare_scoring_context(
        session,
        train_data,
        cast("CampaignConfig", campaign_config),
        run_baseline=not args.skip_baseline,
        pipeline_params=pipeline_params,
    )

    baseline_acc = campaign_rounds[-1]["accuracy"] if campaign_rounds else 0.0
    state["baseline_prompt_fields"] = baseline.prompt_field_dict()
    state["dataset_count"] = len(dataset)
    state["baseline_accuracy"] = baseline_acc
    session.store.campaigns.save_session(backend_id, session_id, state)

    excl_str = f"{', '.join(excluded)} excluded" if excluded else "none excluded"
    session.store.campaigns.append_log(
        backend_id,
        session_id,
        f"""# Campaign Report — {session_id}

## Setup
- Backend: {backend_id} @ {args.backend_url}
- Active steps: {", ".join(active)} ({excl_str})
- Eval data: {len(dataset)} queries
- Baseline: {baseline_acc:.1%}""",
    )

    return CommandResult(
        data={
            "session_id": session_id,
            "backend_id": backend_id,
            "phase": state["phase"],
            "baseline_accuracy": baseline_acc,
            "dataset_count": len(dataset),
            "active_steps": active,
            "excluded_nodes": excluded,
        },
        human=(
            f"\nSession created: {session_id}\n"
            f"Baseline: {baseline_acc:.1%} ({len(dataset)} queries)"
        ),
    )


async def cmd_recon(args: argparse.Namespace) -> CommandResult:
    """Run reconnaissance pass (sensitivity scan) with provided variants."""
    from promptpotter.application.campaign.campaign_setup import run_recon_and_persist
    from promptpotter.application.recon.recon_advisor import (
        flatten_recon_variants,
        resolve_schema_axes,
    )
    from promptpotter.shared.constants import PROMPT_STRING_FIELDS

    ctx = load_session(args)

    with open(args.variants_file) as f:
        recon_variants = json.load(f)

    session = await init_services_cli(**ctx.init_params)

    resolve_schema_axes(flatten_recon_variants(recon_variants), session.pipeline_schema)
    prompt_axes = [k for k in recon_variants if k in PROMPT_STRING_FIELDS]
    node_axes = [k for k in recon_variants if k not in PROMPT_STRING_FIELDS]
    logger.info(
        "Recon variants resolved: %d prompt axes, %d node axes", len(prompt_axes), len(node_axes)
    )

    baseline = load_cli_baseline(session)
    sample_size = (
        args.sample_size
        if args.sample_size is not None
        else ctx.campaign_config.get("recon_sample_size", 0)
    )
    _recon_bl, _baseline_opt, recon_df, _axis_profiles = await run_recon_and_persist(
        baseline,
        ctx.campaign_config,
        recon_variants,
        session.queries or [],
        session=session,
        recon_sample_size=sample_size,
        experiment_id=ctx.init_params["experiment_id"],
        session_id=ctx.session_id,
        log=logger.info,
    )

    ctx.state["recon_variants"] = recon_variants
    records = recon_df.to_dict(orient="records") if recon_df is not None else []
    best = max(records, key=lambda r: r.get("accuracy", 0)) if records else {}
    ctx.save_phase(
        "recon",
        log=f"## Recon\n- Axes: {len(recon_variants)}, variants: {len(records)}\n"
        f"- Best: {best.get('axis', '?')}={best.get('value_preview', '?')} "
        f"\u2192 {best.get('accuracy', 0):.1%} (delta: {best.get('delta', 0):+.1%})",
    )
    return CommandResult(data={"n_variants": len(records), "best": best})


async def cmd_show_recon(args: argparse.Namespace) -> CommandResult:
    """Show recon analytics and seed campaign from recon winner."""
    import pandas as pd

    from promptpotter.application.recon.recon_report import finalize_scan

    ctx = load_session(args)
    session = await init_services_cli(**ctx.init_params)

    recon_data = session.store.campaigns.load_recon_results(ctx.backend_id, ctx.session_id)
    if not recon_data:
        sys.exit("ERROR: No recon results. Run 'recon' first.")

    recon_df = pd.DataFrame(recon_data["recon_df"])
    axis_profiles = recon_data["axis_profiles"]

    human_parts: list[str] = []
    if not recon_df.empty:
        best_row = recon_df.loc[recon_df["accuracy"].idxmax()]
        human_parts.append(
            f"Recon results: {len(recon_df)} variants across {recon_df['axis'].nunique()} axes"
        )
        human_parts.append(
            f"Best: {best_row['axis']}={best_row.get('value_preview', '?')} "
            f"({best_row['accuracy']:.1%}, delta={best_row.get('delta', 0):+.1%})"
        )

    recon_baseline_sp = load_cli_baseline(session)
    campaign_rounds: list = []
    finalize_scan(
        recon_df,
        axis_profiles,
        recon_baseline_sp.to_job_search_point(),
        ctx.recon_variants,
        campaign_rounds=campaign_rounds,
        campaign_config=ctx.campaign_config,
    )

    if campaign_rounds:
        ctx.state["baseline_accuracy"] = campaign_rounds[-1].get("accuracy", 0.0)
        ctx.state["baseline_prompt_fields"] = campaign_rounds[-1].get("prompt_fields", {})

    ctx.save_phase("recon-results")
    return CommandResult(
        data={
            "n_variants": len(recon_df),
            "baseline_accuracy": ctx.state.get("baseline_accuracy", 0.0),
        },
        human="\n".join(human_parts) if human_parts else None,
    )


async def cmd_optimize(args: argparse.Namespace) -> CommandResult:
    """Run optimization loop. Dashboard is dashboard.json in the cycle dir."""
    from promptpotter.application.campaign.campaign_setup import load_recon_brief
    from promptpotter.application.campaign.data import extract_campaign_baseline
    from promptpotter.application.campaign.runner import (
        run_optimization as _orch_run_optimization,
    )
    from promptpotter.application.optimization.pipeline import set_round_recorder
    from promptpotter.infrastructure.persistence.control import CampaignControlReader
    from promptpotter.infrastructure.persistence.round_recorder import RoundRecorder

    ctx = load_session(args)
    campaign_config = ctx.campaign_config

    session = await init_services_cli(**ctx.init_params)
    pipeline_params = configure_pipeline(session, campaign_config)
    train_data = session.queries or []

    resume_from_round: int | None = getattr(args, "resume_from_round", None)
    if resume_from_round is not None:
        prior_cycle = ctx.state.get("cycle_id")
        if not prior_cycle:
            raise ValueError(
                "--from requires an active cycle on this session; run `optimize` first"
            )
        if resume_from_round < 0:
            raise ValueError(f"--from must be >= 0, got {resume_from_round}")
        logger.info("Resuming cycle %s from after round %d", prior_cycle, resume_from_round)
    else:
        prior_cycle = ctx.state.get("cycle_id")
        if prior_cycle:
            logger.info("Resuming cycle %s", prior_cycle)

    log_startup_summary(
        session,
        pipeline_params,
        len(train_data),
        ctx.backend_url,
        ctx.init_params.get("dataset_name"),
    )
    session_dir = session.store.campaigns._session_dir(ctx.backend_id, ctx.session_id)
    logger.info("Session: %s", session_dir)

    # Re-run baseline (fast — cached) to populate baseline_results for critique
    has_baseline = ctx.state.get("baseline_accuracy", 0) > 0
    _baseline, dataset, campaign_rounds, _baseline_results = await prepare_scoring_context(
        session,
        train_data,
        campaign_config,
        pipeline_params=pipeline_params,
        run_baseline=has_baseline,
    )

    baseline_acc = campaign_rounds[-1].get("accuracy", 0.0) if campaign_rounds else 0.0
    recon_brief = load_recon_brief(session, ctx.session_id, ctx.recon_variants, baseline_acc)

    campaign_config.setdefault("optimization", {}).pop("pause_before_scoring", None)
    ctx.save_phase("optimizing")

    set_round_recorder(RoundRecorder(session_dir / "rounds"))

    baseline = extract_campaign_baseline(campaign_rounds)
    state_path = session_dir / "dashboard.json"
    try:
        cycle_result = await _orch_run_optimization(
            dataset,
            campaign_config,
            baseline=baseline,
            session=session,
            recon_brief=recon_brief,
            experiment_id=ctx.state.get("experiment_id"),
            task_context=ctx.task_context,
            session_id=ctx.session_id,
            control=CampaignControlReader(session_dir).check,
            cycle_id=ctx.state.get("cycle_id"),
            resume_from_round_override=resume_from_round,
        )
    finally:
        set_round_recorder(None)

    ctx.state["cycle_id"] = cycle_result.cycle_id
    ctx.state["best_accuracy"] = cycle_result.best_accuracy
    ctx.save_phase("optimize")

    result_path = session_dir / "optimize_result.json"
    result_path.write_text(
        json.dumps(cycle_result.model_dump(), indent=2, default=str), encoding="utf-8"
    )

    return CommandResult(
        data=cycle_result.model_dump(),
        human=(
            f"Campaign: {cycle_result.cycle_id}\nDashboard: {state_path}\nResult: {result_path}"
        ),
    )


async def cmd_results(args: argparse.Namespace) -> CommandResult:
    """Show campaign results, optionally save winner."""
    from promptpotter.application.campaign.utils import save_campaign_winner
    from promptpotter.presentation.cli.renderers import (
        render_campaign_summary,
        render_flip_tracking,
        render_lineage,
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
    flips = render_flip_tracking(campaign_rounds)
    if flips:
        human_parts.append(flips)
    lineage = render_lineage(campaign_rounds)
    if lineage:
        human_parts.append(lineage)

    if args.save:
        save_campaign_winner(
            campaign_rounds,
            ctx.campaign_config,
            session.store,
            ctx.backend_id,
            campaign_id=ctx.state.get("experiment_id", ""),
        )
        human_parts.append("Winner saved.")

    session.store.campaigns.append_log(
        ctx.backend_id,
        ctx.session_id,
        f"""## Results
- Rounds: {len(campaign_rounds)}
- Best: {best.get("accuracy", 0):.1%} (round {best.get("round", "?")})
- Saved: {args.save}""",
    )

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
# Dispatch
# ─────────────────────────────────────────────────────────────────────────────


COMMANDS = {
    "init": cmd_init,
    "set-task": cmd_task_context,
    "recon": cmd_recon,
    "show-recon": cmd_show_recon,
    "optimize": cmd_optimize,
    "control": cmd_control,
    "profile": cmd_profile,
    "show-results": cmd_results,
    "show-status": cmd_status,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    set_verbose(bool(getattr(args, "verbose", False)))

    result = asyncio.run(COMMANDS[args.command](args))
    if result is None:
        return
    if args.json_output or result.human is None:
        print(json.dumps(result.data, indent=2, default=str))
    else:
        print(result.human)


if __name__ == "__main__":
    main()
