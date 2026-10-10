"""The launch is the command's own (``jobs/launcher/launch.py``); this verb owns its flags and the inline run."""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.launching import run_mode_of
from promptpotter.application.commands.payloads import (
    ForkCyclePayload,
    RunShape,
    StartRunPayload,
)
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.mint import ConfigDriftError
from promptpotter.config.logging import setup_logging
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.pipeline_overlay import (
    permitted_models_for_campaign,
    steers_disallowed_model,
)
from promptpotter.infrastructure.runtime_flags import is_checkin
from promptpotter.presentation.cli.commands.launch import (
    backend_unreachable_result,
    confirm_tty,
    cycle_result_command,
    divergence_hint,
    held_run,
    inline_launch,
    log_startup_summary,
    prepare_launch,
    run_inline,
)
from promptpotter.presentation.cli.commands.new import mint_and_run
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores, resolve_target
from promptpotter.presentation.cli.parsers import launch_limits_from_args
from promptpotter.presentation.cli.session import load_session, no_dataset_hint
from promptpotter.shared.errors import PotterError, ResumeDivergenceError

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from promptpotter.application.jobs.launcher.run_job import HeldRun
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import CycleResult
    from promptpotter.presentation.cli.session import SessionCtx

logger = logging.getLogger("promptpotter.presentation.cli")


def _run_shape(args: argparse.Namespace, *, fork_on_divergence: bool) -> RunShape:
    return RunShape(
        from_round=args.resume_from_round,
        no_divergence_check=args.no_divergence_check,
        fork_on_divergence=fork_on_divergence,
        diag=args.diag,
    )


def _start_run(
    args: argparse.Namespace, hop: CycleHop, *, fork_on_divergence: bool
) -> StartRunPayload:
    return StartRunPayload(
        campaign_id=hop.campaign_id,
        cycle_id=hop.cycle_id,
        **_run_shape(args, fork_on_divergence=fork_on_divergence).model_dump(),
        **launch_limits_from_args(args).model_dump(),
    )


async def _dispatch_held(
    args: argparse.Namespace, ctx: SessionCtx, payload: StartRunPayload | ForkCyclePayload
) -> HeldRun:
    """*ctx* follows the cycle the command answers (a fork, a diag sibling) and stays put when the hold is refused."""
    outcome = await CommandDispatcher(ctx.store, inline=inline_launch(args)).dispatch_cycle_command(
        CommandCall(payload, uuid.uuid4().hex), expected_version=None
    )
    held = held_run(outcome.result)
    ctx.cycle_id = held.session.hop.cycle_id
    return held


async def _dispatch_fork(
    args: argparse.Namespace,
    ctx: SessionCtx,
    *,
    from_round: int,
    seed: dict[str, Any],
    keep_rounds: bool = False,
    reason: str = "",
) -> HeldRun:
    """A refused hold leaves no fork: the applier removes the stub it minted."""
    try:
        return await _dispatch_held(
            args,
            ctx,
            ForkCyclePayload(
                campaign_id=ctx.campaign_id,
                cycle_id=ctx.cycle_id,
                round=from_round,
                seed=seed,
                keep_rounds=keep_rounds,
                reason=reason,
                **_run_shape(args, fork_on_divergence=args.fork_on_divergence).model_dump(),
            ),
        )
    except (ConfigDriftError, BackendUnreachableError):
        raise
    except PotterError as exc:
        raise SystemExit(f"ERROR: fork refused — {exc}") from exc


async def _fork_operator_rewind(args: argparse.Namespace, ctx: SessionCtx) -> HeldRun:
    rewind_to: int = args.rewind_to_round
    if rewind_to < 0:
        raise SystemExit(f"ERROR: --rewind must be >= 0, got {rewind_to}")
    if rewind_to == 0:
        raise SystemExit("ERROR: --rewind 0 mints a fork at the cycle root. Use `--diag` instead.")

    reason = args.rewind_reason.strip() or f"operator rewind to round {rewind_to}"
    parent_cycle_id = ctx.cycle_id
    held = await _dispatch_fork(
        args, ctx, from_round=rewind_to, seed={}, keep_rounds=True, reason=reason
    )
    logger.info(
        "Operator rewind: %s → %s at round %d [reason=%s]",
        parent_cycle_id,
        ctx.cycle_id,
        rewind_to,
        reason,
    )
    return held


