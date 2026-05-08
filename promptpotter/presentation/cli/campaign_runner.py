"""CLI entry point — argparse schema, ``cmd_init`` + ``cmd_optimize``, ``main()``.

The CLI is two write verbs: ``init`` creates a session+cycle, ``optimize`` runs
a campaign against it. Reads happen by opening the on-disk artifact tree
(``sessions/{id}/``, ``campaigns/{cycle_id}/``) — ``dashboard.json`` for live
state, ``log.md`` for the digest, ``index.json`` for the final summary
including ``stop_reason``. Stop with Ctrl+C — there is no mid-run pause/resume.

``session.py`` carries ``SessionCtx``/``load_session``/``load_campaign_config``.
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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.infrastructure.store.base import read_text_optional
from promptpotter.presentation.cli.parsers import build_parser
from promptpotter.presentation.cli.session import (
    load_campaign_config,
    load_session,
)

if TYPE_CHECKING:
    from promptpotter.application.bootstrap import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.run_observers import RunObservers
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.presentation.cli.session import SessionCtx
    from promptpotter.presentation.views.live import LiveDisplay

logger = logging.getLogger("promptpotter.presentation.cli")


@dataclass
class CommandResult:
    """``data`` is machine-readable; ``human`` is pre-rendered text. ``main()`` picks one."""

    data: dict[str, Any] | None = None
    human: str | None = None


_VERBOSE = False


def set_verbose(value: bool) -> None:
    """Toggle verbose mode. Called once from ``main()`` before dispatch."""
    global _VERBOSE
    _VERBOSE = value


def log_startup_summary(
    session: Session,
    pipeline_params: dict | None,
    dataset_len: int,
    backend_url: str,
    dataset_name: str | None,
) -> None:
    """One-line collapsed summary of pipeline + backend + dataset + active nodes."""
    ps = session.pipeline_schema
    pipe = f"{ps.name} v{ps.version}" if ps else "pipeline unavailable"
    active = list((pipeline_params or {}).get("steps") or [])
    nodes = f"{len(active)} node{'s' if len(active) != 1 else ''}"
    if active:
        nodes += f" ({', '.join(active)})"
    ds = f"{dataset_name or '?'} ({dataset_len} queries)"
    logger.info("%s · %s · backend %s · dataset %s", pipe, nodes, backend_url, ds)


async def init_services_cli(
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    dataset_name: str | None = None,
    take_over: bool = False,
    tenant_id: str = "default",
) -> Session:
    """Initialize services for a CLI command (logging style + service init)."""
    from promptpotter.application.bootstrap import init_services
    from promptpotter.config.logging import setup_logging

    setup_logging(style="full" if _VERBOSE else "cli")
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return await init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        on_status=lambda msg: logger.info(msg) if _VERBOSE else None,
        take_over=take_over,
        tenant_id=tenant_id,
    )


def _prepare_cycle(session: Session, campaign_config: CampaignConfig, dataset: list):
    """Apply pipeline → load baseline → compute cycle_id. Returns (pipeline_params, baseline, cycle_id)."""
    from promptpotter.application.baseline import load_baseline_prompt
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.runner import build_baseline_cycle_id

    schema = session.pipeline_schema
    pipeline_params = configure_and_apply_pipeline(
        session, campaign_config, log=logger.info if _VERBOSE else (lambda *_a, **_k: None)
    )
    baseline = load_baseline_prompt(
        session.experiment_extract,
        prompt_node_names=schema.prompt_node_names() if schema else [],
        dataset_name=session.dataset_name,
    )
    return pipeline_params, baseline, build_baseline_cycle_id(baseline, schema, dataset)


def _mint_session_and_cycle(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    cycle_id: str,
    init_params: dict,
    pipeline_params: dict,
    baseline,
    dataset_count: int,
) -> str:
    """Mint session+cycle with the CLI's pipeline-snapshot extras."""
    from promptpotter.application.bootstrap import auto_mint_session

    session_id, _ = auto_mint_session(
        session,
        campaign_config,
        cycle_id=cycle_id,
        baseline_prompt_fields=baseline.prompt_field_dict(),
        dataset_size=dataset_count,
        experiment_id=init_params.get("experiment_id"),
        pipeline_params=pipeline_params,
        active_steps=list(pipeline_params.get("steps", [])),
    )
    return session_id


