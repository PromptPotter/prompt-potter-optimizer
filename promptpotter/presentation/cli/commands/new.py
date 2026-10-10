from __future__ import annotations

import argparse
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import yaml
from pydantic import ValidationError

from promptpotter.application.campaign_config import load_campaign_config as _load_cfg
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.draft_editing import dispatch_draft_patch
from promptpotter.application.commands.launching import dispatch_start_checkin
from promptpotter.application.commands.origin_resolving import dispatch_origin_resolution
from promptpotter.application.commands.payloads import (
    EditDraftCampaignPayload,
    MintCampaignPayload,
    ResolveOriginPayload,
    StartCheckinPayload,
)
from promptpotter.application.datasets.authored import read_campaign_config_file
from promptpotter.application.datasets.csv_ingest import IngestError
from promptpotter.application.datasets.draft_campaign import (
    SETTABLE_SCALARS,
    EditDraftPatch,
    OptimizationOverrides,
    load_checkin_draft,
)
from promptpotter.application.datasets.ingest import SlugTakenError, ingest_draft
from promptpotter.application.datasets.origin_readiness import origin_readiness
from promptpotter.application.jobs.launcher.mint_and_start import dataset_campaign_config
from promptpotter.application.runner.entry import RunMode
from promptpotter.config.logging import setup_logging
from promptpotter.domain.campaign import ArmRequest
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    backend_type_of_dataset,
    readable_dataset_dir,
)
from promptpotter.presentation.cli.commands.launch import (
    backend_reach_line,
    backend_unreachable_result,
    cycle_result_command,
    held_run,
    inline_launch,
    pipeline_summary,
    prepare_launch,
    run_inline,
)
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores
from promptpotter.presentation.cli.parsers import launch_limits_from_args
from promptpotter.presentation.cli.session import no_dataset_hint
from promptpotter.presentation.terminal.startup_checklist import checkin_line
from promptpotter.shared.errors import PotterError

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence

    from promptpotter.application.datasets.draft_campaign import DraftCampaign
    from promptpotter.application.datasets.origin_readiness import FieldGap, OriginLastResolution
    from promptpotter.application.jobs.launcher.run_job import HeldRun
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger("promptpotter.presentation.cli")


_SET_ALIAS: dict[str, str] = {
    "task_description": "raw_task_description",
    "column.query": "column_query",
    "column.ground_truth": "column_ground_truth",
}

_NODES = "nodes"
_SET_KNOBS: frozenset[str] = frozenset(OptimizationOverrides.model_fields) - {_NODES}

_SET_FIELDS: frozenset[str] = (SETTABLE_SCALARS - set(_SET_ALIAS.values())) | set(_SET_ALIAS)


def _settable() -> str:
    return ", ".join([*sorted(_SET_FIELDS | _SET_KNOBS), f"{_NODES}.<node>.<knob>"])


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
        elif field.startswith(f"{_NODES}.") and field.count(".") == 2:
            _, node, knob = field.split(".")
            config = knobs.setdefault(_NODES, {}).setdefault(node, {"config": {}})["config"]
            config[knob] = yaml.safe_load(raw)
        elif field in _SET_FIELDS:
            patch_raw[_SET_ALIAS.get(field, field)] = raw
        else:
            raise SystemExit(
                f"ERROR: --set field {field!r} is not settable. One of: {_settable()}."
            )
    if knobs:
        patch_raw["optimization_overrides"] = knobs
    try:
        return EditDraftPatch.model_validate(patch_raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        name = str(first.get("loc", ("?",))[0])
        raise SystemExit(
            f"ERROR: --set {name} rejected: {first.get('msg', 'invalid value')}"
        ) from None


def _set_knobs(sets: list[str]) -> dict[str, Any]:
    if not sets:
        return {}
    patch = _sets_to_patch(sets)
    spelled = {model: cli for cli, model in _SET_ALIAS.items()}
    fields = patch.model_fields_set - {"optimization_overrides"}
    if stray := sorted(spelled.get(f, f) for f in fields):
        raise SystemExit(
            f"ERROR: --set {', '.join(stray)} confirms a check-in field, which a dataset name "
            f"does not have. On a name: {', '.join(sorted(_SET_KNOBS))}, {_NODES}.<node>.<knob>."
        )
    knobs = patch.optimization_overrides
    assert knobs is not None
    return knobs


def _reload_draft(stores: Stores, campaign_id: str) -> DraftCampaign:
    draft = load_checkin_draft(stores, campaign_id)
    assert draft is not None
    return draft


def _raise_incomplete(gaps: Sequence[FieldGap], turn: OriginLastResolution | None) -> NoReturn:
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
    ]
    raise SystemExit("\n".join(lines))