def _steer_overlay(specs: list[str], schema: PipelineSchema) -> dict[str, dict[str, Any]]:
    """A value is read in the param's DECLARED type, as the browser coerces (`nodeConfig.ts::coerce`): a string stays text, anything else is JSON."""
    overlay: dict[str, dict[str, Any]] = {}
    for spec in specs:
        key, sep, raw = spec.partition("=")
        node, dot, param = key.strip().partition(".")
        if not sep or not dot or not node or not param:
            raise SystemExit(f"ERROR: --steer expects NODE.PARAM=VALUE, got {spec!r}")
        declared_node = schema.get_node(node)
        if declared_node is None:
            names = ", ".join(n.name for n in schema.declared_nodes) or "(none)"
            raise SystemExit(f"ERROR: --steer names no node called {node!r}; nodes: {names}")
        kind = declared_node.param_types.get(param)
        if kind is None:
            known = ", ".join(sorted(declared_node.param_types)) or "(none)"
            raise SystemExit(f"ERROR: --steer: {node!r} carries no {param!r}; params: {known}")
        value: Any = raw.strip()
        if kind != "string":
            try:
                value = json.loads(value)
            except ValueError:
                raise SystemExit(
                    f"ERROR: --steer {node}.{param} is declared {kind}; {value!r} is not one "
                    f"(spell it as JSON)."
                ) from None
        overlay.setdefault(node, {})[param] = value
    return overlay


async def _fork_operator_steer(args: argparse.Namespace, ctx: SessionCtx) -> HeldRun:
    """C0 is INHERITED; a steer to a non-PERMITTED model needs ``campaign.babysit``, the dispatcher's gate: this only warns."""
    # The overlay is typed by the backend's own schema, which only a session holds.
    typing_session = await init_services(
        backend_url=ctx.campaign.backend_url,
        backend_id=ctx.campaign.backend_id,
        dataset_name=ctx.campaign.dataset_name,
        identity=ctx.store.identity,
    )
    overlay = _steer_overlay(args.steer, typing_session.pipeline_schema)

    # The fork-cycle applier's own gate, over the origin's FROZEN config: `ctx.campaign_config` is the one the seed moved.
    frozen_config = ctx.campaign.config
    disallowed = steers_disallowed_model(frozen_config, overlay)
    if disallowed:
        permitted = permitted_models_for_campaign(frozen_config)
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

    seed: dict[str, Any] = {"pipeline_overlay": overlay}
    if args.steer_max_rounds is not None:
        seed["config_overrides"] = {"max_rounds": args.steer_max_rounds}
    parent_cycle_id = ctx.cycle_id
    held = await _dispatch_fork(args, ctx, from_round=0, seed=seed)
    logger.info(
        "Operator steer-fork (%s): %s → %s [overlay=%s]",
        "babysit, grade C" if disallowed else "clean, sanctioned values",
        parent_cycle_id,
        ctx.cycle_id,
        overlay,
    )
    return held


def _divergence_result(div: ResumeDivergenceError) -> CommandResult:
    return CommandResult(
        data={
            "error": "resume_divergence",
            "round": div.round_num,
            "kind": div.kind,
            "recorded_outcome": div.recorded_outcome,
            "current_outcome": div.current_outcome,
        },
        human=f"{div}\n\n{divergence_hint()}",
    )


async def _run_loop(args: argparse.Namespace, ctx: SessionCtx, held: HeldRun) -> CommandResult:
    fork_on_divergence: bool = args.fork_on_divergence
    cycle_result: CycleResult
    try:
        cycle_result = await run_inline(
            held, mode=run_mode_of(_run_shape(args, fork_on_divergence=fork_on_divergence))
        )
    except ResumeDivergenceError as div:
        if fork_on_divergence:
            return _divergence_result(div)
        # A non-TTY (None) falls through to the structured error, so a script gets its exit code.
        print()
        print(str(div))
        print()
        print(f"Active cycle: {ctx.cycle_id}")
        print("Forking branches a sibling cycle at this point under the current scorer.")
        print("The parent campaign is preserved untouched.")
        answer = confirm_tty("Fork here?", default_no=True)
        if answer is None:
            return _divergence_result(div)
        if not answer:
            return CommandResult(
                data={"cancelled": True, "reason": "divergence_declined"},
                human="Cancelled. The active campaign is unchanged.",
            )
        logger.info(
            "Operator accepted fork on divergence — re-running with fork_on_divergence=True"
        )
        # The refused run handed its slot back; the re-run is a launch — and a command — of its own.
        rerun = _start_run(args, ctx.hop, fork_on_divergence=True)
        held = await _dispatch_held(args, ctx, rerun)
        cycle_result = await run_inline(held, mode=run_mode_of(rerun))

    return cycle_result_command(held.session, cycle_result)


