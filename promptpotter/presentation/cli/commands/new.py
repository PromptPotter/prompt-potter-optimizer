"""``cmd_new`` — mint a fresh campaign + run the loop from round 0. The declaration (target +
optimizer-prompt hash) rides ``campaign.json`` for resume-time drift, never derives the id."""

from __future__ import annotations

import argparse
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from pydantic import ValidationError

from promptpotter.application.campaign_config import load_campaign_config as _load_cfg
from promptpotter.application.commands.checkin_dispatch import (
    dispatch_draft_patch,
    dispatch_origin_resolution,
    dispatch_start_checkin,
)
from promptpotter.application.commands.dispatcher import CommandCall
from promptpotter.application.commands.payloads import (
    EditDraftCampaignPayload,
    ResolveOriginPayload,
    StartCheckinPayload,
)
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    read_campaign_config_file,
)
from promptpotter.application.datasets.csv_ingest import IngestError
from promptpotter.application.datasets.draft_campaign import (
    OptimizationOverrides,
    load_checkin_draft,
)
from promptpotter.application.datasets.draft_patch import SETTABLE_SCALARS, EditDraftPatch
from promptpotter.application.datasets.ingest import SlugTakenError, ingest_draft
from promptpotter.application.datasets.origin_readiness import origin_readiness
from promptpotter.application.initialization.session import mint_checkin_skeleton
from promptpotter.application.jobs.launcher.admission import probe_backend
from promptpotter.application.jobs.launcher.checkin import prepare_checkin_run
from promptpotter.application.jobs.mint import fresh_campaign_id, prepare_fresh_cycle
from promptpotter.application.optimization.task_context import (
    checkin_call_context,
    committed_task_context,
    decompose_prompt_fields,
)
from promptpotter.application.runner.entry import RunMode
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.dataset_access import backend_type_of_dataset
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    backend_reach_line,
    backend_unreachable_result,
    bind_session_identity,
    cycle_result_command,
    drive_cycle,
    get_verbose,
    identity_from_args,
    init_services_cli,
    pipeline_summary,
)
from promptpotter.presentation.cli.session import load_session, no_dataset_hint
from promptpotter.presentation.terminal.startup_checklist import checkin_line
from promptpotter.shared.errors import PotterError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.datasets.draft_campaign import DraftCampaign
    from promptpotter.application.datasets.origin_readiness import FieldGap
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.presentation.cli.session import SessionCtx

logger = logging.getLogger("promptpotter.presentation.cli")


# --- File-ingest branch: `new <file>` folds onto the durable check-in path ----

# The CLI's ``--set`` vocabulary, DERIVED from the one patch model every ingress edits an origin
# through (``application/datasets/draft_patch.py::EditDraftPatch``) — hand-listing it here would
# make the terminal and the web Advanced block two capabilities wearing one name. Only the two
# SPELLINGS that genuinely differ live here.
_SET_ALIAS: dict[str, str] = {
    # What an operator calls the framing; the model names the RAW text, pre-decomposition.
    "task_description": "raw_task_description",
    # Dotted on a command line, underscored on the wire — one field either way.
    "column.query": "column_query",
    "column.ground_truth": "column_ground_truth",
}

# The campaign-config knobs are NOT patch fields — they ride the patch's one
# ``optimization_overrides`` dict, shallow-merged and validated by ``plan_draft_patch`` exactly as
# the web Advanced block's are. Derived from the model, never hand-listed. ``mechanisms`` is nested
# and has no flat string form.
_SET_KNOBS: frozenset[str] = frozenset(OptimizationOverrides.model_fields) - {"mechanisms"}

# What a `FIELD=VALUE` can name: every scalar the patch declares, under its CLI spelling.
_SET_FIELDS: frozenset[str] = (SETTABLE_SCALARS - set(_SET_ALIAS.values())) | set(_SET_ALIAS)