async def _maybe_decompose_task(
    session: Session,
    campaign_config: CampaignConfig,
    session_id: str,
    *,
    dataset_name: str,
    task_file: str | None,
    task_text: str | None,
) -> None:
    """Decompose task description once at session-creation time.

    ``datasets/{name}/task_description.md`` is the canonical source;
    ``--task-file`` and ``--task-text`` override for ad-hoc cases. Result
    is disk-cached, so re-init against the same dataset is free.
    """
    from promptpotter.application.config import create_llm_client
    from promptpotter.application.optimization.decomposition import decompose_task_context

    if task_file:
        task_description = Path(task_file).read_text(encoding="utf-8")
    elif task_text:
        task_description = task_text
    else:
        task_description = read_text_optional(
            Path("datasets") / dataset_name / "task_description.md"
        )

    if not task_description:
        return

    llm_client, model = create_llm_client(campaign_config)
    task_context, _consultation, was_cached = await decompose_task_context(
        task_description,
        llm_client,
        model,
        store_base_dir=session.store.base_dir if session.store else None,
        backend_id=session.backend_id,
    )
    logger.info(
        "Task context decomposed%s: %d fields",
        " (cached)" if was_cached else "",
        len(task_context),
    )
    state = session.store.sessions.read(session_id) or {}
    state["task_context"] = task_context.to_dict()
    session.store.sessions.update(session_id, state)


async def cmd_init(args: argparse.Namespace) -> CommandResult:
    """Initialize services, load datasets, configure pipeline, create session.

    No scoring calls — the baseline runs as phase 0 of ``optimize`` on the
    ``sp_budget_ttest`` slice. The one LLM cost paid here is the
    ``restructure`` template call (via ``_maybe_decompose_task``) the first
    time a dataset's ``task_description.md`` (or ``--task-file`` /
    ``--task-text``) is seen; the result is content-hash cached at
    ``restructure_cache.json`` so subsequent runs are free. ``optimize``
    reads the decomposition off the session.
    """
    from promptpotter.application.baseline import prepare_datasets
    from promptpotter.application.config import load_campaign_config as _load_cfg

    file_config = load_campaign_config(args.config)
    dataset_name = args.dataset_name or file_config.get("dataset_name")
    if not dataset_name:
        from promptpotter.presentation.cli.session import no_dataset_hint

        raise SystemExit(
            "ERROR: init requires a dataset name. Silent defaults to the "
            "TermNorm production experiment are no longer allowed.\n\n" + no_dataset_hint()
        )

    # Auto-load the dataset's campaign.json when --config wasn't given. Without this,
    # the session persists with scoring=null and default optimization knobs — the
    # dataset's own file is the intended source of truth.
    if not args.config:
        default_config_path = Path("datasets") / dataset_name / "campaign.json"
        if default_config_path.exists():
            file_config = load_campaign_config(str(default_config_path))

    session = await init_services_cli(
        backend_url=args.backend_url,
        backend_id=args.backend_id,
        experiment_id=args.experiment_id,
        dataset_name=dataset_name,
        take_over=True,
        tenant_id=getattr(args, "tenant", "default"),
    )
    backend_id = session.backend_id

    profile = session.store.backends.load_connector_profile(backend_id) or {}
    campaign_config = _load_cfg({**profile, **file_config})

    excluded = list(campaign_config.exclude_nodes)

    if args.excel_path:
        train_data = prepare_datasets(session.store, args.excel_path).train_data or []
    else:
        train_data = session.samples or []

    pipeline_params, baseline, cycle_id = _prepare_cycle(session, campaign_config, train_data)
    active = list(pipeline_params.get("steps", [])) if pipeline_params else []
    init_params = {
        "backend_url": args.backend_url,
        "backend_id": backend_id,
        "experiment_id": args.experiment_id,
        "dataset_name": dataset_name,
    }
    session_id = _mint_session_and_cycle(
        session,
        campaign_config,
        cycle_id=cycle_id,
        init_params=init_params,
        pipeline_params=pipeline_params,
        baseline=baseline,
        dataset_count=len(train_data),
    )

    await _maybe_decompose_task(
        session,
        campaign_config,
        session_id,
        dataset_name=dataset_name,
        task_file=args.task_file,
        task_text=args.task_text,
    )

    return CommandResult(
        data={
            "session_id": session_id,
            "cycle_id": cycle_id,
            "backend_id": backend_id,
            "phase": "init",
            "dataset_count": len(train_data),
            "active_steps": active,
            "excluded_nodes": excluded,
        },
        human=(
            f"\nSession created: {session_id}\n"
            f"Cycle: {cycle_id}\n"
            f"Dataset: {len(train_data)} queries (baseline runs in optimize phase 0)"
        ),
    )


