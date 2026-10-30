from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import yaml

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.draft_editing import dispatch_draft_patch
from promptpotter.application.commands.launching import dispatch_start_checkin
from promptpotter.application.commands.origin_resolving import dispatch_origin_resolution
from promptpotter.application.datasets.authored import (
    campaign_file_block,
    validated_campaign_config,
)
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.ingest import SlugTakenError, ingest_draft
from promptpotter.application.datasets.loaders import no_dataset_hint
from promptpotter.application.datasets.origin_readiness import origin_readiness
from promptpotter.application.datasets.origin_resolve import FINDING_PATCH_KEYS
from promptpotter.application.datasets.tabular_ingest import IngestError
from promptpotter.config.logging import setup_logging
from promptpotter.domain.command_kinds import (
    ArmRequest,
    EditDraftCampaignPayload,
    HeadToHeadRequest,
    MintCampaignPayload,
    MintHeadToHeadPayload,
    PauseCyclePayload,
    ResolveOriginPayload,
    StartCheckinPayload,
)
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.draft_edit import SETTABLE_SCALARS, EditDraftPatch, OptimizationOverrides
from promptpotter.domain.run_mode import RunMode
from promptpotter.presentation.cli.commands.launch import (
    backend_reach_line,
    backend_unreachable_result,
    ctrl_c_pauses,
    cycle_result_command,
    held_run,
    inline_launch,
    pipeline_summary,
    prepare_launch,
    run_inline,
)
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.verbs import VERBS
from promptpotter.presentation.cli.commands.workspace import open_stores
from promptpotter.presentation.cli.parsers import budget_from_args, build_parser
from promptpotter.shared.errors import DatasetAccessError, PayloadInvalidError, PotterError

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence

    from promptpotter.application.datasets.origin_readiness import FieldGap, OriginLastResolution
    from promptpotter.application.jobs.launcher.head_to_head import HeadToHeadLaunched
    from promptpotter.application.jobs.launcher.launch import Inline
    from promptpotter.application.jobs.launcher.run_job import HeldRun
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger("promptpotter.presentation.cli")


_NODES = "nodes"
_BUDGET = "budget"
_SET_KNOBS: frozenset[str] = frozenset(OptimizationOverrides.model_fields) - {_NODES, _BUDGET}

_SET_FIELDS: frozenset[str] = (SETTABLE_SCALARS - set(FINDING_PATCH_KEYS.values())) | set(
    FINDING_PATCH_KEYS
)
_KNOB_COMMA = re.compile(r",(?=[\w.]+=)")


def _settable() -> str:
    return ", ".join(
        [*sorted(_SET_FIELDS | _SET_KNOBS), f"{_BUDGET}.<axis>", f"{_NODES}.<node>.<knob>"]
    )


def _sets_of(args: argparse.Namespace) -> list[str]:
    halt = args.halt_at_accuracy
    return [*args.sets, *([] if halt is None else [f"halt_at_accuracy={halt}"])]


def _checkin_line(step: str, detail: str) -> None:
    logger.info("✓ %-13s — %s", step, detail)


def _sets_to_patch(sets: list[str]) -> EditDraftPatch:
    """Shape only: every bound is the patch model's and ``plan_draft_patch``'s, as for the browser."""
    patch_raw: dict[str, Any] = {}
    knobs: dict[str, Any] = {}
    for item in sets:
        if "=" not in item:
            raise SystemExit(f"ERROR: --set expects FIELD=VALUE, got {item!r}.")
        field, raw = (part.strip() for part in item.split("=", 1))
        if field in _SET_KNOBS:
            knobs[field] = raw
        elif field.startswith(f"{_BUDGET}.") and field.count(".") == 1:
            axis = field.split(".")[1]
            knobs.setdefault(_BUDGET, {})[axis] = (
                None if raw.lower() == "none" else yaml.safe_load(raw)
            )
        elif field.startswith(f"{_NODES}.") and field.count(".") == 2:
            _, node, knob = field.split(".")
            config = knobs.setdefault(_NODES, {}).setdefault(node, {"config": {}})["config"]
            config[knob] = yaml.safe_load(raw)
        elif field in _SET_FIELDS:
            patch_raw[FINDING_PATCH_KEYS.get(field, field)] = raw
        else:
            raise SystemExit(
                f"ERROR: --set field {field!r} is not settable. One of: {_settable()}."
            )
    if knobs:
        patch_raw["optimization_overrides"] = knobs
    return PayloadInvalidError.validating(EditDraftPatch, patch_raw)