def _settable() -> str:
    return ", ".join(sorted(_SET_FIELDS | _SET_KNOBS))


def _sets_to_patch(sets: list[str]) -> EditDraftPatch:
    """``--set FIELD=VALUE`` → the patch the web sends. Shape only: every BOUND, the slug-collision
    check and the column-membership check belong to the patch model and ``plan_draft_patch``, so the
    terminal cannot enforce a different rule than the browser does."""
    patch_raw: dict[str, Any] = {}
    knobs: dict[str, Any] = {}
    for item in sets:
        if "=" not in item:
            raise SystemExit(f"ERROR: --set expects FIELD=VALUE, got {item!r}.")
        field, raw = (part.strip() for part in item.split("=", 1))
        if field in _SET_KNOBS:
            knobs[field] = raw
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


def _reload_draft(stores: Stores, campaign_id: str) -> DraftCampaign:
    """The draft as the dispatcher just left it. Every write path persists, so the CLI re-reads
    rather than threading a model the applier already superseded."""

    draft = load_checkin_draft(stores, campaign_id)
    assert draft is not None  # just written by the applier
    return draft


def _raise_incomplete(gaps: Sequence[FieldGap], resolution: dict[str, Any] | None) -> NoReturn:
    """Print the still-open gaps + the resolver's questions and exit non-zero, rather than minting a
    half-specified origin."""
    lines = ["ERROR: origin still incomplete — nothing minted.", "", "Open fields:"]
    lines += [f"  - {g.field}: {g.hint}" for g in gaps]
    questions = (
        ((resolution or {}).get("last_resolution") or {})
        .get("next_action", {})
        .get("questions", [])
    )
    if questions:
        lines += ["", "The resolver asked:"]
        lines += [f"  - {q.get('prompt', '')}".rstrip() for q in questions]
    lines += [
        "",
        "Confirm a field and re-run, e.g.:",
        "  promptpotter new <file> --set task_description='what the prompt does' "
        "--set column.query=<header> --set column.ground_truth=<header>",
    ]
    raise SystemExit("\n".join(lines))


async def _ingest_checkin(args: argparse.Namespace) -> str:
    """Parse → ``--set`` → resolve the origin → the gated check-in campaign id. On a residual gap this
    exits non-zero but the campaign SURVIVES, so a later ``--set`` + ``resume`` completes it."""

    file_path = Path(args.dataset)
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)

    try:
        draft = ingest_draft(
            stores=stores,
            blob=file_path.read_bytes(),
            filename=file_path.name,
            slug=args.slug,
        )
    except IngestError as exc:
        raise SystemExit(f"ERROR: could not parse {file_path.name}: {exc.message}") from None
    except ValueError as exc:
        raise SystemExit(f"ERROR: bad slug — {exc}") from None
    except SlugTakenError as exc:
        raise SystemExit(
            f"ERROR: slug '{exc.slug}' already exists. Try --slug {exc.suggested}."
        ) from None

    campaign_id = draft.draft_id  # the check-in campaign id (draft re-keyed at mint)
    checkin_line("ingest", f"{draft.n_samples} rows → check-in '{draft.slug}' ({campaign_id})")

    # Both mutations ride `CommandDispatcher`, exactly as the browser's do: each lands a
    # `CommandRecord` + ack on the check-in ledger, each is idempotent under its key, and the
    # resolver turn replays from `cache.json` rather than re-spending the LLM call. Writing the
    # draft directly here records an operator's `--set` nowhere and re-bills every retry.
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

        resolution: dict[str, Any] | None = None
        if not origin_readiness(draft).complete:
            checkin_line("origin resolver", "running AI check-in")
            # One turn: code owns the deterministic facts (the answer space) and the operator
            # states the framing up front via --set, so the resolver only reads the columns +
            # authors the prompt. A residual gap surfaces below with the --set instructions
            # rather than spinning more LLM turns.
            turn = await dispatch_origin_resolution(
                stores,
                CommandCall(ResolveOriginPayload(draft_id=campaign_id), uuid.uuid4().hex),
            )
            resolution = turn.get("resolution") or {}
            draft = _reload_draft(stores, campaign_id)
    except PotterError as exc:
        # The dispatcher's own refusals — a taken slug, a column that is not an uploaded header,
        # a knob out of range. One wording for both entry points.
        raise SystemExit(f"ERROR: {exc}") from None

    readiness = origin_readiness(draft)
    if not readiness.complete:
        _raise_incomplete(readiness.gaps, resolution)

    checkin_line("origin", f"complete — check-in '{draft.slug}' ready")
    return campaign_id