def _build_divergence_hint() -> str:
    """Derive the divergence-checked-kinds list from the DECISION_GATING table.

    The hint used to hardcode the gated kinds, which silently rotted
    every time a new ``DecisionKind`` member landed. Now it walks the
    enum so adding a kind (with its gating choice) updates the operator
    message automatically.
    """
    from promptpotter.domain.run_records import DECISION_GATING, GatingMode

    replayed = sorted(k.value for k, m in DECISION_GATING.items() if m is GatingMode.REPLAYED)
    archival = sorted(k.value for k, m in DECISION_GATING.items() if m is GatingMode.ARCHIVAL)
    return (
        f"Checked decisions: {', '.join(replayed)}.\n"
        f"(Archival, not divergence-gated: {', '.join(archival)}.)\n\n"
        "Rerun with `--fork-on-divergence` to branch a sibling cycle "
        "here under the current scorer, revert "
        "`campaign.json::scoring` to continue the original trajectory, "
        "or pass `--no-divergence-check` to accept the divergence."
    )


_DIVERGENCE_HINT = _build_divergence_hint()


def _prepare_cycle_for_optimize(
    args: argparse.Namespace,
    ctx: SessionCtx,
    session: Session,
    campaign_config: CampaignConfig,
    train_data: list,
) -> tuple[dict, OptSearchPoint]:
    """Resolve cycle context: drift-detect + auto-mint + ``--from`` validation.

    Pipeline divergence-detect: if pipeline.json (model, temperature, …),
    baseline prompt, or dataset changed since the active session was
    init'd, the recomputed cycle hash no longer matches the pointer's
    cycle_id. Auto-mint a fresh session+cycle so a model swap starts a
    new campaign root instead of silently mixing measurements from the
    old model. Mutates ``ctx`` (cycle_id / session_id / state) in place.
    Returns ``(pipeline_params, baseline)`` for the emitter.
    """
    resume_from_round: int | None = getattr(args, "resume_from_round", None)
    pipeline_params, baseline_now, expected_cycle_id = _prepare_cycle(
        session, campaign_config, train_data
    )

    minted_fresh = False
    if ctx.cycle_id and resume_from_round is None and expected_cycle_id != ctx.cycle_id:
        old_cycle_id = ctx.cycle_id
        ctx.session_id = _mint_session_and_cycle(
            session,
            campaign_config,
            cycle_id=expected_cycle_id,
            init_params=ctx.init_params,
            pipeline_params=pipeline_params,
            baseline=baseline_now,
            dataset_count=len(train_data),
        )
        ctx.cycle_id = expected_cycle_id
        ctx.state = session.store.sessions.read(ctx.session_id) or ctx.state
        minted_fresh = True
        logger.info(
            "Pipeline changed since init (was %s, now %s) — minted new cycle",
            old_cycle_id,
            expected_cycle_id,
        )

    if resume_from_round is not None:
        if not ctx.cycle_id:
            raise ValueError(
                "--from requires an active cycle on this session; run `optimize` first"
            )
        if resume_from_round < 0:
            raise ValueError(f"--from must be >= 0, got {resume_from_round}")
        logger.info("Resuming cycle %s from after round %d", ctx.cycle_id, resume_from_round)
    elif ctx.cycle_id and not minted_fresh:
        logger.info("Resuming cycle %s", ctx.cycle_id)

    return pipeline_params, baseline_now


