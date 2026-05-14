"""``cmd_optimize`` — the single write verb (fresh-init OR resume)."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.presentation.cli.commands._shared import (
    _DIVERGENCE_HINT,
    CommandResult,
    _mint_session_and_cycle,
    _prepare_cycle,
    get_verbose,
    init_services_cli,
    log_startup_summary,
)
from promptpotter.presentation.cli.commands.init import _run_init_body
from promptpotter.presentation.cli.session import load_session

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.run_observers import RunObservers
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.presentation.cli.session import SessionCtx
    from promptpotter.presentation.views.live import LiveDisplay

logger = logging.getLogger("promptpotter.presentation.cli")


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
    from promptpotter.application.run_observers import build_run_observers

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
        verbose=get_verbose(),
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

    # Wire the webapp's "Stop run" channel: presence of .runtime/stop.flag
    # signals the loop to exit at the next stop_check point. Pre-M12; the
    # M12 daemon will replace the flag with a proper control channel. See
    # docs/operations/human-in-the-loop.md.
    stop_flag = session.store.campaigns.campaign_dir(ctx.cycle_id) / ".runtime" / "stop.flag"
    session.stop_check = stop_flag.is_file

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
    """Run optimization loop.

    Fresh mode (``--config`` or ``--dataset-name`` present): mint a new
    session+cycle, then run from round 0. Resume mode (neither flag):
    pick up the active session.

    Live state is ``campaigns/{cycle_id}/dashboard.json``; digest is
    ``log.md``; final summary is ``index.json::final``. Stop with Ctrl+C.
    """
    # Keep the spend chip honest against provider re-pricing. The cache
    # has a 24 h TTL so this is a no-op on most starts; --refresh-rates
    # forces a fetch. Network failure logs + falls back to the prior cache
    # or the in-repo bundled floor — offline starts still resolve rates.
    from promptpotter.shared.spend import refresh_rates

    refresh_rates(force=bool(getattr(args, "refresh_rates", False)))

    fresh_mode = bool(args.config or args.dataset_name)
    fresh_session: Session | None = None
    if fresh_mode:
        bad = [
            flag
            for flag, set_ in (
                ("--from", getattr(args, "resume_from_round", None) is not None),
                ("--no-divergence-check", getattr(args, "no_divergence_check", False)),
                ("--fork-on-divergence", getattr(args, "fork_on_divergence", False)),
            )
            if set_
        ]
        if bad:
            raise SystemExit(
                f"ERROR: {', '.join(bad)} is resume-path only and cannot be combined "
                "with --config / --dataset-name (those mint a fresh cycle at round 0)."
            )
        _info, fresh_session = await _run_init_body(args)

    ctx = load_session(args)
    campaign_config = ctx.campaign_config
    # Fresh mode already paid the init_services_cli cost inside _run_init_body
    # (backend handshake, pipeline parse, dataset materialization). Reusing
    # that Session is the difference between "Parsed pipeline 'TermNorm' with
    # 1 steps" printing once vs twice.
    session = (
        fresh_session if fresh_session is not None else await init_services_cli(**ctx.init_params)
    )

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


__all__ = ["cmd_optimize"]