async def _ingest_and_prepare_checkin(
    args: argparse.Namespace,
) -> tuple[Session, CampaignConfig, str, str]:
    """The CLI tail of check-in Start. Backend reachability is not preflighted: a check-in is
    durable, so ``resume`` runs it later."""

    campaign_id = await _ingest_checkin(args)
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)

    async def make_session(dataset_name: str) -> Session:
        return await init_services_cli(
            backend_url=args.backend_url,
            backend_id=args.backend_id,
            dataset_name=dataset_name,
            identity=identity_from_args(args),
        )

    prepared = await dispatch_start_checkin(
        stores,
        CommandCall(StartCheckinPayload(campaign_id=campaign_id), uuid.uuid4().hex),
        start=lambda hop, draft: prepare_checkin_run(
            stores, hop=hop, draft=draft, make_session=make_session
        ),
    )
    checkin_line("campaign", f"started check-in {campaign_id}")
    return (
        prepared.session,
        prepared.campaign_config,
        prepared.session.dataset_name or "?",
        prepared.session_id,
    )


async def _commit_task_framing(
    session: Session,
    *,
    task_file: str | None,
    task_text: str | None,
) -> None:
    """``--task-file`` / ``--task-text`` IS a check-in: decompose the operator's context and COMMIT
    it as the dataset's framing. Runs BEFORE the mint because framing renders — the cycle id hashes
    it, so a framing that arrives afterwards names a prompt the id never saw."""

    override = Path(task_file).read_text(encoding="utf-8") if task_file else task_text
    if not override:
        # Absent framing is legitimate, but SILENT absent framing is not: the run scores an
        # unframed prompt and the id honestly says so, which looks identical to a dataset that
        # never had framing. Four shipped benchmarks are in exactly this state.
        if not committed_task_context(session.store, session.dataset_name) and (
            session.dataset_config_dir is not None
            and (Path(session.dataset_config_dir) / "task_description.md").is_file()
        ):
            checkin_line(
                "task check-in",
                f"no committed framing — running unframed. "
                f"`new {session.dataset_name} --task-file "
                f"datasets/{session.dataset_name}/task_description.md` commits it",
            )
        return
    dataset_name = session.dataset_name or ""
    # The check-in's own campaign is where the decomposition bills — the same skeleton the web
    # ingest mints, and the reason one exists: "the origin isn't authored yet, so there is no
    # content hash to address it by".
    _sid, campaign_id, cycle_id = mint_checkin_skeleton(
        session.store,
        slug=dataset_name,
        backend_type=backend_type_of_dataset(session.store, dataset_name),
    )
    result = await decompose_prompt_fields(
        override,
        campaign_id=campaign_id,
        context=checkin_call_context(
            session.store, CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
        ),
    )
    framing = dict(result.get("task_context") or {})
    framing["raw_description"] = override
    session.store.tenant_datasets.save_task_context(dataset_name, framing)
    checkin_line("task check-in", f"committed framing for {dataset_name}")