async def _pivot_to_fresh(
    args: argparse.Namespace, ctx: SessionCtx, drift: ConfigDriftError
) -> CommandResult:
    print()
    print(str(drift))
    print()
    answer = confirm_tty(
        f"Start a fresh campaign on `{drift.dataset_name}` instead?", default_no=True
    )
    if answer is None:
        raise SystemExit(f"ERROR: {drift}")
    if not answer:
        raise SystemExit("Cancelled. Revert the config edits and retry `resume`.")
    return await mint_and_run(
        argparse.Namespace(
            command="new",
            dataset=drift.dataset_name,
            dataset_name=None,
            config=None,
            task_file=None,
            task_text=None,
            slug=None,
            sets=[],
            arm=None,
            backend_url=ctx.campaign.backend_url,
            backend_id=ctx.campaign.backend_id,
            diag=False,
            no_wait=args.no_wait,
            halt_at_accuracy=args.halt_at_accuracy,
            spend_budget_usd=args.spend_budget_usd,
            token_budget=args.token_budget,
            tenant=args.tenant,
            verbose=args.verbose,
            json_output=args.json_output,
        )
    )


def cmd_resume(args: argparse.Namespace) -> Coroutine[Any, Any, CommandResult]:
    prepare_launch(args)
    return _resume(args)


async def _resume(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = open_stores(args)
    campaign_id, cycle_id = resolve_target(args, stores)
    if not campaign_id or not cycle_id:
        raise SystemExit(
            "ERROR: No active session.\n\n"
            "To start a campaign, run `new` against a dataset:\n\n" + no_dataset_hint()
        )
    ctx = load_session(stores, CycleHop(campaign_id=campaign_id, cycle_id=cycle_id))
    campaign = ctx.campaign
    if is_checkin(stores.campaigns.cycle_dir(campaign.root_hop)):
        raise SystemExit(
            f"ERROR: campaign '{ctx.campaign_id}' is still in check-in — its origin "
            "isn't authored yet, so there's nothing to resume.\n"
            "Finish + Start it in the webapp, or run "
            "`python -m promptpotter new <file>` to author and run from the CLI."
        )

    resume_from_round: int | None = args.resume_from_round
    if resume_from_round is not None and resume_from_round < 0:
        raise SystemExit(f"ERROR: --from must be >= 0, got {resume_from_round}")
    rewinding = args.rewind_to_round is not None
    steering = bool(args.steer)
    if rewinding and steering:
        raise SystemExit("ERROR: --rewind and --steer each cut a fork — pass one per `resume`.")

    try:
        if rewinding:
            held = await _fork_operator_rewind(args, ctx)
        elif steering:
            held = await _fork_operator_steer(args, ctx)
        else:
            held = await _dispatch_held(
                args, ctx, _start_run(args, ctx.hop, fork_on_divergence=args.fork_on_divergence)
            )
    except BackendUnreachableError as exc:
        return backend_unreachable_result(exc)
    except ConfigDriftError as drift:
        return await _pivot_to_fresh(args, ctx, drift)

    session = held.session
    logger.info(
        "Resuming cycle %s%s",
        ctx.cycle_id,
        "" if resume_from_round is None else f" from after round {resume_from_round}",
    )
    log_startup_summary(
        session,
        session.pipeline_params,
        len(session.samples),
        session.backend_client.base_url,
        session.dataset_name,
    )
    logger.info("Campaign: %s", session.store.campaigns.campaign_root_dir(ctx.campaign_id))

    return await _run_loop(args, ctx, held)


__all__ = ["cmd_resume"]
