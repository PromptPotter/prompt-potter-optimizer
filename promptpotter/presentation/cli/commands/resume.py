"""``cmd_resume`` — continue the active campaign.

Bare ``resume`` picks up from the latest completed round. ``--from N``
rewinds in place. ``--fork-on-divergence`` mints a sibling cycle when
the decision-replay walker disagrees with the recorded trail.
``--no-check`` accepts divergence silently. ``--diag`` runs diagnostic
mode against the active cycle (branches a counted sibling on top of a
prior diag cycle).

Pipeline-content drift between the active session and the current
on-disk config is treated as operator error here: the recomputed
cycle hash no longer matches the active pointer, and resuming would
silently start something different. We error out and point at ``new``.
"""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from promptpotter.presentation.cli.commands._shared import (
    _DIVERGENCE_HINT,
    CommandResult,
    _prepare_cycle,
    confirm_tty,
    init_services_cli,
    log_startup_summary,
)
from promptpotter.presentation.cli.commands.new import _build_observers
from promptpotter.presentation.cli.session import load_session

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig

logger = logging.getLogger("promptpotter.presentation.cli")


class _PivotToFreshError(Exception):
    """Operator confirmed at the drift prompt: pivot resume → new on this dataset."""

    def __init__(self, dataset_name: str) -> None:
        self.dataset_name = dataset_name


def _prepare_cycle_for_resume(
    args: argparse.Namespace,
    ctx,
    session: Session,
    campaign_config: CampaignConfig,
    train_data: list,
    *,
    pivot_prompt: bool = True,
) -> dict:
    """Apply pipeline to session + verify the active cycle still matches.

    On content-hash drift (pipeline.json model swap, origin prompt edit,
    dataset change) the resume target is no longer the right cycle. If we
    have a TTY and ``pivot_prompt=True``, prompt the operator to pivot to
    a fresh ``new`` run (the common case after editing config). Sweep
    callers pass ``pivot_prompt=False`` — pivoting to a fresh root would
    silently turn a sweep into a regular new-campaign run, which is not
    what the operator asked for. Non-TTY ⇒ structured error so scripts
    get a deterministic exit-code path.
    """
    resume_from_round: int | None = getattr(args, "resume_from_round", None)
    pipeline_params, _origin, expected_cycle_id = _prepare_cycle(
        session, campaign_config, train_data
    )

    if ctx.cycle_id and expected_cycle_id != ctx.cycle_id:
        dataset_name = ctx.init_params.get("dataset_name") or "<dataset>"
        drift_error_msg = (
            f"ERROR: pipeline content changed since the active session was minted.\n"
            f"  active cycle:   {ctx.cycle_id}\n"
            f"  current hash:   {expected_cycle_id}\n"
            f"\n"
            f"Resuming under a different content hash would silently start a "
            f"different experiment. Either:\n"
            f"  • `python -m promptpotter new {dataset_name}` to mint a fresh "
            f"root with the new config (prior campaign preserved), or\n"
            f"  • revert the pipeline.json / campaign.json / task_description "
            f"edits that changed the hash and retry `resume`."
        )
        if not pivot_prompt:
            raise SystemExit(drift_error_msg)
        print()
        print("Pipeline content changed since the active session was minted.")
        print(f"  active cycle:   {ctx.cycle_id}")
        print(f"  current hash:   {expected_cycle_id}")
        print()
        print("Resuming under a different content hash would silently start a")
        print("different experiment. Starting fresh mints a brand-new root cycle")
        print("with the current config (the prior campaign is preserved).")
        answer = confirm_tty(f"Start fresh campaign on `{dataset_name}` instead?", default_no=True)
        if answer is None:
            raise SystemExit(drift_error_msg)
        if not answer:
            raise SystemExit("Cancelled. The active campaign is unchanged.")
        raise _PivotToFreshError(dataset_name)

    if resume_from_round is not None:
        if not ctx.cycle_id:
            raise SystemExit(
                "ERROR: `resume --from N` requires an active cycle on this session.\n"
                "Run `python -m promptpotter new <dataset>` first."
            )
        if resume_from_round < 0:
            raise SystemExit(f"ERROR: --from must be >= 0, got {resume_from_round}")
        logger.info("Resuming cycle %s from after round %d", ctx.cycle_id, resume_from_round)
    elif ctx.cycle_id:
        logger.info("Resuming cycle %s", ctx.cycle_id)

    return pipeline_params


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