async def _mint_fresh_session(
    args: argparse.Namespace,
) -> tuple[Session, CampaignConfig, str, str]:
    """Find-or-create campaign + mint session + root cycle. No scoring — the origin is phase 0 of the loop."""

    file_config = read_campaign_config_file(Path(args.config)) if args.config else {}
    # Resolution order: positional dataset → --dataset-name → config["dataset_name"]
    dataset_name = (
        getattr(args, "dataset", None) or args.dataset_name or file_config.get("dataset_name")
    )
    if not dataset_name:
        raise SystemExit(
            "ERROR: `new` requires a dataset name. Pass it as a positional "
            "(`new aime`), via `--dataset-name <name>`, or via a `--config` "
            "that names one.\n\n" + no_dataset_hint()
        )

    session = await init_services_cli(
        backend_url=args.backend_url,
        backend_id=args.backend_id,
        dataset_name=dataset_name,
        identity=identity_from_args(args),
    )

    # Auto-load dataset's campaign.json from the resolved config dir (tenant-first
    # via session.dataset_config_dir) when --config wasn't given — else the session
    # persists with scoring=null + default knobs. file_config only feeds
    # campaign_config below, so reading it post-init is safe.
    if not args.config and session.dataset_config_dir is not None:
        default_config_path = dataset_campaign_path(session.dataset_config_dir)
        if default_config_path.exists():
            file_config = read_campaign_config_file(default_config_path)

    campaign_config = _load_cfg(file_config)

    train_data = session.samples

    # Framing BEFORE identity — the cycle id about to be minted hashes the prompt this commits,
    # so an operator-supplied description has to land as dataset content first.
    await _commit_task_framing(session, task_file=args.task_file, task_text=args.task_text)

    # The one shared mint prologue — same application seam the web mint runs (detached).
    minted = prepare_fresh_cycle(
        session,
        campaign_config,
        train_data,
        campaign_id=fresh_campaign_id(session, campaign_config),
        log=logger.info if get_verbose() else None,
    )

    checkin_line("campaign", f"minted {minted.campaign_id}")

    return session, campaign_config, dataset_name, minted.session_id


async def _run_loop(
    args: argparse.Namespace,
    ctx: SessionCtx,
    campaign_config: CampaignConfig,
    session: Session,
    train_data: list[Sample],
) -> CommandResult:

    cycle_result, _ = await drive_cycle(
        args,
        ctx,
        campaign_config,
        session,
        train_data,
        mode=RunMode(
            diag=getattr(args, "diag", False),
        ),
    )
    return cycle_result_command(ctx, session, cycle_result)


async def cmd_new(args: argparse.Namespace) -> CommandResult:
    """Mint a fresh campaign and run from round 0. The positional is a dataset name or a raw CSV; both
    produce the same session bundle, so the tail (backend → dataset → pipeline → task → loop) is one."""
    if (pos := getattr(args, "dataset", None)) and Path(pos).is_file():
        session, campaign_config, dataset_name, _sid = await _ingest_and_prepare_checkin(args)
    else:
        session, campaign_config, dataset_name, _sid = await _mint_fresh_session(args)

    backend_type = backend_type_of_dataset(session.store, dataset_name)
    try:
        await probe_backend(backend_type, args.backend_url)
    except BackendUnreachableError as exc:
        return backend_unreachable_result(exc)
    checkin_line("backend", backend_reach_line(backend_type, args.backend_url))

    train_data = session.samples
    checkin_line("dataset", f"{dataset_name} ({len(train_data)} queries)")
    checkin_line("pipeline", pipeline_summary(session, session.pipeline_params))

    ctx = load_session(args)
    campaign_config = ctx.campaign_config
    bind_session_identity(session, ctx)

    logger.info("Session: %s", session.store.sessions.session_dir(ctx.session_id))
    logger.info("Campaign: %s", session.store.campaigns.campaign_root_dir(ctx.campaign_id))

    checkin_line("origin", "launching origin scoring")
    return await _run_loop(args, ctx, campaign_config, session, train_data)


__all__ = ["cmd_new"]
