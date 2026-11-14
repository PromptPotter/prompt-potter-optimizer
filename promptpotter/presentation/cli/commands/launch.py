"""The launch is the command's own (``jobs/launcher/launch.py``); this verb owns its flags and the inline run."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import json
import logging
import re
import shlex
import signal
import sys
from collections.abc import Callable, Coroutine, Iterator, Sequence
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from promptpotter import connectors
from promptpotter.application.account import local_stores
from promptpotter.application.commands import (
    draft_editing,
    launching,
    origin_resolving,
    workspace_edits,
)
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.launching import dispatch_start_checkin
from promptpotter.application.cycle_addressing import SessionCtx, cycle_target, load_session
from promptpotter.application.datasets.authored import (
    campaign_file_block,
)
from promptpotter.application.datasets.loaders import no_dataset_hint
from promptpotter.application.datasets.origin_readiness import checklist_field
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.launcher.launch import Inline, Launched
from promptpotter.application.jobs.launcher.mint_and_start import OriginIncompleteError
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.mint import ConfigDriftError
from promptpotter.application.pipeline_resolve import fork_preview
from promptpotter.application.views.render.phase import render_completion
from promptpotter.config.first_run import ensure_api_key
from promptpotter.config.logging import setup_logging
from promptpotter.domain.account_words import refusal_words
from promptpotter.domain.checkin_draft import OriginResolution
from promptpotter.domain.command_kinds import (
    ArmRequest,
    CancelPayload,
    CommandPayload,
    CyclePayload,
    Door,
    EditDraftCampaignPayload,
    ForkCyclePayload,
    ForkSeed,
    HeadToHeadRequest,
    IngestUploadPayload,
    MintCampaignPayload,
    MintHeadToHeadPayload,
    PauseCyclePayload,
    ResolveOriginPayload,
    StartCheckinPayload,
    StartRunPayload,
)
from promptpotter.domain.connector import BACKEND_REACHABILITY_INFO
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.draft_edit import EditDraftPatch, OptimizationOverrides
from promptpotter.domain.layer_fold import wire_value
from promptpotter.domain.phases import stop_reason_outcome
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.records.run_records import LaunchAnswer
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.strict_model import MAX_UPLOAD_BYTES
from promptpotter.presentation.cli.main import (
    ARM_GRAMMAR,
    SET_GRAMMAR,
    VERBS,
    CommandResult,
    budget_from_args,
    build_parser,
)
from promptpotter.shared.errors import (
    ConflictError,
    DatasetAccessError,
    PayloadInvalidError,
    PotterError,
)

logger = logging.getLogger("promptpotter.presentation.cli")


def prepare_launch() -> None:
    """Runs BEFORE the loop exists: a prompt or refusal here never meets a run's Ctrl+C handler."""
    ensure_api_key()


def pipeline_summary(session: Session, pipeline_params: PipelineParams) -> str:
    pipe = session.backend_client.connector.name
    active = list(pipeline_params.steps or [])
    nodes = f"{len(active)} node{'s' if len(active) != 1 else ''}"
    if active:
        nodes += f" ({', '.join(active)})"
    return f"{pipe} · {nodes}"


def log_startup_summary(
    session: Session,
    pipeline_params: PipelineParams,
    dataset_len: int,
    backend_url: str,
    dataset_name: str | None,
) -> None:
    ds = f"{dataset_name or '?'} ({dataset_len} queries)"
    logger.info(
        "%s · backend %s · dataset %s", pipeline_summary(session, pipeline_params), backend_url, ds
    )


def backend_reach_line(connector: str, backend_url: str) -> str:
    """Said of a held run, whose admission probed the backend live; an ``in_process`` connector was
    never contacted, so no URL is claimed for it."""
    reached = BACKEND_REACHABILITY_INFO["live"].label
    if connectors.CONNECTORS.get(connector).execution == "in_process":
        return f"{reached} in-process ({connector}) — no wire"
    return f"{reached} at {backend_url}"


