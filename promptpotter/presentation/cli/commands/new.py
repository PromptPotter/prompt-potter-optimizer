"""``cmd_new`` — mint a fresh campaign + run the loop from round 0.

``campaign_id = {dataset}__{rand6_hex}``, fresh per call. Declaration
(target + optimizer-prompt hash) is recorded as properties on
``campaign.json`` for resume-time drift detection, not used to derive the id."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.store.base import read_text_optional
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    _mint_session_and_cycle,
    _prepare_cycle,
    campaign_result_human,
    get_verbose,
    init_services_cli,
)
from promptpotter.presentation.cli.session import load_campaign_config, load_session
from promptpotter.presentation.views.startup_checklist import checkin_line

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.run_observers import RunObservers
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.cli.session import SessionCtx
    from promptpotter.presentation.views.live import LiveDisplay

logger = logging.getLogger("promptpotter.presentation.cli")


async def _checkin_task(
    session: Session,
    campaign_config: CampaignConfig,
    session_id: str,
    *,
    dataset_name: str,
    task_file: str | None,
    task_text: str | None,
) -> None:
    """Check in the task description; decomposed once into L1 fields.
    ``datasets/{name}/task_description.md`` is canonical; ``--task-file``/``--task-text`` override.
    Content-hash cached — unchanged description is a free hit."""
    from promptpotter.application.config import create_llm_client
    from promptpotter.application.optimization.task_context import decompose_task_context

    if task_file:
        task_description = Path(task_file).read_text(encoding="utf-8")
    elif task_text:
        task_description = task_text
    else:
        task_description = read_text_optional(
            Path("datasets") / dataset_name / "task_description.md"
        )

    if not task_description:
        checkin_line("task check-in", "no task description — skipped")
        return

    llm_client, model = create_llm_client(campaign_config)
    task_context, _consultation, was_cached = await decompose_task_context(
        task_description,
        llm_client,
        model,
        store_base_dir=session.store.base_dir if session.store else None,
        backend_id=session.backend_id,
    )
    n = len(task_context)
    checkin_line(
        "task check-in",
        f"{'cached' if was_cached else 'decomposed'} ({n} field{'' if n == 1 else 's'})",
    )
    state = session.store.sessions.read(session_id) or {}
    state["task_context"] = task_context.to_dict()
    session.store.sessions.update(session_id, state)


async def _mint_fresh_session(
    args: argparse.Namespace,
) -> tuple[Session, CampaignConfig, str, str]:
    """Find-or-create campaign + mint session + root cycle. No scoring (origin runs as phase 0 of the loop).
    Returns ``(session, campaign_config, dataset_name, session_id)``."""
    from promptpotter.application.config import load_campaign_config as _load_cfg
    from promptpotter.application.origin import prepare_datasets
    from promptpotter.infrastructure.store import session_index

    file_config = load_campaign_config(args.config)
    # Resolution order: positional dataset → --dataset-name → config["dataset_name"]
    dataset_name = (
        getattr(args, "dataset", None) or args.dataset_name or file_config.get("dataset_name")
    )
    if not dataset_name:
        from promptpotter.presentation.cli.session import no_dataset_hint

        raise SystemExit(
            "ERROR: `new` requires a dataset name. Pass it as a positional "
            "(`new aime`), via `--dataset-name <name>`, or via a `--config` "
            "that names one.\n\n" + no_dataset_hint()
        )

    # Auto-load dataset's campaign.json when --config wasn't given (else session persists with scoring=null + default knobs).
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

    if args.excel_path:
        train_data = prepare_datasets(session.store, args.excel_path).train_data or []
    else:
        train_data = session.samples or []

    pipeline_params, origin, cycle_id = _prepare_cycle(session, campaign_config, train_data)

    init_params = {
        "backend_url": args.backend_url,
        "backend_id": backend_id,
        "experiment_id": args.experiment_id,
        "dataset_name": dataset_name,
    }
    session_id, campaign_id, cycle_id = _mint_session_and_cycle(
        session,
        campaign_config,
        cycle_id=cycle_id,
        init_params=init_params,
        pipeline_params=pipeline_params,
        origin=origin,
        dataset_count=len(train_data),
    )

    sess_n = session_index(cycle_id)
    if sess_n == 1:
        checkin_line("campaign", f"minted {campaign_id} — session #1")
    else:
        checkin_line("campaign", f"joined {campaign_id} — session #{sess_n}")

    return session, campaign_config, dataset_name, session_id


def _build_live_display(
    args: argparse.Namespace,
    *,
    session: Session,
    campaign_config: CampaignConfig,
    origin_acc: float,
) -> LiveDisplay:
    """Build the CLI's LiveDisplay — verbose (-v) gets full notebook parity, else concise."""
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
    train_data: list[Sample],
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
        # ``new`` always starts at round 0, no resume.
        resumed_from_round=None,
        origin_accuracy=origin_acc,
    )