def _build_live_display(
    args: argparse.Namespace,
    *,
    session: Session,
    campaign_config: CampaignConfig,
    baseline_acc: float,
) -> LiveDisplay:
    """Build the CLI's LiveDisplay — verbose ($-v$) gets full notebook parity, else concise."""
    from promptpotter.application.scoring.formula import split_scoring_block
    from promptpotter.presentation.views.display import set_display_tags
    from promptpotter.presentation.views.live import LiveDisplay as _LiveDisplay

    set_display_tags(session.pipeline_schema)
    scoring_formula = split_scoring_block(campaign_config.scoring).per_sample
    opt = campaign_config.optimization

    if getattr(args, "verbose", False):
        return _LiveDisplay(
            campaign_rounds=[],
            baseline_acc=baseline_acc,
            l1_patience=opt.l1_patience,
            pipeline_schema=session.pipeline_schema,
            scoring_formula=scoring_formula,
        )
    return _LiveDisplay(
        baseline_acc=baseline_acc,
        l1_patience=opt.l1_patience,
        sp_budget_ttest=campaign_config.sp_budget_ttest,
        scoring_formula=scoring_formula,
        pipeline_schema=session.pipeline_schema,
    )


def _build_observers(
    args: argparse.Namespace,
    session: Session,
    campaign_config: CampaignConfig,
    train_data: list,
    baseline_acc: float,
) -> RunObservers:
    """CLI thin shim around ``build_run_observers`` — passes ``args``-derived display."""
    from promptpotter.application.run_observers import build_run_observers

    display = _build_live_display(
        args, session=session, campaign_config=campaign_config, baseline_acc=baseline_acc
    )
    return build_run_observers(
        session=session,
        campaign_config=campaign_config,
        dataset=train_data,
        display=display,
        resumed_from_round=getattr(args, "resume_from_round", None),
        baseline_accuracy=baseline_acc,
    )


async def _run_sweep_batch(
    args: argparse.Namespace,
    root_ctx: SessionCtx,
    campaign_config: CampaignConfig,
    train_data: list,
    sweep_payloads: list[tuple[Path, Any]],
) -> CommandResult:
    """Thin shim — defers to ``application.sweep.run_sweep_batch``.

    Builds an observer factory bound to this CLI's ``args`` +
    ``campaign_config`` so the orchestrator stays free of presentation
    imports.
    """
    from promptpotter.application.sweep import run_sweep_batch

    def observer_factory(session: Session, baseline_acc: float) -> RunObservers:
        return _build_observers(args, session, campaign_config, train_data, baseline_acc)

    result = await run_sweep_batch(
        args,
        root_ctx,
        campaign_config,
        train_data,
        sweep_payloads,
        observer_factory=observer_factory,
        verbose=_VERBOSE,
    )
    return CommandResult(
        data=result.model_dump(),
        human=(
            f"Sweep batch {result.batch_id}: {len(result.fork_cycle_ids)} forks under "
            f"{result.parent_cycle_id}\n" + "\n".join(f"  - {c}" for c in result.fork_cycle_ids)
        ),
    )


