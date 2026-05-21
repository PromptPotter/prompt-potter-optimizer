"""``cmd_new`` — start (or join) a campaign + run the loop from round 0.

``new`` is **find-or-create**: ``campaign_id`` is ``{dataset}__{hash}``,
derived from the origin declaration's content hash. A *new* declaration
mints a fresh :class:`Campaign`; re-running ``new`` on an *unchanged*
declaration resolves to the existing campaign and adds a **session** to
it — a fresh ``session_id`` plus a ``cycle_{hash}_s{N}`` root cycle. The
campaign dir (``campaigns/{campaign_id}/``) holds ``campaign.json`` +
``log.md``; per-session telemetry (``dashboard.json``) and round detail
live one level down under ``cycles/{cycle_id}/``.
"""

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
    log_startup_summary,
)
from promptpotter.presentation.cli.session import load_campaign_config, load_session

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.run_observers import RunObservers

logger = logging.getLogger("promptpotter.presentation.cli")


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


async def _mint_fresh_session(args: argparse.Namespace) -> tuple[dict[str, Any], Session]:
    """Find-or-create the campaign + mint a session + root cycle.

    ``campaign_id`` is derived from the origin declaration's content hash,
    so a re-run on an unchanged declaration joins the existing campaign as
    session N. ``auto_mint_session`` writes ``campaign.json`` (new campaign
    only), the session's root cycle index, and the 4-key active pointer.

    No scoring calls here — the origin runs as phase 0 of the optimize
    loop on the ``sp_budget_ttest`` slice. The one LLM cost is the
    ``restructure`` template call via :func:`_maybe_decompose_task`,
    content-hash cached at ``restructure_cache.json`` so subsequent ``new``
    runs against the same dataset are free.
    """
    from promptpotter.application.config import load_campaign_config as _load_cfg
    from promptpotter.application.origin import prepare_datasets

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
    session_id, campaign_id, cycle_id = _mint_session_and_cycle(
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

    from promptpotter.infrastructure.store import session_index

    sess_n = session_index(cycle_id)
    if sess_n == 1:
        logger.info(
            "Fresh campaign minted: campaign=%s session=%s cycle=%s dataset=%s (%d queries)",
            campaign_id,
            session_id,
            cycle_id,
            dataset_name,
            len(train_data),
        )
    else:
        logger.info(
            "Joined campaign %s as session #%d: session=%s cycle=%s dataset=%s (%d queries)",
            campaign_id,
            sess_n,
            session_id,
            cycle_id,
            dataset_name,
            len(train_data),
        )

    info = {
        "session_id": session_id,
        "campaign_id": campaign_id,
        "cycle_id": cycle_id,
        "backend_id": backend_id,
        "dataset_count": len(train_data),
        "active_steps": active,
        "excluded_nodes": excluded,
    }
    return info, session


def _build_live_display(
    args: argparse.Namespace,
    *,
    session: Session,
    campaign_config: CampaignConfig,
    origin_acc: float,
):
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
        # ``new`` always starts at round 0, no resume.
        resumed_from_round=None,
        origin_accuracy=origin_acc,
    )


async def _run_sweep_batch(
    args: argparse.Namespace,
    root_ctx,
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


async def _maybe_dispatch_sweep_batch(
    args: argparse.Namespace, ctx, campaign_config, train_data
) -> CommandResult | None:
    """Multi-fork sweep dispatch: with --sweep-batch AND a non-empty
    ``datasets/{name}/sweep/*.json`` directory, mint one fork per
    :class:`OperatorSweepFile` and run sweep mode on each. Returns None
    to fall through to the normal path (no --sweep-batch flag, no sweep
    dir, or empty payloads)."""
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
    args: argparse.Namespace, ctx, campaign_config, session, train_data
) -> CommandResult:
    """Build observers, drive the optimization loop."""
    from promptpotter.application.runner import (
        run_optimization as _orch_run_optimization,
    )

    pre_origin_acc = ctx.state.get("origin_accuracy", 0.0)
    observers = _build_observers(args, session, campaign_config, train_data, pre_origin_acc)
    ctx.save_phase("optimizing")

    # Wire the webapp's "Stop run" channel: presence of .runtime/stop.flag
    # signals the loop to exit at the next stop_check point. See
    # docs/operations/human-in-the-loop.md.
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


async def cmd_new(args: argparse.Namespace) -> CommandResult:
    """Start (or join) a campaign and run the loop from round 0.

    Find-or-create on the origin declaration's content hash: a new
    declaration mints a fresh campaign, a re-run of an unchanged one adds
    a session to the existing campaign. Live state is
    ``cycles/{cycle_id}/dashboard.json``; the campaign digest is
    ``campaigns/{campaign_id}/log.md``; the final summary is
    ``cycles/{cycle_id}/index.json::final``. Stop with Ctrl+C.
    """
    from promptpotter.shared.spend import refresh_rates

    refresh_rates(force=bool(getattr(args, "refresh_rates", False)))

    _info, session = await _mint_fresh_session(args)

    ctx = load_session(args)
    campaign_config = ctx.campaign_config

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return CommandResult(
            data={"error": "backend_unreachable", "backend_url": ctx.backend_url},
            human=f"Backend unreachable at {ctx.backend_url}. Start the backend and retry.",
        )

    train_data = session.samples or []
    session.session_id = ctx.session_id
    session.campaign_id = ctx.campaign_id
    session.state.cycle_id = ctx.cycle_id

    log_startup_summary(
        session,
        session.pipeline_params,
        len(train_data),
        ctx.backend_url,
        ctx.init_params["dataset_name"],
    )
    logger.info("Session: %s", session.store.sessions.session_dir(ctx.session_id))
    logger.info("Campaign: %s", session.store.campaigns.campaign_root_dir(ctx.campaign_id))

    if (
        sweep_result := await _maybe_dispatch_sweep_batch(args, ctx, campaign_config, train_data)
    ) is not None:
        return sweep_result

    return await _run_loop(args, ctx, campaign_config, session, train_data)


__all__ = ["cmd_new"]