async def _ingest_checkin(args: argparse.Namespace) -> str:
    """A residual gap exits non-zero but the campaign SURVIVES, for a later ``--set`` + ``resume``."""
    file_path = Path(args.dataset)
    stores = open_stores(args)

    try:
        draft = await ingest_draft(
            stores=stores,
            blob=file_path.read_bytes(),
            filename=file_path.name,
            slug=args.slug,
            **({"backend_url": args.backend_url} if args.backend_url else {}),
        )
    except IngestError as exc:
        raise SystemExit(f"ERROR: could not parse {file_path.name}: {exc.message}") from None
    except ValueError as exc:
        raise SystemExit(f"ERROR: bad slug — {exc}") from None
    except SlugTakenError as exc:
        raise SystemExit(
            f"ERROR: slug '{exc.slug}' already exists. Try --slug {exc.suggested}."
        ) from None

    campaign_id = draft.draft_id
    checkin_line("ingest", f"{draft.n_samples} rows → check-in '{draft.slug}' ({campaign_id})")

    # Dispatched, as the browser's: a direct draft write records nothing and re-bills every retry.
    try:
        if args.sets:
            await dispatch_draft_patch(
                stores,
                CommandCall(
                    EditDraftCampaignPayload(draft_id=campaign_id, patch=_sets_to_patch(args.sets)),
                    uuid.uuid4().hex,
                ),
            )
            draft = _reload_draft(stores, campaign_id)

        last_turn: OriginLastResolution | None = None
        if not origin_readiness(draft).complete:
            checkin_line("origin resolver", "running AI check-in")
            # One turn: a residual gap surfaces below with --set instructions, not more LLM turns.
            turn = await dispatch_origin_resolution(
                stores,
                CommandCall(ResolveOriginPayload(draft_id=campaign_id), uuid.uuid4().hex),
            )
            last_turn = turn.resolution.last_resolution
            draft = _reload_draft(stores, campaign_id)
    except PotterError as exc:
        raise SystemExit(f"ERROR: {exc}") from None

    readiness = origin_readiness(draft)
    if not readiness.complete:
        _raise_incomplete(readiness.gaps, last_turn)

    checkin_line("origin", f"complete — check-in '{draft.slug}' ready")
    return campaign_id


async def _hold_ingested_checkin(args: argparse.Namespace) -> HeldRun:
    campaign_id = await _ingest_checkin(args)
    stores = open_stores(args)
    launched = await dispatch_start_checkin(
        stores,
        CommandCall(
            StartCheckinPayload(
                campaign_id=campaign_id,
                diag=args.diag,
                backend_url=args.backend_url,
                backend_id=args.backend_id,
                **launch_limits_from_args(args).model_dump(),
            ),
            uuid.uuid4().hex,
        ),
        inline=inline_launch(args),
    )
    checkin_line("campaign", f"started check-in {campaign_id}")
    return held_run(launched)


def _arm_request(raw: str | None) -> ArmRequest | None:
    if raw is None:
        return None
    head_to_head_id, sep, arm_key = raw.partition(":")
    if not sep:
        raise SystemExit(f"ERROR: --arm takes HEAD_TO_HEAD:KEY, got {raw!r}")
    try:
        return ArmRequest(head_to_head_id=head_to_head_id, arm_key=arm_key)
    except ValidationError as exc:
        raise SystemExit(f"ERROR: --arm {raw!r}: {exc}") from exc


async def _hold_named_mint(args: argparse.Namespace) -> HeldRun:
    declared = None
    if args.config:
        file_config = read_campaign_config_file(Path(args.config))
        declared = _load_cfg(file_config)
    dataset_name = (
        args.dataset or args.dataset_name or (declared.dataset_name if declared else None)
    )
    if not dataset_name:
        raise SystemExit(
            "ERROR: `new` requires a dataset name. Pass it as a positional "
            "(`new aime`), via `--dataset-name <name>`, or via a `--config` "
            "that names one.\n\n" + no_dataset_hint()
        )
    stores = open_stores(args)
    knobs = _set_knobs(args.sets)
    try:
        dataset_campaign_config(
            readable_dataset_dir(stores, dataset_name), optimization=knobs, declared=declared
        )
    except DatasetAccessError:
        raise SystemExit(
            f"ERROR: no dataset named {dataset_name!r}.\n\n" + no_dataset_hint()
        ) from None
    except PotterError as exc:
        raise SystemExit(f"ERROR: --set rejected: {exc}") from None
    arm = _arm_request(args.arm)
    task_text = (
        Path(args.task_file).read_text(encoding="utf-8") if args.task_file else args.task_text
    )

    try:
        mint = MintCampaignPayload(
            dataset_name=dataset_name,
            optimization=OptimizationOverrides.model_validate(knobs) if knobs else None,
            arm=arm,
            diag=args.diag,
            campaign_config=declared,
            task_text=task_text,
            backend_url=args.backend_url,
            backend_id=args.backend_id,
            **launch_limits_from_args(args).model_dump(),
        )
    except ValidationError as exc:
        raise SystemExit(f"ERROR: {exc.errors()[0].get('msg', exc)}") from None
    outcome = await CommandDispatcher(
        stores, inline=inline_launch(args)
    ).dispatch_workspace_command(CommandCall(mint, uuid.uuid4().hex))
    held = held_run(outcome.result)
    checkin_line("campaign", f"minted {held.session.campaign_id}")
    return held


def cmd_new(args: argparse.Namespace) -> Coroutine[Any, Any, CommandResult]:
    prepare_launch(args)
    return mint_and_run(args)


async def mint_and_run(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    try:
        if args.dataset and Path(args.dataset).is_file():
            if args.arm is not None:
                raise SystemExit(
                    "ERROR: --arm mints a committed dataset's campaign, not a raw file"
                )
            held = await _hold_ingested_checkin(args)
        else:
            held = await _hold_named_mint(args)
    except BackendUnreachableError as exc:
        return backend_unreachable_result(exc)

    session = held.session
    dataset_name = session.dataset_name or "?"
    backend_type = backend_type_of_dataset(session.store, dataset_name)
    checkin_line("backend", backend_reach_line(backend_type, session.backend_client.base_url))
    checkin_line("dataset", f"{dataset_name} ({len(session.samples)} queries)")
    checkin_line("pipeline", pipeline_summary(session, session.pipeline_params))

    logger.info("Campaign: %s", session.store.campaigns.campaign_root_dir(session.campaign_id))

    checkin_line("origin", "launching origin scoring")
    result = await run_inline(held, mode=RunMode(diag=args.diag))
    return cycle_result_command(session, result)


__all__ = ["cmd_new", "mint_and_run"]