def _maybe_fork_diag_sibling(args: argparse.Namespace, ctx, session) -> None:
    """Diag-BFS: re-running ``--diag`` against a finalized diag cycle branches
    off a counted sibling (``{root}_diag_NNN``) instead of overwriting the
    parent's archive. Each probe is its own cycle with ``parent_cycle_id`` set;
    baseline measurements stay shared via the JSP-keyed archive."""
    if not (
        getattr(args, "diag", False)
        and ctx.cycle_id
        and getattr(args, "resume_from_round", None) is None
    ):
        return
    existing_index = session.store.campaigns.load(session.backend_id, ctx.cycle_id) or {}
    if (existing_index.get("final") or {}).get("mode") != "diag":
        return
    from promptpotter.application.optimization.cycle import fork_for_diag_sibling

    new_cycle_id = fork_for_diag_sibling(
        session.store.campaigns,
        session.tenant.tenant_id if session.tenant else "default",
        ctx.session_id,
        ctx.cycle_id,
    )
    ctx.cycle_id = new_cycle_id
    session.state.cycle_id = new_cycle_id


async def _maybe_dispatch_sweep_batch(
    args: argparse.Namespace, ctx, campaign_config, train_data
) -> CommandResult | None:
    """Multi-fork sweep dispatch: with --sweep AND a non-empty
    ``datasets/{name}/sweep/*.json`` directory, mint one fork per candidate
    ``SweepPayload`` and run sweep mode on each. Returns None to fall through
    to the normal path (no --sweep flag, no sweep dir, or empty payloads)."""
    if not getattr(args, "sweep", False):
        return None
    from promptpotter.application.sweep import (
        load_sweep_payloads,
        resolve_sweep_dir,
    )

    sweep_dir = resolve_sweep_dir(ctx.init_params.get("dataset_name"))
    if sweep_dir is None:
        return None
    sweep_payloads = load_sweep_payloads(sweep_dir)
    if not sweep_payloads:
        return None
    ctx.save_phase("optimizing")
    return await _run_sweep_batch(args, ctx, campaign_config, train_data, sweep_payloads)


async def _run_normal_optimize(
    args: argparse.Namespace, ctx, campaign_config, session, train_data
) -> CommandResult:
    """Build observers, drive the optimization loop, handle divergence."""
    from promptpotter.application.runner import (
        run_optimization as _orch_run_optimization,
    )
    from promptpotter.shared.errors import ResumeDivergenceError

    pre_baseline_acc = ctx.state.get("baseline_accuracy", 0.0)
    observers = _build_observers(args, session, campaign_config, train_data, pre_baseline_acc)
    ctx.save_phase("optimizing")

    try:
        cycle_result = await _orch_run_optimization(
            train_data,
            campaign_config,
            session=session,
            observers=observers,
            experiment_id=ctx.state["experiment_id"],
            task_context=ctx.task_context,
            resume_from_round_override=getattr(args, "resume_from_round", None),
            no_divergence_check=getattr(args, "no_divergence_check", False),
            fork_on_divergence=getattr(args, "fork_on_divergence", False),
            sweep=getattr(args, "sweep", False),
            diag=getattr(args, "diag", False),
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
            human=f"{div}\n\n{_DIVERGENCE_HINT}",
        )

    ctx.state["best_accuracy"] = cycle_result.best_accuracy
    ctx.save_phase("optimize")
    campaign_dir = session.store.campaigns.campaign_dir(ctx.cycle_id)
    return CommandResult(
        data=cycle_result.model_dump(),
        human=(
            f"Campaign: {cycle_result.cycle_id}\n"
            f"Dashboard: {campaign_dir / 'dashboard.json'}\n"
            f"Digest: {campaign_dir / 'log.md'}"
        ),
    )