def inline_launch(args: argparse.Namespace) -> Inline:
    return Inline(no_wait=args.no_wait)


def held_run(launched: object) -> HeldRun:
    assert isinstance(launched, Launched), "a launching command answers its launch"
    assert launched.held is not None, "an inline launch is held, never detached"
    return launched.held


def ctrl_c_pauses(
    dispatcher: CommandDispatcher, pauses: Sequence[PauseCyclePayload]
) -> contextlib.AbstractContextManager[Callable[[CyclePayload], None]]:
    """The first Ctrl+C sends *pauses* as the browser's press does, and nothing more; the second force-quits."""

    async def _pause_each() -> None:
        for pause in pauses:
            with contextlib.suppress(PotterError):
                await dispatcher.dispatch(CommandCall(pause, door=Door.TERMINAL))

    return _ctrl_c(dispatcher, _pause_each)


def ctrl_c_stops_pass(
    dispatcher: CommandDispatcher, call: CommandCall[Any]
) -> contextlib.AbstractContextManager[Callable[[CyclePayload], None]]:
    """A paid pass's Ctrl+C: the `cancel` of *call*'s command, as every other door's; the second
    press force-quits."""

    async def _stop() -> None:
        with contextlib.suppress(PotterError):
            cancel = CancelPayload(command_id=call.command_id)
            await dispatcher.dispatch(CommandCall(cancel, door=Door.TERMINAL))

    return _ctrl_c(dispatcher, _stop)


@contextlib.contextmanager
def _ctrl_c(
    dispatcher: CommandDispatcher, stop: Callable[[], Coroutine[Any, Any, None]]
) -> Iterator[Callable[[CyclePayload], None]]:
    loop = asyncio.get_running_loop()
    asked: set[asyncio.Task[None]] = set()

    async def send(payload: CyclePayload) -> None:
        try:
            await dispatcher.dispatch(CommandCall(payload, door=Door.TERMINAL))
        except PotterError as exc:
            print(f"  {refusal_words(exc)}", flush=True)

    def _spawn(coro: Coroutine[Any, Any, None]) -> None:
        task = loop.create_task(coro)
        asked.add(task)
        task.add_done_callback(asked.discard)

    def dispatch(payload: CyclePayload) -> None:
        _spawn(send(payload))

    interrupted = False

    def on_sigint(_signum: int, _frame: object) -> None:
        nonlocal interrupted
        if interrupted:
            raise KeyboardInterrupt
        interrupted = True
        loop.call_soon_threadsafe(lambda: _spawn(stop()))

    previous = signal.signal(signal.SIGINT, on_sigint)
    try:
        yield dispatch
    finally:
        signal.signal(signal.SIGINT, previous)


async def run_inline(held: HeldRun) -> CycleResult:
    hop = held.session.hop
    pause = PauseCyclePayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, reason="Ctrl+C")
    with ctrl_c_pauses(CommandDispatcher(held.session.store), [pause]):
        _, result = await held.run_inline(readout_sink=functools.partial(print, flush=True))
    return result


def cycle_result_command(session: Session, cycle_result: CycleResult) -> CommandResult:
    return CommandResult(
        data=cycle_result.model_dump(),
        human=render_completion(
            cycle_result,
            pipeline_schema=session.pipeline_schema,
            dataset_name=session.dataset_name,
            campaign_dir=session.store.campaigns.campaign_root_dir(session.campaign_id),
        ),
        exit_code=stop_reason_outcome(cycle_result.stop_reason).exit_code,
    )