async def _run_sweep_batch(
    args: argparse.Namespace,
    root_ctx: SessionCtx,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
    sweep_payloads: list[tuple[Path, Any]],
) -> CommandResult:
    """Thin shim → ``application.sweep.run_sweep_batch``; binds observer factory to CLI args + campaign_config."""
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


async def _maybe_dispatch_sweep_batch(
    args: argparse.Namespace,
    ctx: SessionCtx,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
) -> CommandResult | None:
    """Multi-fork sweep dispatch: with ``--sweep-batch`` AND ``datasets/{name}/sweep/*.json``,
    mint one fork per OperatorSweepFile. ``None`` falls through to the normal path."""
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


async def _run_loop(
    args: argparse.Namespace,
    ctx: SessionCtx,
    campaign_config: CampaignConfig,
    session: Session,
    train_data: list[Sample],
) -> CommandResult:
    """Build observers, drive the optimization loop."""
    from promptpotter.application.runner import (
        run_optimization as _orch_run_optimization,
    )

    pre_origin_acc = ctx.state.get("origin_accuracy", 0.0)
    observers = _build_observers(args, session, campaign_config, train_data, pre_origin_acc)
    ctx.save_phase("optimizing")

    # Webapp "Stop run" channel: .runtime/stop.flag → loop exits at next stop_check.
    cycle_dir = session.store.campaigns.cycle_dir(ctx.campaign_id, ctx.cycle_id)
    session.stop_check = (cycle_dir / ".runtime" / "stop.flag").is_file

    cycle_result = await _orch_run_optimization(
        train_data,
        campaign_config,
        session=session,
        observers=observers,
        experiment_id=ctx.state["experiment_id"],
        task_context=ctx.task_context,
        resume_from_round_override=None,
        no_divergence_check=False,
        fork_on_divergence=False,
        sweep=getattr(args, "sweep", False),
        diag=getattr(args, "diag", False),
        halt_at_accuracy=getattr(args, "halt_at_accuracy", None),
        max_spend_usd=getattr(args, "max_spend_usd", None),
    )

    ctx.state["best_accuracy"] = cycle_result.best_accuracy
    ctx.save_phase("optimize")
    campaign_dir = session.store.campaigns.campaign_root_dir(ctx.campaign_id)
    return CommandResult(
        data=cycle_result.model_dump(),
        human=campaign_result_human(
            campaign_dir,
            dataset_name=ctx.init_params.get("dataset_name") or "?",
            cycle_id=cycle_result.cycle_id,
        ),
    )


def _pipeline_detail(session: Session) -> str:
    """Pipeline name + active-node summary for the pre-flight check-in line."""
    ps = session.pipeline_schema
    pipe = f"{ps.name} v{ps.version}" if ps else "pipeline unavailable"
    active = list((session.pipeline_params or {}).get("steps") or [])
    nodes = f"{len(active)} node{'' if len(active) == 1 else 's'}"
    if active:
        nodes += f" ({', '.join(active)})"
    return f"{pipe} · {nodes}"


async def cmd_new(args: argparse.Namespace) -> CommandResult:
    """Mint a fresh campaign and run the loop from round 0.
    Pre-flight: campaign → backend → dataset → pipeline → task → origin.
    Live state: ``cycles/{cycle_id}/dashboard.json``; digest: ``campaigns/{campaign_id}/log.md``;
    final: ``cycles/{cycle_id}/index.json::final``. Stop with Ctrl+C."""
    from promptpotter.shared.spend import refresh_rates

    refresh_rates(force=bool(getattr(args, "refresh_rates", False)))

    session, campaign_config, dataset_name, session_id = await _mint_fresh_session(args)

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return CommandResult(
            data={"error": "backend_unreachable", "backend_url": args.backend_url},
            human=f"Backend unreachable at {args.backend_url}. Start the backend and retry.",
        )
    checkin_line("backend", f"reachable at {args.backend_url}")

    train_data = session.samples or []
    checkin_line("dataset", f"{dataset_name} ({len(train_data)} queries)")
    checkin_line("pipeline", _pipeline_detail(session))

    await _checkin_task(
        session,
        campaign_config,
        session_id,
        dataset_name=dataset_name,
        task_file=args.task_file,
        task_text=args.task_text,
    )

    ctx = load_session(args)
    campaign_config = ctx.campaign_config
    session.session_id = ctx.session_id
    session.campaign_id = ctx.campaign_id
    session.state.cycle_id = ctx.cycle_id

    logger.info("Session: %s", session.store.sessions.session_dir(ctx.session_id))
    logger.info("Campaign: %s", session.store.campaigns.campaign_root_dir(ctx.campaign_id))

    if (
        sweep_result := await _maybe_dispatch_sweep_batch(args, ctx, campaign_config, train_data)
    ) is not None:
        return sweep_result

    checkin_line("origin", "launching origin scoring")
    return await _run_loop(args, ctx, campaign_config, session, train_data)


__all__ = ["cmd_new"]