async def cmd_optimize(args: argparse.Namespace) -> CommandResult:
    """Run optimization loop. Live state is ``campaigns/{cycle_id}/dashboard.json``;
    digest is ``log.md``; final summary is ``index.json::final``. Stop with Ctrl+C.
    """
    # Keep the spend chip honest against provider re-pricing. The cache
    # has a 24 h TTL so this is a no-op on most starts; --refresh-rates
    # forces a fetch. Network failure logs + falls back to the prior cache
    # or the in-repo bundled floor — offline starts still resolve rates.
    from promptpotter.shared.spend import refresh_rates

    refresh_rates(force=bool(getattr(args, "refresh_rates", False)))

    ctx = load_session(args)
    campaign_config = ctx.campaign_config
    session = await init_services_cli(**ctx.init_params)

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return CommandResult(
            data={"error": "backend_unreachable", "backend_url": ctx.backend_url},
            human=f"Backend unreachable at {ctx.backend_url}. Start the backend and retry.",
        )

    train_data = session.samples or []
    pipeline_params, _baseline = _prepare_cycle_for_optimize(
        args, ctx, session, campaign_config, train_data
    )
    # ctx may have been re-minted; bind the now-resolved ids onto session.
    session.session_id = ctx.session_id
    session.state.cycle_id = ctx.cycle_id

    _maybe_fork_diag_sibling(args, ctx, session)

    log_startup_summary(
        session,
        pipeline_params,
        len(train_data),
        ctx.backend_url,
        ctx.init_params["dataset_name"],
    )
    logger.info("Session: %s", session.store.sessions.session_dir(ctx.session_id))
    logger.info("Campaign: %s", session.store.campaigns.campaign_dir(ctx.cycle_id))

    if (
        sweep_result := await _maybe_dispatch_sweep_batch(args, ctx, campaign_config, train_data)
    ) is not None:
        return sweep_result

    return await _run_normal_optimize(args, ctx, campaign_config, session, train_data)


async def cmd_compare(args: argparse.Namespace) -> CommandResult:
    """PoBB-compare cycle winners across the family with adaptive top-up."""
    from promptpotter.application.bootstrap import populate_session_scoring
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.optimization.elevation import (
        discover_compare_arms,
        elevate_to_decisive,
    )
    from promptpotter.application.scoring.formula import split_scoring_block

    ctx = load_session(args)
    session = await init_services_cli(**ctx.init_params)
    session.session_id = ctx.session_id
    session.state.cycle_id = ctx.cycle_id

    campaign_config = ctx.campaign_config
    configure_and_apply_pipeline(
        session, campaign_config, log=logger.info if _VERBOSE else (lambda *_a, **_k: None)
    )

    scoring_spec = split_scoring_block(campaign_config.scoring)
    populate_session_scoring(
        session,
        obs=None,
        scoring_formula=scoring_spec.per_sample,
        scoring_round_formula=scoring_spec.per_round,
        scorer_id=scoring_spec.scorer_id,
        experiment_id=ctx.state.get("experiment_id", ""),
        cycle_id=ctx.cycle_id,
        max_consecutive_errors=campaign_config.optimization.max_consecutive_errors,
    )

    armset = discover_compare_arms(
        session, ctx.cycle_id, list(args.cycle_ids), all_family=args.all_family
    )
    if armset.error is not None:
        return CommandResult(human=f"ERROR: {armset.error}")

    discover_family = args.all_family or not args.cycle_ids
    result = await elevate_to_decisive(
        armset.arms,
        session,
        session.samples or [],
        epsilon=args.epsilon,
        max_topups=args.max_topups,
        n_min_per_arm=args.n_min_per_arm,
        stream=discover_family or args.max_topups < 0,
    )

    return CommandResult(
        data={
            "decision": result.decision,
            "best_arm": result.best_arm,
            "p_best": result.p_best,
            "topups_per_arm": result.topups_per_arm,
            "score_histories_n": {k: len(v) for k, v in result.score_histories.items()},
            "score_means": {
                k: (sum(v) / len(v) if v else 0.0) for k, v in result.score_histories.items()
            },
        },
        human=result.note,
    )


COMMANDS = {
    "init": cmd_init,
    "optimize": cmd_optimize,
    "compare": cmd_compare,
}


def main() -> None:
    from promptpotter.shared.errors import RequestTooLargeError

    args = build_parser().parse_args()
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