def _set_knobs(sets: list[str]) -> dict[str, Any]:
    if not sets:
        return {}
    patch = _sets_to_patch(sets)
    spelled = {model: cli for cli, model in FINDING_PATCH_KEYS.items()}
    fields = patch.model_fields_set - {"optimization_overrides"}
    if stray := sorted(spelled.get(f, f) for f in fields):
        raise SystemExit(
            f"ERROR: --set {', '.join(stray)} confirms a check-in field, which a dataset name "
            f"does not have. On a name: {', '.join(sorted(_SET_KNOBS))}, {_BUDGET}.<axis>, "
            f"{_NODES}.<node>.<knob>."
        )
    knobs = patch.optimization_overrides
    assert knobs is not None
    return knobs


def _draft_kept(draft_id: str) -> str:
    return (
        f"Check-in {draft_id} is kept: finish it in the browser, or drop it with "
        f"`promptpotter discard-draft {draft_id}`."
    )


def _raise_incomplete(
    gaps: Sequence[FieldGap], turn: OriginLastResolution | None, draft_id: str
) -> NoReturn:
    lines = ["ERROR: origin still incomplete — nothing minted.", "", "Open fields:"]
    lines += [f"  - {g.field}: {g.hint}" for g in gaps]
    questions = turn.next_action.questions if turn else []
    if questions:
        lines += ["", "The resolver asked:"]
        lines += [f"  - {q.prompt}".rstrip() for q in questions]
    lines += [
        "",
        "Confirm a field and re-run, e.g.:",
        "  promptpotter new <file> --set task_description='what the prompt does' "
        "--set column.query=<header> --set column.ground_truth=<header>",
        "",
        _draft_kept(draft_id),
    ]
    raise SystemExit("\n".join(lines))


async def _ingest_checkin(args: argparse.Namespace, stores: Stores) -> str:
    file_path = Path(args.dataset)
    try:
        draft = await ingest_draft(
            stores=stores,
            blob=await asyncio.to_thread(file_path.read_bytes),
            filename=file_path.name,
            slug=args.slug,
        )
    except IngestError as exc:
        raise SystemExit(f"ERROR: could not parse {file_path.name}: {exc.message}") from None
    except SlugTakenError as exc:
        raise SystemExit(
            f"ERROR: slug '{exc.slug}' already exists. Try --slug {exc.suggested}."
        ) from None

    _checkin_line("ingest", f"{draft.n_samples} rows → check-in '{draft.slug}' ({draft.draft_id})")
    return draft.draft_id


async def _hold_checkin(
    args: argparse.Namespace, stores: Stores, draft_id: str, patch: EditDraftPatch | None
) -> HeldRun:
    # Dispatched, as the browser's: a direct draft write records nothing and re-bills every retry.
    if patch is not None:
        await dispatch_draft_patch(
            stores,
            CommandCall(EditDraftCampaignPayload(draft_id=draft_id, patch=patch)),
        )
    draft = owned_draft(stores, draft_id)

    last_turn: OriginLastResolution | None = None
    if not origin_readiness(draft).complete:
        _checkin_line("origin resolver", "running AI check-in")
        # One turn: a residual gap surfaces below with --set instructions, not more LLM turns.
        turn = await dispatch_origin_resolution(
            stores, CommandCall(ResolveOriginPayload(draft_id=draft_id))
        )
        last_turn = turn.resolution.last_resolution
        draft = owned_draft(stores, draft_id)

    readiness = origin_readiness(draft)
    if not readiness.complete:
        _raise_incomplete(readiness.gaps, last_turn, draft_id)

    _checkin_line("origin", f"complete — check-in '{draft.slug}' ready")
    launched = await dispatch_start_checkin(
        stores,
        CommandCall(
            StartCheckinPayload(draft_id=draft_id, diag=args.diag, budget=budget_from_args(args))
        ),
        inline=inline_launch(args),
    )
    held = held_run(launched)
    _checkin_line("campaign", f"minted {held.session.campaign_id} from check-in {draft_id}")
    return held