async def _drive_optimization(
    args: argparse.Namespace,
    ctx,
    campaign_config,
    session,
    train_data,
    *,
    fork_on_divergence: bool,
):
    """One pass through the loop. Caller handles divergence menu + re-invoke."""
    from promptpotter.application.runner import (
        run_optimization as _orch_run_optimization,
    )

    pre_origin_acc = ctx.state.get("origin_accuracy", 0.0)
    observers = _build_observers(args, session, campaign_config, train_data, pre_origin_acc)
    ctx.save_phase("optimizing")

    stop_flag = session.store.campaigns.campaign_dir(ctx.cycle_id) / ".runtime" / "stop.flag"
    session.stop_check = stop_flag.is_file

    cycle_result = await _orch_run_optimization(
        train_data,
        campaign_config,
        session=session,
        observers=observers,
        experiment_id=ctx.state["experiment_id"],
        task_context=ctx.task_context,
        resume_from_round_override=getattr(args, "resume_from_round", None),
        no_divergence_check=getattr(args, "no_divergence_check", False),
        fork_on_divergence=fork_on_divergence,
        sweep=False,
        diag=getattr(args, "diag", False),
        halt_at_accuracy=getattr(args, "halt_at_accuracy", None),
        max_spend_usd=getattr(args, "max_spend_usd", None),
    )
    return cycle_result


async def _run_loop(
    args: argparse.Namespace, ctx, campaign_config, session, train_data
) -> CommandResult:
    """Drive the loop. On ``ResumeDivergenceError``, prompt the operator (if
    TTY) to fork inline; on yes, re-run the loop once with
    ``fork_on_divergence=True``."""
    from promptpotter.shared.errors import ResumeDivergenceError

    fork_on_divergence = bool(getattr(args, "fork_on_divergence", False))
    try:
        cycle_result = await _drive_optimization(
            args,
            ctx,
            campaign_config,
            session,
            train_data,
            fork_on_divergence=fork_on_divergence,
        )
    except ResumeDivergenceError as div:
        # Already pre-authorized (--fork-on-divergence set, but the trigger
        # fired before its first checkpoint) — no menu, propagate.
        if fork_on_divergence:
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
        # Interactive: show context, ask y/N. On non-TTY, fall through to
        # the structured error so scripts keep their exit-code contract.
        print()
        print(str(div))
        print()
        print(f"Active cycle: {ctx.cycle_id}")
        print("Forking branches a sibling cycle at this point under the current scorer.")
        print("The parent campaign is preserved untouched.")
        answer = confirm_tty("Fork here?", default_no=True)
        if answer is None:
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
        if not answer:
            return CommandResult(
                data={"cancelled": True, "reason": "divergence_declined"},
                human="Cancelled. The active campaign is unchanged.",
            )
        logger.info(
            "Operator accepted fork on divergence — re-running with fork_on_divergence=True"
        )
        cycle_result = await _drive_optimization(
            args,
            ctx,
            campaign_config,
            session,
            train_data,
            fork_on_divergence=True,
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


async def cmd_resume(args: argparse.Namespace) -> CommandResult:
    """Resume the active campaign. Flags drive rewind / divergence / diag modes."""
    from promptpotter.shared.spend import refresh_rates

    refresh_rates(force=bool(getattr(args, "refresh_rates", False)))

    ctx = load_session(args)
    if not ctx.cycle_id:
        raise SystemExit(
            "ERROR: no active campaign to resume. Run `python -m promptpotter new <dataset>` first."
        )
    campaign_config = ctx.campaign_config
    session = await init_services_cli(**ctx.init_params)

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return CommandResult(
            data={"error": "backend_unreachable", "backend_url": ctx.backend_url},
            human=f"Backend unreachable at {ctx.backend_url}. Start the backend and retry.",
        )

    train_data = session.samples or []
    try:
        pipeline_params = _prepare_cycle_for_resume(args, ctx, session, campaign_config, train_data)
    except _PivotToFreshError as pivot:
        # Operator chose at the drift prompt: pivot to a fresh `new` run.
        # Synthesize a `new` namespace from the active session's dataset +
        # the resume args's halt/spend knobs; dispatch directly so we don't
        # re-init services from scratch.
        from promptpotter.presentation.cli.commands.new import cmd_new

        new_args = argparse.Namespace(
            command="new",
            dataset=pivot.dataset_name,
            dataset_name=None,
            config=None,
            task_file=None,
            task_text=None,
            excel_path=None,
            backend_url=ctx.init_params.get("backend_url"),
            backend_id=ctx.init_params.get("backend_id"),
            experiment_id=ctx.init_params.get("experiment_id"),
            sweep=False,
            diag=False,
            halt_at_accuracy=getattr(args, "halt_at_accuracy", None),
            max_spend_usd=getattr(args, "max_spend_usd", None),
            refresh_rates=False,
            tenant=getattr(args, "tenant", "default"),
            verbose=getattr(args, "verbose", False),
            session=getattr(args, "session", None),
            json_output=getattr(args, "json_output", False),
        )
        return await cmd_new(new_args)

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

    return await _run_loop(args, ctx, campaign_config, session, train_data)


__all__ = ["cmd_resume"]
