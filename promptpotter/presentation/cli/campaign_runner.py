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
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.optimization.observers import RunObservers
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
    """Apply pipeline → load origin → compute cycle_id. Returns (pipeline_params, origin, cycle_id)."""
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.origin import load_origin_prompt
    from promptpotter.application.runner import build_origin_cycle_id

    schema = session.pipeline_schema
    pipeline_params = configure_and_apply_pipeline(
        session, campaign_config, log=logger.info if _VERBOSE else (lambda *_a, **_k: None)
    )
    origin = load_origin_prompt(
        session.experiment_extract,
        prompt_node_names=schema.prompt_node_names() if schema else [],
        dataset_name=session.dataset_name,
    )
    return pipeline_params, origin, build_origin_cycle_id(origin, schema, dataset)


def _mint_session_and_cycle(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    cycle_id: str,
    init_params: dict,
    pipeline_params: dict,
    origin,
    dataset_count: int,
) -> str:
    """Mint session+cycle with the CLI's pipeline-snapshot extras."""
    from promptpotter.application.bootstrap.session import auto_mint_session

    session_id, _ = auto_mint_session(
        session,
        campaign_config,
        cycle_id=cycle_id,
        origin_prompt_fields=origin.prompt_field_dict(),
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

    No scoring calls — the origin runs as phase 0 of ``optimize`` on the
    ``sp_budget_ttest`` slice. The one LLM cost paid here is the
    ``restructure`` template call (via ``_maybe_decompose_task``) the first
    time a dataset's ``task_description.md`` (or ``--task-file`` /
    ``--task-text``) is seen; the result is content-hash cached at
    ``restructure_cache.json`` so subsequent runs are free. ``optimize``
    reads the decomposition off the session.
    """
    from promptpotter.application.config import load_campaign_config as _load_cfg
    from promptpotter.application.origin import prepare_datasets

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

    pipeline_params, origin, cycle_id = _prepare_cycle(session, campaign_config, train_data)
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
        origin=origin,
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
            f"Dataset: {len(train_data)} queries (origin runs in optimize phase 0)"
        ),
    )


def _build_divergence_hint() -> str:
    """Derive the divergence-checked-kinds list from the RESUME_CHECKPOINT_GATING table.

    The hint used to hardcode the gated kinds, which silently rotted
    every time a new ``ResumeCheckpointKind`` member landed. Now it walks the
    enum so adding a kind (with its gating choice) updates the operator
    message automatically.
    """
    from promptpotter.application.optimization.resume_and_fork import (
        RESUME_CHECKPOINT_GATING,
        GatingMode,
    )

    replayed = sorted(
        k.value for k, m in RESUME_CHECKPOINT_GATING.items() if m is GatingMode.REPLAYED
    )
    archival = sorted(
        k.value for k, m in RESUME_CHECKPOINT_GATING.items() if m is GatingMode.ARCHIVAL
    )
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
    origin prompt, or dataset changed since the active session was
    init'd, the recomputed cycle hash no longer matches the pointer's
    cycle_id. Auto-mint a fresh session+cycle so a model swap starts a
    new campaign root instead of silently mixing measurements from the
    old model. Mutates ``ctx`` (cycle_id / session_id / state) in place.
    Returns ``(pipeline_params, origin)`` for the emitter.
    """
    resume_from_round: int | None = getattr(args, "resume_from_round", None)
    pipeline_params, origin_now, expected_cycle_id = _prepare_cycle(
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
            origin=origin_now,
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

    return pipeline_params, origin_now


def _build_live_display(
    args: argparse.Namespace,
    *,
    session: Session,
    campaign_config: CampaignConfig,
    origin_acc: float,
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
            origin_acc=origin_acc,
            l1_patience=opt.l1_patience,
            pipeline_schema=session.pipeline_schema,
            scoring_formula=scoring_formula,
        )
    return _LiveDisplay(
        origin_acc=origin_acc,
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
    origin_acc: float,
) -> RunObservers:
    """CLI thin shim around ``build_run_observers`` — passes ``args``-derived display."""
    from promptpotter.application.optimization.observers import build_run_observers

    display = _build_live_display(
        args, session=session, campaign_config=campaign_config, origin_acc=origin_acc
    )
    return build_run_observers(
        session=session,
        campaign_config=campaign_config,
        dataset=train_data,
        display=display,
        resumed_from_round=getattr(args, "resume_from_round", None),
        origin_accuracy=origin_acc,
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

    def observer_factory(session: Session, origin_acc: float) -> RunObservers:
        return _build_observers(args, session, campaign_config, train_data, origin_acc)

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
    origin measurements stay shared via the JSP-keyed archive."""
    if not (
        getattr(args, "diag", False)
        and ctx.cycle_id
        and getattr(args, "resume_from_round", None) is None
    ):
        return
    existing_index = session.store.campaigns.load(session.backend_id, ctx.cycle_id) or {}
    if (existing_index.get("final") or {}).get("mode") != "diag":
        return
    from promptpotter.application.optimization.resume_and_fork import _mint_fork
    from promptpotter.domain.run_records import ForkPayload, ForkTrigger

    tenant_id = session.tenant.tenant_id if session.tenant else "default"
    new_cycle_id = _mint_fork(
        session.store.campaigns,
        tenant_id,
        ctx.session_id,
        ctx.cycle_id,
        0,
        ForkPayload(
            trigger=ForkTrigger.OPERATOR_DIAG,
            reason="diag-sibling BFS exploration",
            issued_by=tenant_id,
        ),
    )
    ctx.cycle_id = new_cycle_id
    session.state.cycle_id = new_cycle_id


async def _maybe_dispatch_sweep_batch(
    args: argparse.Namespace, ctx, campaign_config, train_data
) -> CommandResult | None:
    """Multi-fork sweep dispatch: with --sweep AND a non-empty
    ``datasets/{name}/sweep/*.json`` directory, mint one fork per
    :class:`OperatorSweepFile` (widened to a ``ForkPayload`` by
    :mod:`promptpotter.application.sweep.sweep_runner`) and run sweep mode
    on each. Returns None to fall through to the normal path (no --sweep
    flag, no sweep dir, or empty payloads)."""
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

    pre_origin_acc = ctx.state.get("origin_accuracy", 0.0)
    observers = _build_observers(args, session, campaign_config, train_data, pre_origin_acc)
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
    pipeline_params, _origin = _prepare_cycle_for_optimize(
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


def _sweep_early_exit_reason(stop_reason: str) -> str:
    """Map ``StopReason`` → sweep-result ``early_exit_reason`` enum.

    ``target_hit | max_rounds | max_spend`` is the documented surface;
    anything else (patience, interrupt, hard-cap) lands as the raw
    stop_reason string so the operator sees why the sweep didn't bottom
    out on one of the three intentional exits.
    """
    from promptpotter.domain.phases import StopReason

    mapping = {
        StopReason.TARGET_HIT.value: "target_hit",
        StopReason.MAX_ROUNDS.value: "max_rounds",
        StopReason.MAX_SPEND.value: "max_spend",
    }
    return mapping.get(stop_reason, stop_reason)


@dataclass
class _Variant:
    """One L1-meta-prompt variant in a panel iteration."""

    path: Path | None  # None ⇒ use whatever is currently loaded
    label: str | None  # operator-supplied or file stem


def _parse_variants(args: argparse.Namespace) -> list[_Variant]:
    """Resolve ``--l1-prompts`` (plural, round1/round2) or ``--l1-prompt``
    (singular, time-to). When neither is given returns a single
    ``_Variant(path=None)`` so the verb runs against the currently
    loaded L1 in one iteration."""
    label_override = getattr(args, "l1_prompt_label", None)
    raw = getattr(args, "l1_prompts", None) or getattr(args, "l1_prompt", None)
    if not raw:
        return [_Variant(path=None, label=label_override)]
    paths = [Path(p.strip()) for p in str(raw).split(",") if p.strip()]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise SystemExit(f"L1 prompt file(s) not found: {', '.join(missing)}")
    return [
        _Variant(path=p, label=label_override if len(paths) == 1 else None or p.stem) for p in paths
    ]


def _resolve_l1_template(variant: _Variant) -> Any:
    """Load PromptTemplate from variant's path (or None for current loader)."""
    if variant.path is None:
        return None
    from promptpotter.application.sweep import load_l1_prompt_from_path

    return load_l1_prompt_from_path(variant.path)


async def _setup_sweep_cycle(
    args: argparse.Namespace,
    campaign_config: CampaignConfig,
) -> Any:
    """Init services + resolve cycle for a sweep verb. Returns the bundle
    (ctx, session, train_data, pipeline_params) shared by every verb."""
    ctx = load_session(args)
    session = await init_services_cli(**ctx.init_params)

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return None, ctx, session

    train_data = session.samples or []
    pipeline_params, _origin = _prepare_cycle_for_optimize(
        args, ctx, session, campaign_config, train_data
    )
    session.session_id = ctx.session_id
    session.state.cycle_id = ctx.cycle_id

    log_startup_summary(
        session,
        pipeline_params,
        len(train_data),
        ctx.backend_url,
        ctx.init_params["dataset_name"],
    )
    return train_data, ctx, session


async def _run_sweep_optimize(
    args: argparse.Namespace,
    ctx,
    campaign_config: CampaignConfig,
    session: Session,
    train_data: list,
    *,
    halt_at_accuracy: float | None = None,
    max_spend_usd: float | None = None,
) -> tuple[Any, Any]:
    """Drive ``run_optimization`` for any sweep verb — honors
    ``halt_at_accuracy`` / ``max_spend_usd`` and the active L1 override
    (managed by the caller via :func:`l1_generate_override`). Returns
    ``(cycle_result, observers)``."""
    from promptpotter.application.runner import (
        run_optimization as _orch_run_optimization,
    )

    pre_origin_acc = ctx.state.get("origin_accuracy", 0.0)
    observers = _build_observers(args, session, campaign_config, train_data, pre_origin_acc)
    ctx.save_phase("optimizing")

    cycle_result = await _orch_run_optimization(
        train_data,
        campaign_config,
        session=session,
        observers=observers,
        experiment_id=ctx.state["experiment_id"],
        task_context=ctx.task_context,
        resume_from_round_override=getattr(args, "resume_from_round", None),
        no_divergence_check=False,
        fork_on_divergence=False,
        sweep=False,
        diag=False,
        halt_at_accuracy=halt_at_accuracy,
        max_spend_usd=max_spend_usd,
    )
    ctx.state["best_accuracy"] = cycle_result.best_accuracy
    ctx.save_phase("optimize")
    return cycle_result, observers


def _panel_stats_from_round(round_result: Any, panel_size: int) -> dict[str, float]:
    """Pull per-candidate accuracy + pipeline_params from a ``RoundResult``
    and route through :func:`compute_panel_stats`."""
    from promptpotter.application.sweep import compute_panel_stats

    scores = list(round_result.candidate_scores or [])
    accuracies = [float(s.get("accuracy") or 0.0) for s in scores]
    pps = [s.get("pipeline_params_override") or {} for s in scores]
    return compute_panel_stats(
        candidates_planned=panel_size,
        candidate_accuracies=accuracies,
        candidate_pipeline_params=pps,
    )


async def _cmd_sweep_time_to(args: argparse.Namespace) -> CommandResult:
    """`sweep time-to N` — run optimize, halt on target / max-rounds /
    max-spend, write one result JSON.

    The cycle runs against whatever session+cycle the active pointer
    names — operator runs ``init`` (or implicit auto-mint) first.
    ``--max-rounds`` overrides ``campaign.json::optimization.max_rounds``
    for this sweep only; the override stays in-memory.
    """
    from promptpotter.application.sweep import (
        build_sweep_result,
        current_l1_meta_prompt_hash,
        l1_generate_override,
        slice_samples,
        write_sweep_result,
    )
    from promptpotter.shared.errors import ResumeDivergenceError
    from promptpotter.shared.spend import refresh_rates

    refresh_rates(force=bool(getattr(args, "refresh_rates", False)))

    campaign_config = load_session(args).campaign_config
    target_acc = args.target / 100.0
    max_rounds = int(args.max_rounds)
    max_spend = float(args.max_spend) if args.max_spend is not None else None
    sweep_opt = campaign_config.optimization.model_copy(update={"max_rounds": max_rounds})
    campaign_config = campaign_config.model_copy(update={"optimization": sweep_opt})

    train_data, ctx, session = await _setup_sweep_cycle(args, campaign_config)
    if train_data is None:
        return CommandResult(
            data={"error": "backend_unreachable", "backend_url": ctx.backend_url},
            human=f"Backend unreachable at {ctx.backend_url}. Start the backend and retry.",
        )

    train_data, resolved_slice = slice_samples(
        train_data,
        args.slice_spec,
        stores=session.store,
        backend_id=session.backend_id,
    )
    variants = _parse_variants(args)
    if len(variants) > 1:
        raise SystemExit("sweep time-to: only one L1 variant per invocation")
    variant = variants[0]
    l1_template = _resolve_l1_template(variant)

    logger.info(
        "Sweep time-to %d%%: max_rounds=%d max_spend=%s slice=%s l1=%s",
        args.target,
        max_rounds,
        f"${max_spend:.2f}" if max_spend is not None else "uncapped",
        resolved_slice,
        variant.label or "current",
    )

    try:
        with l1_generate_override(l1_template):
            cycle_result, observers = await _run_sweep_optimize(
                args,
                ctx,
                campaign_config,
                session,
                train_data,
                halt_at_accuracy=target_acc,
                max_spend_usd=max_spend,
            )
    except ResumeDivergenceError as div:
        return CommandResult(
            data={"error": "resume_divergence", "round": div.round_num},
            human=f"{div}\n\n{_DIVERGENCE_HINT}",
        )

    spend_state = observers.dashboard.state.get("spend") or {}
    spend_usd = float(spend_state.get("total_used_usd") or 0.0)
    early_exit = _sweep_early_exit_reason(cycle_result.stop_reason)
    target_hit = early_exit == "target_hit"

    result = build_sweep_result(
        verb="time-to",
        dataset=ctx.init_params["dataset_name"] or "unknown",
        l1_meta_prompt_hash=current_l1_meta_prompt_hash(),
        l1_meta_prompt_label=variant.label,
        l1_prompt_path=str(variant.path) if variant.path else None,
        cycle_id=cycle_result.cycle_id,
        slice_name=resolved_slice,
        rounds_to_target=cycle_result.n_rounds if target_hit else None,
        early_exit_reason=early_exit,
        cost_usd=spend_usd,
        final_accuracy=cycle_result.best_accuracy,
    )
    out_path = write_sweep_result(session.store.base_dir, result, target=args.target)

    return CommandResult(
        data={**result, "result_path": str(out_path), "cycle_id": cycle_result.cycle_id},
        human=(
            f"Sweep time-to {args.target}%: {early_exit} "
            f"(rounds={cycle_result.n_rounds}, best={cycle_result.best_accuracy:.3f}, "
            f"spend=${spend_usd:.4f})\n"
            f"Result: {out_path}"
        ),
    )


def _mint_toolkit_batch_id() -> str:
    """Cycle-id-safe batch id for OPERATOR_SWEEP forks: ``b{ts}{hex}``,
    no underscores (the cycle-id regex parses on ``_sweep_{batch_id}_``).
    """
    import secrets
    from datetime import UTC
    from datetime import datetime as _dt

    return "b" + _dt.now(UTC).strftime("%Y%m%dT%H%M%SZ") + secrets.token_hex(2)


async def _fork_and_bind(
    args: argparse.Namespace,
    parent_ctx,
    sweep_id: str,
    batch_id: str,
    variant: _Variant,
    campaign_config: CampaignConfig,
) -> tuple[Any, Session, str]:
    """Mint an OPERATOR_SWEEP fork off ``parent_ctx.cycle_id``, init a
    fresh session bound to it, return ``(fork_ctx, fork_session, fork_cycle_id)``.

    Mirrors the per-fork setup in ``application/sweep.sweep_runner`` —
    the active pointer is retargeted by ``_mint_fork`` and the caller
    must restore it after the loop.
    """
    from promptpotter.application.bootstrap import init_services
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.optimization.resume_and_fork import _mint_fork
    from promptpotter.domain.run_records import ForkPayload, ForkTrigger
    from promptpotter.infrastructure.store import build_stores

    tenant_id = getattr(args, "tenant", "default")
    store = build_stores(tenant_id=tenant_id)
    source_file = variant.path.name if variant.path else f"current-{variant.label or 'unset'}.json"
    fork_payload = ForkPayload(
        trigger=ForkTrigger.OPERATOR_SWEEP,
        reason=f"sweep-toolkit:{sweep_id}",
        issued_by=tenant_id,
    )
    new_cycle_id = _mint_fork(
        store.campaigns,
        tenant_id,
        parent_ctx.session_id,
        parent_ctx.cycle_id,
        0,
        fork_payload,
        sweep_batch_id=batch_id,
        sweep_source_file=source_file,
    )
    fork_ctx = load_session(args)
    fork_session = await init_services(
        **fork_ctx.init_params,
        tenant_id=tenant_id,
        on_status=lambda msg: logger.info(msg) if _VERBOSE else None,
    )
    fork_session.session_id = fork_ctx.session_id
    fork_session.state.cycle_id = fork_ctx.cycle_id
    configure_and_apply_pipeline(
        fork_session,
        campaign_config,
        log=logger.info if _VERBOSE else (lambda *_a, **_k: None),
    )
    return fork_ctx, fork_session, new_cycle_id


async def _run_one_panel_variant(
    args: argparse.Namespace,
    ctx,
    session: Session,
    train_data: list,
    variant: _Variant,
    campaign_config: CampaignConfig,
) -> tuple[Any, Any]:
    """Run one optimize cycle with ``variant``'s L1 template applied.
    Returns ``(cycle_result, observers)``."""
    from promptpotter.application.sweep import l1_generate_override

    template = _resolve_l1_template(variant)
    with l1_generate_override(template):
        return await _run_sweep_optimize(args, ctx, campaign_config, session, train_data)


def _variant_to_result(
    *,
    verb: str,
    variant: _Variant,
    cycle_result: Any,
    observers: Any,
    panel_size: int,
    sweep_id: str,
    dataset: str,
    resolved_slice: str,
    prior_round1_acc: float | None,
) -> dict[str, Any]:
    """Compose one sweep-result dict from a finished panel run."""
    from promptpotter.application.sweep import (
        build_sweep_result,
        current_l1_meta_prompt_hash,
    )

    rounds = list(cycle_result.rounds or [])
    spend_state = observers.dashboard.state.get("spend") or {}
    spend_usd = float(spend_state.get("total_used_usd") or 0.0)
    if not rounds:
        return build_sweep_result(
            verb=verb,
            dataset=dataset,
            l1_meta_prompt_hash=current_l1_meta_prompt_hash(),
            l1_meta_prompt_label=variant.label,
            l1_prompt_path=str(variant.path) if variant.path else None,
            cycle_id=cycle_result.cycle_id,
            sweep_id=sweep_id,
            slice_name=resolved_slice,
            panel_size=panel_size,
            cost_usd=spend_usd,
            final_accuracy=cycle_result.best_accuracy,
            notes=f"no rounds; stop_reason={cycle_result.stop_reason}",
        )

    r1 = _panel_stats_from_round(rounds[0], panel_size)
    fields: dict[str, Any] = {
        "panel_size": panel_size,
        "round1_accuracy": r1["round1_accuracy"],
        "round1_best": r1["round1_best"],
        "parse_fail_rate": r1["parse_fail_rate"],
        "pipeline_params_entropy": r1["pipeline_params_entropy"],
        "cost_usd": spend_usd,
        "final_accuracy": cycle_result.best_accuracy,
    }
    if verb == "round2" and len(rounds) >= 2:
        r2 = _panel_stats_from_round(rounds[1], panel_size)
        fields["round2_accuracy"] = r2["round1_accuracy"]
        anchor = prior_round1_acc if prior_round1_acc is not None else r1["round1_accuracy"]
        fields["round2_lift"] = r2["round1_accuracy"] - anchor

    return build_sweep_result(
        verb=verb,
        dataset=dataset,
        l1_meta_prompt_hash=current_l1_meta_prompt_hash(),
        l1_meta_prompt_label=variant.label,
        l1_prompt_path=str(variant.path) if variant.path else None,
        cycle_id=cycle_result.cycle_id,
        sweep_id=sweep_id,
        slice_name=resolved_slice,
        **fields,
    )


async def _run_panel_verb(
    args: argparse.Namespace,
    *,
    verb: str,
    n_rounds: int,
    variants: list[_Variant],
    variant_priors: dict[str, float] | None = None,
) -> CommandResult:
    """Drive ``round1`` / ``round2`` across one or more variants.

    Single variant + no ``--l1-prompts`` ⇒ runs on the active session
    (no fork). Multi-variant ⇒ mints one OPERATOR_SWEEP fork per
    variant, restores the active pointer at the end.

    ``variant_priors`` (keyed by ``l1_meta_prompt_label`` or path) carries
    the prior round1_accuracy for round2 lift computation when called
    via ``--from-sweep``.
    """
    from promptpotter.application.sweep import slice_samples, write_sweep_result
    from promptpotter.infrastructure.store import save_active_pointer
    from promptpotter.shared.errors import ResumeDivergenceError
    from promptpotter.shared.spend import refresh_rates

    refresh_rates(force=bool(getattr(args, "refresh_rates", False)))

    panel_size = int(args.panel_size)
    campaign_config = load_session(args).campaign_config
    sweep_opt = campaign_config.optimization.model_copy(
        update={"max_rounds": n_rounds, "n_variants": panel_size}
    )
    campaign_config = campaign_config.model_copy(update={"optimization": sweep_opt})

    train_data, ctx, session = await _setup_sweep_cycle(args, campaign_config)
    if train_data is None:
        return CommandResult(
            data={"error": "backend_unreachable", "backend_url": ctx.backend_url},
            human=f"Backend unreachable at {ctx.backend_url}. Start the backend and retry.",
        )
    train_data, resolved_slice = slice_samples(
        train_data,
        args.slice_spec,
        stores=session.store,
        backend_id=session.backend_id,
    )
    dataset = ctx.init_params["dataset_name"] or "unknown"
    sweep_id = __import__(
        "promptpotter.application.sweep", fromlist=["mint_sweep_id"]
    ).mint_sweep_id()
    multi = len(variants) > 1 or (len(variants) == 1 and variants[0].path is not None)
    batch_id = _mint_toolkit_batch_id() if multi else None
    parent_cycle_id = ctx.cycle_id

    results: list[dict[str, Any]] = []
    out_paths: list[Path] = []

    logger.info(
        "Sweep %s: %d variant(s), panel=%d slice=%s n_rounds=%d sweep_id=%s",
        verb,
        len(variants),
        panel_size,
        resolved_slice,
        n_rounds,
        sweep_id,
    )

    for idx, variant in enumerate(variants):
        if multi:
            ctx_v, sess_v, fork_cycle_id = await _fork_and_bind(
                args, ctx, sweep_id, batch_id or "", variant, campaign_config
            )
            logger.info(
                "Sweep %s [%d/%d] variant=%s → fork %s",
                verb,
                idx + 1,
                len(variants),
                variant.label or "current",
                fork_cycle_id,
            )
        else:
            ctx_v, sess_v = ctx, session
        try:
            cycle_result, observers = await _run_one_panel_variant(
                args, ctx_v, sess_v, train_data, variant, campaign_config
            )
        except ResumeDivergenceError as div:
            return CommandResult(
                data={"error": "resume_divergence", "round": div.round_num},
                human=f"{div}\n\n{_DIVERGENCE_HINT}",
            )

        prior = None
        if variant_priors:
            key = variant.label or (str(variant.path) if variant.path else "")
            prior = variant_priors.get(key)

        result = _variant_to_result(
            verb=verb,
            variant=variant,
            cycle_result=cycle_result,
            observers=observers,
            panel_size=panel_size,
            sweep_id=sweep_id,
            dataset=dataset,
            resolved_slice=resolved_slice,
            prior_round1_acc=prior,
        )
        out_path = write_sweep_result(session.store.base_dir, result)
        out_paths.append(out_path)
        results.append({**result, "result_path": str(out_path)})

    if multi:
        # _mint_fork retargeted the active pointer per iteration; put it
        # back so subsequent operator commands hit the parent cycle.
        save_active_pointer(getattr(args, "tenant", "default"), ctx.session_id, parent_cycle_id)

    human_lines = [f"Sweep {verb}: {len(results)} variant(s); sweep_id={sweep_id}"]
    for r in results:
        bits = [
            f"  {r.get('l1_meta_prompt_label') or 'current'}:",
            f"r1={r.get('round1_accuracy') or 0:.3f}",
        ]
        if r.get("round2_accuracy") is not None:
            bits.append(f"r2={r['round2_accuracy']:.3f}")
            bits.append(f"lift={r['round2_lift']:+.3f}")
        bits.append(f"${r.get('cost_usd') or 0:.4f}")
        human_lines.append(" ".join(bits))
    human_lines.extend(f"  → {p}" for p in out_paths)
    return CommandResult(
        data={"sweep_id": sweep_id, "results": results},
        human="\n".join(human_lines),
    )


async def _cmd_sweep_round1(args: argparse.Namespace) -> CommandResult:
    variants = _parse_variants(args)
    return await _run_panel_verb(args, verb="round1", n_rounds=1, variants=variants)


async def _cmd_sweep_round2(args: argparse.Namespace) -> CommandResult:
    """``--from-sweep <sweep_id>`` ⇒ load that sweep's top-K variants
    (by ``round1_accuracy``) and re-run each with 2 rounds, anchoring
    round2_lift against each variant's prior round1. Without
    ``--from-sweep``, falls back to ``--l1-prompts`` / current L1."""
    from promptpotter.application.sweep import find_sweep_results
    from promptpotter.infrastructure.store import build_stores

    if getattr(args, "from_sweep", None):
        stores = build_stores(tenant_id=getattr(args, "tenant", "default"))
        prior = find_sweep_results(stores.base_dir, sweep_id=args.from_sweep, verb="round1")
        if not prior:
            return CommandResult(
                data={"error": "from_sweep_not_found", "sweep_id": args.from_sweep},
                human=f"--from-sweep {args.from_sweep}: no round1 results found.",
            )
        prior.sort(key=lambda r: r.get("round1_accuracy") or 0.0, reverse=True)
        top_k = prior[: max(1, int(args.top_k))]
        variants: list[_Variant] = []
        priors: dict[str, float] = {}
        for row in top_k:
            path_str = row.get("l1_prompt_path")
            path = Path(path_str) if path_str and Path(path_str).exists() else None
            label = row.get("l1_meta_prompt_label") or (path.stem if path else "current")
            variants.append(_Variant(path=path, label=label))
            r1 = row.get("round1_accuracy")
            if isinstance(r1, int | float):
                priors[label] = float(r1)
                if path is not None:
                    priors[str(path)] = float(r1)
        logger.info("round2: anchoring against sweep %s top-%d", args.from_sweep, len(variants))
        return await _run_panel_verb(
            args, verb="round2", n_rounds=2, variants=variants, variant_priors=priors
        )

    variants = _parse_variants(args)
    return await _run_panel_verb(args, verb="round2", n_rounds=2, variants=variants)


async def _cmd_sweep_rank(args: argparse.Namespace) -> CommandResult:
    """Read sweep results from disk and print a sorted table. Pure
    read — no optimize call, no LLM spend."""
    from promptpotter.application.sweep import find_sweep_results, rank_sweep_results
    from promptpotter.infrastructure.store import build_stores

    stores = build_stores(tenant_id=getattr(args, "tenant", "default"))
    results = find_sweep_results(
        stores.base_dir,
        dataset=args.dataset,
        verb=args.filter_verb,
    )
    if not results:
        return CommandResult(
            data={"results": []},
            human=(
                f"No sweep results under {stores.base_dir / 'archive' / 'sweeps'}"
                + (f" for dataset={args.dataset}" if args.dataset else "")
                + (f" verb={args.filter_verb}" if args.filter_verb else "")
            ),
        )
    table = rank_sweep_results(
        results, by=args.rank_by, last=args.last, ascending=bool(args.ascending)
    )
    return CommandResult(
        data={"results": results[: args.last or len(results)], "by": args.rank_by},
        human=table,
    )


async def cmd_sweep(args: argparse.Namespace) -> CommandResult:
    """Dispatch ``sweep <verb>`` — ``time-to`` / ``round1`` / ``round2`` /
    ``rank`` per ``docs/specs/m10-sweep-toolkit.md``."""
    verb = args.sweep_verb
    if verb == "time-to":
        return await _cmd_sweep_time_to(args)
    if verb == "round1":
        return await _cmd_sweep_round1(args)
    if verb == "round2":
        return await _cmd_sweep_round2(args)
    if verb == "rank":
        return await _cmd_sweep_rank(args)
    raise SystemExit(f"sweep: unknown verb {verb!r}")


async def cmd_compare(args: argparse.Namespace) -> CommandResult:
    """PoBB-compare cycle winners across the family with adaptive top-up."""
    from promptpotter.application.bootstrap.scoring_context import populate_session_scoring
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
    "sweep": cmd_sweep,
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