def confirm_tty(prompt: str, *, default_no: bool = True, context: str = "") -> bool | None:
    """``None`` = stdin is no TTY: the caller picks its non-interactive default. *context* is
    said only to the person the prompt asks."""
    if not sys.stdin.isatty():
        return None
    if context:
        print(f"\n{context}\n")
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    try:
        raw = input(prompt + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not raw:
        return not default_no
    return raw in {"y", "yes"}


_KNOB_COMMA = re.compile(r",(?=[\w.]+=)")
# A dataset name confirms no check-in field: its `--set` lays the campaign knobs alone.
_KNOBS = "config.optimization"


def _checkin_line(step: str, detail: str) -> None:
    logger.info("✓ %-13s — %s", step, detail)


def _members(annotation: object) -> tuple[Any, ...]:
    return tuple(m for m in (get_args(annotation) or (annotation,)) if m is not type(None))


def _laid(into: object, annotation: object, rest: list[str], raw: str, path: str) -> Any:
    """*raw* laid at *rest* under a field typed *annotation*: a model by its fields, a mapping by
    any key, ``Any`` as it comes; at the leaf a field that takes a string keeps the word."""
    members = _members(annotation) if get_origin(annotation) is not dict else (annotation,)
    if not rest:
        try:
            return wire_value(raw, text=str in members)
        except ValueError as exc:
            raise PayloadInvalidError(f"--set {path}: {exc}") from None
    for member in members:
        if isinstance(member, type) and issubclass(member, BaseModel):
            return _lay(into if isinstance(into, dict) else {}, member, rest, raw, path)
        if get_origin(member) is dict or member is Any:
            value_type = get_args(member)[1] if member is not Any else Any
            key, *more = rest
            out = into if isinstance(into, dict) else {}
            out[key] = _laid(out.get(key), value_type, more, raw, path)
            return out
    raise PayloadInvalidError(f"{path!r} reaches past a field that takes a value.")


def _lay(
    body: dict[str, Any], model: type[BaseModel], path: list[str], raw: str, whole: str
) -> Any:
    head, *rest = path
    info = model.model_fields.get(head)
    if info is None and model.model_config.get("extra") != "allow":
        raise PayloadInvalidError(
            f"{whole!r} names no field of {model.__name__}: {head!r} is not one of "
            f"{', '.join(model.model_fields)}."
        )
    body[head] = _laid(body.get(head), Any if info is None else info.annotation, rest, raw, whole)
    return body


def _set_body(model: type[BaseModel], sets: Sequence[str]) -> dict[str, Any]:
    """Each ``PATH=VALUE`` laid by *model*'s own dotted paths; every bound is the model's."""
    body: dict[str, Any] = {}
    for item in sets:
        path, sep, raw = item.partition("=")
        if not sep:
            raise PayloadInvalidError(f"expected {SET_GRAMMAR}, got {item!r}.")
        _lay(body, model, path.strip().split("."), raw.strip(), path.strip())
    return body


def _sets_to_patch(sets: list[str]) -> EditDraftPatch:
    """Shape only: every bound is the patch model's and ``plan_draft_patch``'s, as for the browser."""
    return PayloadInvalidError.validating(EditDraftPatch, _set_body(EditDraftPatch, sets))


def _set_knobs(sets: list[str]) -> dict[str, Any]:
    if stray := [s.partition("=")[0] for s in sets if not s.startswith(f"{_KNOBS}.")]:
        raise PayloadInvalidError(
            f"--set {', '.join(stray)} confirms a check-in field, which a dataset name "
            f"does not have. On a name: {_KNOBS}.<knob>."
        )
    patch = _sets_to_patch(sets)
    if patch.config is None:
        return {}
    return dict(patch.config.model_dump(mode="json", exclude_unset=True)["optimization"])


def _gap_path(field: str) -> tuple[str, ...]:
    """The patch path `--set` confirms *field* by: a gap names a checklist row (`FieldGap.of`)."""
    row = checklist_field(field)
    assert row is not None, field
    return row.patch_path


def _draft_kept(draft_id: str) -> str:
    return (
        f"Check-in {draft_id} is kept: finish it in the browser, or drop it with "
        f"`promptpotter discard-draft {draft_id}`."
    )


def _said_incomplete(
    refused: OriginIncompleteError, turn: OriginResolution | None, draft_id: str
) -> OriginIncompleteError:
    """The Start's own refusal, with what this terminal can do about it."""
    gaps = refused.gaps
    lines = ["Open fields:", *(f"  - {g.field}: {g.hint}" for g in gaps)]
    if turn is not None and turn.degraded_cause:
        lines += ["", f"The resolver turn came back thin: {turn.degraded_cause}."]
    questions = turn.last_resolution.questions if turn and turn.last_resolution else []
    if questions:
        lines += ["", "The resolver asked:"]
        lines += [f"  - {q.prompt}".rstrip() for q in questions]
    lines += [
        "",
        "Confirm the open fields and re-run:",
        "  promptpotter new <file> "
        + " ".join(f"--set {'.'.join(_gap_path(g.field))}=<value>" for g in gaps),
    ]
    refused.add_note("\n".join(lines))
    refused.add_note(_draft_kept(draft_id))
    return refused


async def _ingest_checkin(args: argparse.Namespace, dispatcher: CommandDispatcher) -> str:
    file_path = Path(args.dataset)

    def _bounded_read() -> bytes:
        # One byte past the cap, which the bytes' own bound refuses (`UploadBytes`).
        with file_path.open("rb") as fh:
            return fh.read(MAX_UPLOAD_BYTES + 1)

    payload = PayloadInvalidError.validating(
        IngestUploadPayload,
        {
            "blob": await asyncio.to_thread(_bounded_read),
            "filename": file_path.name,
            "slug": args.slug,
        },
    )
    # A refusal is `cli/main.py::main`'s to say, in its own words, as every verb's.
    draft = (
        await dispatcher.answer(
            workspace_edits.ingest_upload, CommandCall(payload, door=Door.TERMINAL)
        )
    ).result
    _checkin_line("ingest", f"{draft.n_samples} rows → check-in '{draft.slug}' ({draft.draft_id})")
    return draft.draft_id


async def _hold_checkin(
    args: argparse.Namespace,
    dispatcher: CommandDispatcher,
    draft_id: str,
    patch: EditDraftPatch | None,
) -> HeldRun:
    # Dispatched, as the browser's: a direct draft write records nothing and re-bills every retry.
    if patch is not None:
        await dispatcher.answer(
            draft_editing.edit_draft_campaign,
            CommandCall(
                EditDraftCampaignPayload(draft_id=draft_id, patch=patch), door=Door.TERMINAL
            ),
        )
    last_turn: OriginResolution | None = None
    # A `new <file>` sends the one command an empty Send does: the server buys the turn only where
    # one is owed. One turn: a residual gap surfaces below with --set instructions.
    _checkin_line("origin resolver", "asking for the AI check-in")
    try:
        turn = await dispatcher.answer(
            origin_resolving.resolve_origin,
            CommandCall(ResolveOriginPayload(draft_id=draft_id), door=Door.TERMINAL),
        )
    except ConflictError as refused:
        if refused.code != origin_resolving.RESOLVER_NOT_OWED:
            raise
        _checkin_line(
            "origin resolver", "no turn owed: one already ran, or the prompts are authored"
        )
    else:
        last_turn = turn.result.resolution
    # The Start checks the origin's readiness, once, on its one backend read.
    try:
        launched = await dispatch_start_checkin(
            dispatcher.stores,
            CommandCall(
                StartCheckinPayload(draft_id=draft_id, budget=budget_from_args(args)),
                door=Door.TERMINAL,
            ),
            inline=inline_launch(args),
        )
    except OriginIncompleteError as refused:
        raise _said_incomplete(refused, last_turn, draft_id) from None
    _checkin_line("origin", f"complete — check-in {draft_id} started")
    held = held_run(launched)
    _checkin_line("campaign", f"minted {held.session.campaign_id} from check-in {draft_id}")
    return held


def _named_mint(args: argparse.Namespace, dispatcher: CommandDispatcher) -> MintCampaignPayload:
    declared = None
    budget = budget_from_args(args)
    if args.config:
        path = Path(args.config)
        declared = campaign_file_block(path)
    dataset_name = args.dataset or args.dataset_name
    if not dataset_name:
        refused = PayloadInvalidError(
            "`new` requires a dataset name. Pass it as a positional "
            "(`new justlogic/d234`) or via `--dataset-name <name>`."
        )
        refused.add_note(no_dataset_hint(dispatcher.stores))
        raise refused
    knobs = _set_knobs(args.sets)
    task_text = (
        Path(args.task_file).read_text(encoding="utf-8") if args.task_file else args.task_text
    )

    return PayloadInvalidError.validating(
        MintCampaignPayload,
        {
            "dataset_name": dataset_name,
            "optimization": knobs or None,
            "campaign_config": declared,
            "task_text": task_text,
            "budget": budget,
        },
    )


def cmd_new(args: argparse.Namespace) -> Coroutine[Any, Any, CommandResult]:
    prepare_launch()
    return _new(args)


async def _new(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    if not (args.dataset and await asyncio.to_thread(Path(args.dataset).is_file)):
        dispatcher = CommandDispatcher(local_stores(tenant=args.tenant), inline=inline_launch(args))
        return await mint_and_run(dispatcher, _named_mint(args, dispatcher))
    sets = args.sets
    patch = _sets_to_patch(sets) if sets else None
    dispatcher = CommandDispatcher(local_stores(tenant=args.tenant))
    draft_id = await _ingest_checkin(args, dispatcher)
    try:
        held = await _hold_checkin(args, dispatcher, draft_id, patch)
    except PotterError as exc:
        exc.add_note(_draft_kept(draft_id))
        raise
    return await _run_origin(held)


@contextlib.contextmanager
def _named_dataset(dispatcher: CommandDispatcher) -> Iterator[None]:
    """The refusal's own words, then what this account can start: a rewrite would drop its reason."""
    try:
        yield
    except DatasetAccessError as refused:
        refused.add_note(no_dataset_hint(dispatcher.stores))
        raise


async def mint_and_run(dispatcher: CommandDispatcher, mint: MintCampaignPayload) -> CommandResult:
    with _named_dataset(dispatcher):
        launched = (await dispatcher.dispatch(CommandCall(mint, door=Door.TERMINAL))).result
    held = held_run(launched)
    _checkin_line("campaign", f"minted {held.session.campaign_id}")
    return await _run_origin(held)


def _arm_request(raw: str) -> dict[str, Any]:
    arm_key, sep, spec = raw.partition("=")
    if not sep or not spec:
        raise PayloadInvalidError(f"--arm takes {ARM_GRAMMAR}, got {raw!r}")
    # A comma opens a knob only where `name=` follows it: a value may hold one.
    optimizer, *knobs = _KNOB_COMMA.split(spec)
    return {
        "arm_key": arm_key,
        "optimization": _set_body(OptimizationOverrides, [f"optimizer={optimizer}", *knobs]),
    }


def _head_to_head_payload(args: argparse.Namespace) -> MintHeadToHeadPayload:
    return PayloadInvalidError.validating(
        MintHeadToHeadPayload,
        {
            "dataset_name": args.dataset,
            "head_to_head": {
                "head_to_head_id": args.head_to_head,
                "arms": [_arm_request(raw) for raw in args.arms],
                "budget": budget_from_args(args),
            },
        },
    )


def _spelled(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _arm_spec(arm: ArmRequest) -> str:
    knobs = arm.optimization
    sets = [
        *(
            f"{field}={getattr(knobs, field)}"
            for field in sorted(knobs.model_fields_set - {"optimizer", "nodes"})
        ),
        *(
            f"nodes.{node}.config.{knob}={_spelled(value)}"
            for node, overlay in knobs.nodes.items()
            for knob, value in overlay.config.items()
        ),
    ]
    return ",".join([f"{arm.arm_key}={knobs.optimizer}", *sets])


def head_to_head_command(mint: MintHeadToHeadPayload) -> str:
    """The `head-to-head` that dispatches *mint*, read back through the verb's own parser first."""
    request = mint.head_to_head
    words = ["head-to-head", mint.dataset_name, "--head-to-head", request.head_to_head_id]
    for arm in request.arms:
        words += ["--arm", _arm_spec(arm)]
    for axis in sorted(request.budget.named):
        bound = getattr(request.budget, axis)
        flag = CommandPayload.cli_flag(f"budget.{axis}")
        words += [flag, "none" if bound is None else _spelled(bound)]
    back = _head_to_head_payload(build_parser(VERBS).parse_args(words)).head_to_head

    def declared(r: HeadToHeadRequest) -> list[tuple[str, dict[str, Any]]]:
        return [(a.arm_key, r.declared_optimization(a)) for a in r.arms]

    if declared(back) != declared(request):
        raise PotterError(f"no `head-to-head` command line spells {mint.model_dump()}")
    return shlex.join(["python", "-m", "promptpotter", *words])


def cmd_head_to_head(args: argparse.Namespace) -> Coroutine[Any, Any, CommandResult]:
    prepare_launch()
    return _head_to_head(args)


async def _head_to_head(args: argparse.Namespace) -> CommandResult:
    """Each arm runs in its own process, as a server launch does; this waits on them all."""
    setup_logging(style="full" if args.verbose else "cli")
    stores = local_stores(tenant=args.tenant)
    payload = _head_to_head_payload(args)
    dispatcher = CommandDispatcher(stores)
    with _named_dataset(dispatcher):
        outcome = await dispatcher.answer(
            launching.mint_head_to_head, CommandCall(payload, door=Door.TERMINAL)
        )
    answer = outcome.answer
    assert isinstance(answer, LaunchAnswer), "a launch kind answers its LaunchAnswer"
    arms = [
        (arm.arm_key, launch.hop)
        for arm, launch in zip(payload.head_to_head.arms, answer.launches, strict=True)
        if launch.hop is not None
    ]
    for arm_key, hop in arms:
        cycle_dir = stores.campaigns.cycle_dir(hop).absolute()
        _checkin_line(f"arm {arm_key}", f"{hop.campaign_id} → {cycle_dir}")
    logger.info("Each arm writes readout.log in its cycle; Ctrl+C pauses every arm.")
    pauses = [
        PauseCyclePayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, reason="Ctrl+C")
        for _, hop in arms
    ]
    with ctrl_c_pauses(dispatcher, pauses):
        await asyncio.gather(*outcome.result.exits)
    ended = [
        f"  {arm_key}: {hop.campaign_id} — {dispatcher.stores.run_state(hop).run_phase.value}"
        for arm_key, hop in arms
    ]
    subjects = " ".join(f"--subject campaign:{hop.campaign_id}" for _, hop in arms)
    return CommandResult(
        data=answer.model_dump(mode="json"),
        human="\n".join(
            [
                f"Head-to-head {payload.head_to_head.head_to_head_id}:",
                *ended,
                "",
                f"Read it: python -m promptpotter evidence {subjects}",
            ]
        ),
    )


async def _run_origin(held: HeldRun) -> CommandResult:
    session = held.session
    dataset_name = session.dataset_name or "?"
    _checkin_line(
        "backend",
        backend_reach_line(session.backend_client.connector.name, session.backend_client.base_url),
    )
    _checkin_line("dataset", f"{dataset_name} ({len(session.bank.samples)} queries)")
    _checkin_line("pipeline", pipeline_summary(session, session.pipeline_params))

    logger.info("Campaign: %s", session.store.campaigns.campaign_root_dir(session.campaign_id))

    _checkin_line("origin", "launching origin scoring")
    result = await run_inline(held)
    return cycle_result_command(session, result)


def _run_mode(args: argparse.Namespace) -> RunMode:
    """`--from`'s bound is `RunMode.from_round`'s, refused as every door refuses a payload."""
    return PayloadInvalidError.validating(
        RunMode, {"from_round": args.resume_from_round, "on_divergence": args.on_divergence}
    )


def _payload(args: argparse.Namespace, hop: CycleHop) -> StartRunPayload | ForkCyclePayload:
    """The whole payload the flags spell: a `--rewind` or a `--steer` cuts a fork, refused as
    every door refuses one."""
    budget, mode = budget_from_args(args), _run_mode(args)
    if args.rewind_to_round is None and not args.steer:
        return StartRunPayload(
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            budget=budget,
            step_rounds=args.step_rounds,
            mode=mode,
        )
    if args.step_rounds is not None:
        raise PayloadInvalidError("--step-rounds steps a cycle's own run; a fork's run takes none.")
    return PayloadInvalidError.validating(
        ForkCyclePayload,
        {
            "campaign_id": hop.campaign_id,
            "cycle_id": hop.cycle_id,
            "round": args.rewind_to_round or 0,
            "keep_rounds": args.rewind_to_round is not None,
            "reason": args.rewind_reason.strip(),
            "seed": ForkSeed(pipeline_overlay=_steer_overlay(args.steer or [])),
            "budget": budget,
            "mode": mode,
        },
    )


async def _dispatch_held(
    args: argparse.Namespace, ctx: SessionCtx, payload: StartRunPayload | ForkCyclePayload
) -> HeldRun:
    """*ctx* follows the cycle the command answers (a fork) and stays put when the hold is refused."""
    outcome = await CommandDispatcher(ctx.store, inline=inline_launch(args)).dispatch(
        CommandCall(payload, door=Door.TERMINAL)
    )
    held = held_run(outcome.result)
    ctx.cycle_id = held.session.hop.cycle_id
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


def _confirm_steer(ctx: SessionCtx, overlay: dict[str, dict[str, object]]) -> None:
    """C0 is INHERITED; a steer to a non-PERMITTED model needs ``campaign.babysit``, the dispatcher's gate: this only warns."""
    preview = fork_preview(ctx.store, ctx.campaign_id, overlay)
    if preview.steer == "clean":
        return
    steered = ", ".join(
        f"{node}.{param}={value!r}"
        for node, cfg in sorted(overlay.items())
        for param, value in sorted(cfg.items())
    )
    print()
    print(f"⚠  Steering {steered} — NOT permitted by")
    print(f"   the origin: {preview.permitted or '{} (nothing sanctioned)'}.")
    print("   This branch will be marked babysat (grade C); the origin's C0 is inherited.")
    print()
    # The cap is the authorization; the prompt is a courtesy, and a non-TTY run (None) proceeds on the cap.
    if confirm_tty("Proceed with the babysit steer?", default_no=True) is False:
        raise SystemExit("Cancelled. The active campaign is unchanged.")


async def _pivot_to_fresh(
    args: argparse.Namespace, ctx: SessionCtx, drift: ConfigDriftError
) -> CommandResult:
    (act,) = drift.next_acts
    answer = confirm_tty(
        f"Start a fresh campaign on `{act.value}` instead?", default_no=True, context=str(drift)
    )
    if answer is None:
        raise drift
    if not answer:
        raise SystemExit("Cancelled. Revert the config edits and retry `resume`.")
    fresh = {act.field: act.value, "budget": budget_from_args(args)}
    return await mint_and_run(
        CommandDispatcher(ctx.store, inline=inline_launch(args)),
        MintCampaignPayload.model_validate(fresh),
    )


def cmd_resume(args: argparse.Namespace) -> Coroutine[Any, Any, CommandResult]:
    prepare_launch()
    return _resume(args)


async def _resume(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = local_stores(tenant=args.tenant)
    ctx = load_session(stores, cycle_target(stores, args.campaign, args.cycle))

    payload = _payload(args, ctx.hop)
    if isinstance(payload, ForkCyclePayload) and payload.seed.pipeline_overlay:
        _confirm_steer(ctx, payload.seed.pipeline_overlay)
    try:
        held = await _dispatch_held(args, ctx, payload)
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
    cycle_result = await run_inline(held)
    return cycle_result_command(session, cycle_result)