def _named_mint(args: argparse.Namespace, stores: Stores) -> MintCampaignPayload:
    declared = None
    if args.config:
        path = Path(args.config)
        declared = validated_campaign_config(campaign_file_block(path), path=path)
    dataset_name = args.dataset or args.dataset_name
    if not dataset_name:
        raise SystemExit(
            "ERROR: `new` requires a dataset name. Pass it as a positional "
            "(`new justlogic/d234`) or via `--dataset-name <name>`.\n\n" + no_dataset_hint(stores)
        )
    knobs = _set_knobs(_sets_of(args))
    task_text = (
        Path(args.task_file).read_text(encoding="utf-8") if args.task_file else args.task_text
    )

    return PayloadInvalidError.validating(
        MintCampaignPayload,
        {
            "dataset_name": dataset_name,
            "optimization": knobs or None,
            "diag": args.diag,
            "campaign_config": declared,
            "task_text": task_text,
            "budget": budget_from_args(args),
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
    sets = _sets_of(args)
    patch = _sets_to_patch(sets) if sets else None
    stores = open_stores(args)
    draft_id = await _ingest_checkin(args, stores)
    try:
        held = await _hold_checkin(args, stores, draft_id, patch)
    except BackendUnreachableError as exc:
        logger.info(_draft_kept(draft_id))
        return backend_unreachable_result(exc)
    except PotterError as exc:
        raise SystemExit(f"ERROR: {exc}\n\n{_draft_kept(draft_id)}") from None
    return await _run_origin(held, diag=args.diag)


async def mint_and_run(stores: Stores, mint: MintCampaignPayload, inline: Inline) -> CommandResult:
    try:
        outcome = await CommandDispatcher(stores, inline=inline).dispatch(CommandCall(mint))
    except BackendUnreachableError as exc:
        return backend_unreachable_result(exc)
    except DatasetAccessError:
        raise SystemExit(
            f"ERROR: no dataset named {mint.dataset_name!r}.\n\n" + no_dataset_hint(stores)
        ) from None
    except PotterError as exc:
        raise SystemExit(f"ERROR: {exc}") from None
    held = held_run(outcome.result)
    _checkin_line("campaign", f"minted {held.session.campaign_id}")
    return await _run_origin(held, diag=mint.diag)


def _arm_request(raw: str) -> dict[str, Any]:
    arm_key, sep, spec = raw.partition("=")
    if not sep or not spec:
        raise SystemExit(f"ERROR: --arm takes KEY=OPTIMIZER[,KNOB=VALUE…], got {raw!r}")
    # A comma opens a knob only where `name=` follows it: a value may hold one.
    optimizer, *knobs = _KNOB_COMMA.split(spec)
    return {"arm_key": arm_key, "optimization": _set_knobs([f"optimizer={optimizer}", *knobs])}


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
            for field in sorted(_SET_KNOBS & knobs.model_fields_set - {"optimizer"})
        ),
        *(
            f"{_NODES}.{node}.{knob}={_spelled(value)}"
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
        raise SystemExit(f"ERROR: no `head-to-head` command line spells {mint.model_dump()}")
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
    try:
        outcome = await dispatcher.dispatch(CommandCall(payload))
    except BackendUnreachableError as exc:
        return backend_unreachable_result(exc)
    except DatasetAccessError:
        raise SystemExit(
            f"ERROR: no dataset named {payload.dataset_name!r}.\n\n" + no_dataset_hint(stores)
        ) from None
    except PotterError as exc:
        raise SystemExit(f"ERROR: {exc}") from None
    launched: HeadToHeadLaunched = outcome.result
    started = launched.started
    for arm in started.arms:
        cycle_dir = stores.campaigns.cycle_dir(arm.hop).absolute()
        _checkin_line(f"arm {arm.arm_key}", f"{arm.hop.campaign_id} → {cycle_dir}")
    logger.info("Each arm writes readout.log in its cycle; Ctrl+C pauses every arm.")
    pauses = [
        PauseCyclePayload(campaign_id=a.hop.campaign_id, cycle_id=a.hop.cycle_id, reason="Ctrl+C")
        for a in started.arms
    ]
    with ctrl_c_pauses(stores, pauses):
        await asyncio.gather(*launched.exits)
    ended = [
        f"  {arm.arm_key}: {arm.hop.campaign_id} — {dispatcher.run_state(arm.hop).run_phase.value}"
        for arm in started.arms
    ]
    subjects = " ".join(f"--subject campaign:{arm.hop.campaign_id}" for arm in started.arms)
    return CommandResult(
        data=started.model_dump(mode="json"),
        human="\n".join(
            [
                f"Head-to-head {started.head_to_head_id}:",
                *ended,
                "",
                f"Read it: python -m promptpotter evidence {subjects}",
            ]
        ),
    )


async def _run_origin(held: HeldRun, *, diag: bool) -> CommandResult:
    session = held.session
    dataset_name = session.dataset_name or "?"
    _checkin_line(
        "backend", backend_reach_line(session.backend_id, session.backend_client.base_url)
    )
    _checkin_line("dataset", f"{dataset_name} ({len(session.bank.samples)} queries)")
    _checkin_line("pipeline", pipeline_summary(session, session.pipeline_params))

    logger.info("Campaign: %s", session.store.campaigns.campaign_root_dir(session.campaign_id))

    _checkin_line("origin", "launching origin scoring")
    result = await run_inline(held, mode=RunMode(diag=diag))
    return cycle_result_command(session, result)
