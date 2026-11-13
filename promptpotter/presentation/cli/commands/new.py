from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, get_args, get_origin

import yaml
from pydantic import BaseModel

from promptpotter.application.commands import (
    draft_editing,
    launching,
    origin_resolving,
    workspace_edits,
)
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.launching import dispatch_start_checkin
from promptpotter.application.datasets.authored import (
    campaign_file_block,
    validated_campaign_config,
)
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.loaders import no_dataset_hint
from promptpotter.application.datasets.origin_readiness import checklist_field, origin_readiness
from promptpotter.application.jobs.launcher.mint_and_start import OriginIncompleteError
from promptpotter.application.pipeline_resolve import draft_pipeline, draft_served
from promptpotter.config.logging import setup_logging
from promptpotter.domain.command_kinds import (
    ArmRequest,
    EditDraftCampaignPayload,
    HeadToHeadRequest,
    IngestUploadPayload,
    MintCampaignPayload,
    MintHeadToHeadPayload,
    PauseCyclePayload,
    ResolveOriginPayload,
    StartCheckinPayload,
)
from promptpotter.domain.draft_edit import EditDraftPatch, OptimizationOverrides
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import LaunchAnswer
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import MAX_UPLOAD_BYTES
from promptpotter.presentation.cli.commands.launch import (
    backend_reach_line,
    ctrl_c_pauses,
    cycle_result_command,
    held_run,
    inline_launch,
    pipeline_summary,
    prepare_launch,
    run_inline,
)
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.verbs import ARM_GRAMMAR, SET_GRAMMAR, VERBS
from promptpotter.presentation.cli.commands.workspace import open_stores
from promptpotter.presentation.cli.parsers import budget_from_args, build_parser
from promptpotter.shared.errors import (
    ConflictError,
    DatasetAccessError,
    PayloadInvalidError,
    PotterError,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence

    from promptpotter.application.datasets.origin_readiness import FieldGap
    from promptpotter.application.jobs.launcher.launch import Inline
    from promptpotter.application.jobs.launcher.run_job import HeldRun
    from promptpotter.domain.checkin_draft import OriginResolution
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger("promptpotter.presentation.cli")


_KNOB_COMMA = re.compile(r",(?=[\w.]+=)")
# A dataset name confirms no check-in field: its `--set` lays the campaign knobs alone.
_KNOBS = "config.optimization"


def _checkin_line(step: str, detail: str) -> None:
    logger.info("✓ %-13s — %s", step, detail)


def _value(raw: str) -> Any:
    """YAML, except a word YAML 1.1 reads as a bool stays the word: `reasoning_effort=off`."""
    value = yaml.safe_load(raw)
    return raw if isinstance(value, bool) and raw.lower() not in ("true", "false") else value


def _members(annotation: object) -> tuple[Any, ...]:
    return tuple(m for m in (get_args(annotation) or (annotation,)) if m is not type(None))


def _laid(into: object, annotation: object, rest: list[str], raw: str, path: str) -> Any:
    """*raw* laid at *rest* under a field typed *annotation*: a model by its fields, a mapping by
    any key, ``Any`` as it comes; at the leaf a field that takes a string keeps the word."""
    members = _members(annotation) if get_origin(annotation) is not dict else (annotation,)
    if not rest:
        return raw if str in members else _value(raw)
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


def _raise_incomplete(
    gaps: Sequence[FieldGap], turn: OriginResolution | None, draft_id: str
) -> NoReturn:
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
    refused = OriginIncompleteError(tuple(gaps))
    refused.add_note("\n".join(lines))
    refused.add_note(_draft_kept(draft_id))
    raise refused


async def _ingest_checkin(args: argparse.Namespace, stores: Stores) -> str:
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
    # A refusal is `campaign_runner.main`'s to say, in its own words, as every verb's.
    draft = (
        await CommandDispatcher(stores).answer(workspace_edits.ingest_upload, CommandCall(payload))
    ).result
    _checkin_line("ingest", f"{draft.n_samples} rows → check-in '{draft.slug}' ({draft.draft_id})")
    return draft.draft_id


async def _hold_checkin(
    args: argparse.Namespace, stores: Stores, draft_id: str, patch: EditDraftPatch | None
) -> HeldRun:
    # Dispatched, as the browser's: a direct draft write records nothing and re-bills every retry.
    if patch is not None:
        await CommandDispatcher(stores).answer(
            draft_editing.edit_draft_campaign,
            CommandCall(EditDraftCampaignPayload(draft_id=draft_id, patch=patch)),
        )
    last_turn: OriginResolution | None = None
    # A `new <file>` sends the one command an empty Send does: the server buys the turn only where
    # one is owed. One turn: a residual gap surfaces below with --set instructions.
    _checkin_line("origin resolver", "asking for the AI check-in")
    try:
        turn = await CommandDispatcher(stores).answer(
            origin_resolving.resolve_origin, CommandCall(ResolveOriginPayload(draft_id=draft_id))
        )
    except ConflictError as refused:
        if refused.code != origin_resolving.RESOLVER_NOT_OWED:
            raise
        _checkin_line(
            "origin resolver", "no turn owed: one already ran, or the prompts are authored"
        )
    else:
        last_turn = turn.result.resolution
    draft = owned_draft(stores, draft_id)

    readiness = origin_readiness(
        draft, draft_pipeline(stores, draft, await draft_served(stores, draft))
    )
    if not readiness.complete:
        _raise_incomplete(readiness.gaps, last_turn, draft_id)

    _checkin_line("origin", f"complete — check-in '{draft.slug}' ready")
    launched = await dispatch_start_checkin(
        stores,
        CommandCall(StartCheckinPayload(draft_id=draft_id, budget=budget_from_args(args))),
        inline=inline_launch(args),
    )
    held = held_run(launched)
    _checkin_line("campaign", f"minted {held.session.campaign_id} from check-in {draft_id}")
    return held


def _named_mint(args: argparse.Namespace, stores: Stores) -> MintCampaignPayload:
    declared = None
    budget = budget_from_args(args)
    if args.config:
        path = Path(args.config)
        declared = validated_campaign_config(campaign_file_block(path), path=path).model_dump(
            mode="json", exclude_unset=True
        )
        # A file's budget bounds this launch, under the `--max-*` flags: the payload's one door.
        filed = declared.get("optimization", {}).pop("budget", {})
        budget = Budget.model_validate(filed).over(budget)
    dataset_name = args.dataset or args.dataset_name
    if not dataset_name:
        refused = PayloadInvalidError(
            "`new` requires a dataset name. Pass it as a positional "
            "(`new justlogic/d234`) or via `--dataset-name <name>`."
        )
        refused.add_note(no_dataset_hint(stores))
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
        stores = open_stores(args)
        return await mint_and_run(stores, _named_mint(args, stores), inline_launch(args))
    sets = args.sets
    patch = _sets_to_patch(sets) if sets else None
    stores = open_stores(args)
    draft_id = await _ingest_checkin(args, stores)
    try:
        held = await _hold_checkin(args, stores, draft_id, patch)
    except PotterError as exc:
        exc.add_note(_draft_kept(draft_id))
        raise
    return await _run_origin(held)


@contextlib.contextmanager
def _named_dataset(stores: Stores) -> Iterator[None]:
    """The refusal's own words, then what this account can start: a rewrite would drop its reason."""
    try:
        yield
    except DatasetAccessError as refused:
        refused.add_note(no_dataset_hint(stores))
        raise


async def mint_and_run(stores: Stores, mint: MintCampaignPayload, inline: Inline) -> CommandResult:
    dispatcher = CommandDispatcher(stores, inline=inline)
    with _named_dataset(stores):
        launched = (await dispatcher.dispatch(CommandCall(mint))).result
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
    return value if isinstance(value, str) and yaml.safe_load(value) == value else json.dumps(value)


_BUDGET_FLAGS = {
    "usd": "--max-usd",
    "tokens": "--max-tokens",
    "rounds": "--max-rounds",
    "wall_clock_s": "--max-wall-clock",
}


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
        words += [_BUDGET_FLAGS[axis], "none" if bound is None else _spelled(bound)]
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
    stores = open_stores(args)
    payload = _head_to_head_payload(args)
    dispatcher = CommandDispatcher(stores)
    with _named_dataset(stores):
        outcome = await dispatcher.answer(launching.mint_head_to_head, CommandCall(payload))
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
    with ctrl_c_pauses(stores, pauses):
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
    result = await run_inline(held, mode=RunMode())
    return cycle_result_command(session, result)
