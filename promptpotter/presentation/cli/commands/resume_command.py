"""The launch is the command's own (``jobs/launcher/launch.py``); this verb owns its flags and the inline run."""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.cycle_addressing import cycle_target
from promptpotter.application.jobs.mint import ConfigDriftError
from promptpotter.application.pipeline_resolve import fork_preview
from promptpotter.config.logging import setup_logging
from promptpotter.domain.command_kinds import (
    ForkCyclePayload,
    ForkSeed,
    MintCampaignPayload,
    StartRunPayload,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.run_mode import RunMode
from promptpotter.presentation.cli.commands.launch import (
    confirm_tty,
    cycle_result_command,
    held_run,
    inline_launch,
    log_startup_summary,
    prepare_launch,
    run_inline,
)
from promptpotter.presentation.cli.commands.new import mint_and_run
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores
from promptpotter.presentation.cli.parsers import budget_from_args
from promptpotter.presentation.cli.session import load_session
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from promptpotter.application.jobs.launcher.run_job import HeldRun
    from promptpotter.presentation.cli.session import SessionCtx

logger = logging.getLogger("promptpotter.presentation.cli")


def _run_mode(args: argparse.Namespace, *, fork_on_divergence: bool) -> RunMode:
    """`--from`'s bound is `RunMode.from_round`'s, refused as every door refuses a payload."""
    return PayloadInvalidError.validating(
        RunMode,
        {
            "from_round": args.resume_from_round,
            "no_divergence_check": args.no_divergence_check,
            "fork_on_divergence": fork_on_divergence,
            "diag": args.diag,
        },
    )


def _start_run(
    args: argparse.Namespace, hop: CycleHop, *, fork_on_divergence: bool
) -> StartRunPayload:
    return StartRunPayload(
        campaign_id=hop.campaign_id,
        cycle_id=hop.cycle_id,
        budget=budget_from_args(args),
        step_rounds=args.step_rounds,
        mode=_run_mode(args, fork_on_divergence=fork_on_divergence),
    )


async def _dispatch_held(
    args: argparse.Namespace, ctx: SessionCtx, payload: StartRunPayload | ForkCyclePayload
) -> HeldRun:
    """*ctx* follows the cycle the command answers (a fork) and stays put when the hold is refused."""
    outcome = await CommandDispatcher(ctx.store, inline=inline_launch(args)).dispatch(
        CommandCall(payload)
    )
    held = held_run(outcome.result)
    ctx.cycle_id = held.session.hop.cycle_id
    return held


async def _dispatch_fork(
    args: argparse.Namespace,
    ctx: SessionCtx,
    *,
    from_round: int,
    seed: ForkSeed,
    keep_rounds: bool = False,
    reason: str = "",
) -> HeldRun:
    """A refused hold leaves no fork: the applier removes the stub it minted."""
    return await _dispatch_held(
        args,
        ctx,
        PayloadInvalidError.validating(
            ForkCyclePayload,
            {
                "campaign_id": ctx.campaign_id,
                "cycle_id": ctx.cycle_id,
                "round": from_round,
                "seed": seed,
                "budget": budget_from_args(args),
                "keep_rounds": keep_rounds,
                "reason": reason,
                "mode": _run_mode(args, fork_on_divergence=args.fork_on_divergence),
            },
        ),
    )


async def _fork_operator_rewind(args: argparse.Namespace, ctx: SessionCtx) -> HeldRun:
    rewind_to: int = args.rewind_to_round
    reason = args.rewind_reason.strip() or f"operator rewind to round {rewind_to}"
    parent_cycle_id = ctx.cycle_id
    held = await _dispatch_fork(
        args, ctx, from_round=rewind_to, seed=ForkSeed(), keep_rounds=True, reason=reason
    )
    logger.info(
        "Operator rewind: %s → %s at round %d [reason=%s]",
        parent_cycle_id,
        ctx.cycle_id,
        rewind_to,
        reason,
    )
    return held


def _steer_overlay(specs: list[str]) -> dict[str, dict[str, str]]:
    """Split only: the server reads each value in its param's declared type (`launching.py::_typed_steer`)."""
    overlay: dict[str, dict[str, str]] = {}
    for spec in specs:
        key, sep, raw = spec.partition("=")
        node, dot, param = key.strip().partition(".")
        if not sep or not dot or not node or not param:
            raise PayloadInvalidError(f"--steer expects NODE.PARAM=VALUE, got {spec!r}")
        overlay.setdefault(node, {})[param] = raw.strip()
    return overlay


async def _fork_operator_steer(args: argparse.Namespace, ctx: SessionCtx) -> HeldRun:
    """C0 is INHERITED; a steer to a non-PERMITTED model needs ``campaign.babysit``, the dispatcher's gate: this only warns."""
    overlay = _steer_overlay(args.steer)
    preview = fork_preview(ctx.store, ctx.campaign_id, overlay)
    disallowed = preview.steer != "clean"
    if disallowed:
        permitted = preview.permitted
        steered = ", ".join(
            f"{node}.{param}={value!r}"
            for node, cfg in sorted(overlay.items())
            for param, value in sorted(cfg.items())
        )
        print()
        print(f"⚠  Steering {steered} — NOT permitted by")
        print(f"   the origin: {permitted or '{} (nothing sanctioned)'}.")
        print("   This branch will be marked babysat (grade C); the origin's C0 is inherited.")
        print()
        # The cap is the authorization; the prompt is a courtesy, and a non-TTY run (None) proceeds on the cap.
        if confirm_tty("Proceed with the babysit steer?", default_no=True) is False:
            raise SystemExit("Cancelled. The active campaign is unchanged.")

    parent_cycle_id = ctx.cycle_id
    held = await _dispatch_fork(args, ctx, from_round=0, seed=ForkSeed(pipeline_overlay=overlay))
    logger.info(
        "Operator steer-fork (%s): %s → %s [overlay=%s]",
        "babysit, grade C" if disallowed else "clean, sanctioned values",
        parent_cycle_id,
        ctx.cycle_id,
        overlay,
    )
    return held


async def _pivot_to_fresh(
    args: argparse.Namespace, ctx: SessionCtx, drift: ConfigDriftError
) -> CommandResult:
    answer = confirm_tty(
        f"Start a fresh campaign on `{drift.dataset_name}` instead?",
        default_no=True,
        context=str(drift),
    )
    if answer is None:
        raise drift
    if not answer:
        raise SystemExit("Cancelled. Revert the config edits and retry `resume`.")
    return await mint_and_run(
        ctx.store,
        MintCampaignPayload(
            dataset_name=drift.dataset_name,
            budget=budget_from_args(args),
        ),
        inline_launch(args),
    )


def cmd_resume(args: argparse.Namespace) -> Coroutine[Any, Any, CommandResult]:
    prepare_launch()
    return _resume(args)


async def _resume(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = open_stores(args)
    ctx = load_session(stores, cycle_target(stores, args.campaign, args.cycle))

    rewinding = args.rewind_to_round is not None
    steering = bool(args.steer)
    if rewinding and steering:
        raise PayloadInvalidError("--rewind and --steer each cut a fork — pass one per `resume`.")
    if (rewinding or steering) and args.step_rounds is not None:
        raise PayloadInvalidError("--step-rounds steps a cycle's own run; a fork's run takes none.")

    try:
        if rewinding:
            held = await _fork_operator_rewind(args, ctx)
        elif steering:
            held = await _fork_operator_steer(args, ctx)
        else:
            held = await _dispatch_held(
                args, ctx, _start_run(args, ctx.hop, fork_on_divergence=args.fork_on_divergence)
            )
    except ConfigDriftError as drift:
        return await _pivot_to_fresh(args, ctx, drift)

    session = held.session
    logger.info(
        "Resuming cycle %s%s",
        ctx.cycle_id,
        "" if args.resume_from_round is None else f" from after round {args.resume_from_round}",
    )
    log_startup_summary(
        session,
        session.pipeline_params,
        len(session.bank.samples),
        session.backend_client.base_url,
        session.dataset_name,
    )
    logger.info("Campaign: %s", session.store.campaigns.campaign_root_dir(ctx.campaign_id))

    # A halt on divergence is the run's DIVERGED stop: its `next_acts` name the fork.
    cycle_result = await run_inline(
        held, mode=_run_mode(args, fork_on_divergence=args.fork_on_divergence)
    )
    return cycle_result_command(session, cycle_result)
