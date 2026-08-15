"""``cmd_new`` — mint a fresh campaign + run the loop from round 0. The declaration (target +
optimizer-prompt hash) rides ``campaign.json`` for resume-time drift, never derives the id."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from pydantic import ValidationError

from promptpotter.application.datasets.draft_campaign import OptimizationOverrides
from promptpotter.application.jobs.mint import fresh_campaign_id, prepare_fresh_cycle
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    backend_unreachable_result,
    bind_session_identity,
    build_observers,
    cycle_result_command,
    drive_cycle,
    get_verbose,
    identity_from_args,
    init_services_cli,
    pipeline_summary,
)
from promptpotter.presentation.cli.session import load_session
from promptpotter.presentation.views.startup_checklist import checkin_line
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.datasets.draft_campaign import DraftCampaign
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_observers import RunObservers
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.cli.session import SessionCtx

logger = logging.getLogger("promptpotter.presentation.cli")


# --- File-ingest branch: `new <file>` folds onto the durable check-in path ----
# A raw file → durable check-in campaign → resolved origin → flip to active + run
# inline. The CLI owns no ingest/resolve/commit logic of its own; every step is an
# application-layer call shared with the web (`ingest_draft`, `resolve_origin_turn`,
# `prepare_checkin_run`). The ONLY CLI/web difference is run-invocation: the CLI
# runs the loop inline with `LiveDisplay`; the web detaches via `JobRegistry`.
# Spec: ``docs/specs/roadmap.md``.

# Field id → ``DraftCampaign`` attribute a ``--set`` writes — the CLI parallel of
# the web Advanced block. Only ``task_description`` is gated (CONFIRM opens the
# readiness gate); the rest are config the operator overrides off its default.
# Columns route through ``confirm_columns`` separately.
_SET_ATTR: dict[str, str] = {
    "task_description": "raw_task_description",
    "connector": "connector",
    "scoring_composite": "scoring_composite",
}

# The campaign-config knobs are NOT draft attributes — they ride the draft's one
# ``optimization_overrides`` dict, so a ``--set`` on them merges + validates through
# ``OptimizationOverrides`` exactly as the web Advanced block does
# (``routers/commands.py::dispatch_draft_patch``). Derived from the model, never
# hand-listed. ``mechanisms`` is nested and has no flat string form.
_SET_KNOBS: frozenset[str] = frozenset(OptimizationOverrides.model_fields) - {"mechanisms"}


def _settable() -> str:
    return ", ".join(sorted({*_SET_ATTR, *_SET_KNOBS, "column.query", "column.ground_truth"}))


def _set_knob(draft: DraftCampaign, field: str, raw: str) -> DraftCampaign:
    """Merge one ``--set`` knob onto the draft's overrides. Pydantic coerces the raw CLI string and
    enforces the leaf's bounds, so there is no second range check to drift from the model's own."""
    merged = {**draft.optimization_overrides, field: raw}
    try:
        knobs = OptimizationOverrides.model_validate(merged)
    except ValidationError as exc:
        detail = exc.errors()[0].get("msg", "invalid value")
        raise SystemExit(f"ERROR: --set {field}={raw!r} rejected: {detail}") from None
    return draft.apply_resolution(values={"optimization_overrides": knobs.model_dump(mode="json")})


def _apply_sets(draft: DraftCampaign, sets: list[str]) -> DraftCampaign:
    for item in sets:
        if "=" not in item:
            raise SystemExit(f"ERROR: --set expects FIELD=VALUE, got {item!r}.")
        field, raw = item.split("=", 1)
        field, raw = field.strip(), raw.strip()
        if field in ("column.query", "column.ground_truth"):
            if raw not in draft.headers:
                raise SystemExit(
                    f"ERROR: --set {field}={raw!r} is not an uploaded column. "
                    f"Available: {', '.join(draft.headers) or '<none>'}."
                )
            kwarg = "query_col" if field == "column.query" else "ground_truth_col"
            draft = draft.confirm_columns(**{kwarg: raw})
            continue
        if field in _SET_KNOBS:
            draft = _set_knob(draft, field, raw)
            continue
        attr = _SET_ATTR.get(field)
        if attr is None:
            raise SystemExit(
                f"ERROR: --set field {field!r} is not settable. One of: {_settable()}."
            )
        if field == "task_description":
            # Gated — confirming the framing opens the origin-readiness gate.
            draft = draft.apply_resolution(
                values={attr: raw}, provenance={field: Provenance.CONFIRMED}
            )
        else:
            # Config — not gated, just set the value (parity with the web Advanced block).
            draft = draft.apply_resolution(values={attr: raw})
    return draft


def _raise_incomplete(gaps: Any, last: Any) -> NoReturn:
    """Print the still-open gaps + the resolver's questions and exit non-zero, rather than minting a
    half-specified origin."""
    lines = ["ERROR: origin still incomplete — nothing minted.", "", "Open fields:"]
    lines += [f"  - {g.field}: {g.hint}" for g in gaps]
    questions = (
        (last.resolution.get("last_resolution") or {}).get("next_action", {}).get("questions", [])
        if last
        else []
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
    from promptpotter.application.datasets.csv_ingest import IngestError
    from promptpotter.application.datasets.ingest import SlugTakenError, ingest_draft
    from promptpotter.application.datasets.origin_readiness import origin_readiness
    from promptpotter.application.datasets.origin_resolve import resolve_origin_turn
    from promptpotter.application.jobs.launcher.checkin import save_checkin_draft
    from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
    from promptpotter.infrastructure.store.stores import build_stores

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

    if args.sets:
        draft = _apply_sets(draft, args.sets)
        save_checkin_draft(stores, draft)

    last: Any = None
    if not origin_readiness(draft).complete:
        checkin_line("origin resolver", "running AI check-in")
        # One turn: code owns the deterministic facts (the answer space) and the
        # operator states the framing up front via --set, so the resolver only
        # reads the columns + authors the prompt. A residual gap surfaces below
        # with the --set instructions rather than spinning more LLM turns.
        last = await resolve_origin_turn(stores=stores, draft=draft)
        draft = last.draft

    readiness = origin_readiness(draft)
    if not readiness.complete:
        _raise_incomplete(readiness.gaps, last)

    checkin_line("origin", f"complete — check-in '{draft.slug}' ready")
    return campaign_id


async def _ingest_and_prepare_checkin(
    args: argparse.Namespace,
) -> tuple[Session, CampaignConfig, str, str]:
    """The CLI tail of check-in Start, sharing :func:`prepare_checkin_run` with the web detach path.
    Backend reachability is not preflighted: a check-in is durable, so ``resume`` runs it later."""
    from promptpotter.application.jobs.launcher.checkin import (
        load_checkin_draft,
        prepare_checkin_run,
    )
    from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
    from promptpotter.infrastructure.store.stores import build_stores

    campaign_id = await _ingest_checkin(args)
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    campaign = stores.campaigns.load_campaign(campaign_id)
    assert campaign is not None  # just minted
    draft = load_checkin_draft(stores, campaign_id)
    assert draft is not None  # just authored

    async def make_session(dataset_name: str) -> Session:
        return await init_services_cli(
            backend_url=args.backend_url,
            backend_id=args.backend_id,
            dataset_name=dataset_name,
            identity=identity_from_args(args),
        )

    prepared = await prepare_checkin_run(
        stores,
        hop=campaign.root_hop,
        draft=draft,
        make_session=make_session,
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
    from promptpotter.application.initialization.session import mint_checkin_skeleton
    from promptpotter.application.optimization.task_context import (
        checkin_call_context,
        committed_task_context,
        decompose_prompt_fields,
    )
    from promptpotter.domain.cycle_paths import CycleHop

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
    _sid, campaign_id, cycle_id = mint_checkin_skeleton(session.store, slug=dataset_name)
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
    from promptpotter.application.campaign_config import load_campaign_config as _load_cfg
    from promptpotter.application.datasets.authored import (
        dataset_campaign_path,
        read_campaign_config_file,
    )

    # Shared unwrap only — `new` validates AFTER merging with the connector
    # profile ({**profile, **file_config}), a different composition order than
    # the draft path, so it keeps its own validate-after-merge step below.
    file_config = read_campaign_config_file(Path(args.config)) if args.config else {}
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

    session = await init_services_cli(
        backend_url=args.backend_url,
        backend_id=args.backend_id,
        dataset_name=dataset_name,
        identity=identity_from_args(args),
    )
    backend_id = session.backend_id

    # Auto-load dataset's campaign.json from the resolved config dir (tenant-first
    # via session.dataset_config_dir) when --config wasn't given — else the session
    # persists with scoring=null + default knobs. file_config only feeds
    # campaign_config below, so reading it post-init is safe.
    if not args.config and session.dataset_config_dir is not None:
        default_config_path = dataset_campaign_path(session.dataset_config_dir)
        if default_config_path.exists():
            file_config = read_campaign_config_file(default_config_path)

    profile = session.store.backends.load_connector_profile(backend_id) or {}
    campaign_config = _load_cfg({**profile, **file_config})

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


async def _run_sweep_batch(
    args: argparse.Namespace,
    root_ctx: SessionCtx,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
    sweep_payloads: list[tuple[Path, Any]],
) -> CommandResult:
    """Thin shim → ``application.sweep.run_sweep_batch``, binding the observer factory and the
    active-pointer reload to CLI args so the application layer imports no ``argparse``."""
    from promptpotter.application.sweep import run_sweep_batch
    from promptpotter.presentation.cli.session import load_session

    def observer_factory(session: Session, origin_acc: float) -> RunObservers:
        return build_observers(session, campaign_config, train_data, origin_acc)

    result = await run_sweep_batch(
        lambda: load_session(args),
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
    dataset_config_dir: Path | None,
) -> CommandResult | None:
    """``--sweep-batch`` mints one fork per ``OperatorSweepFile``. A missing or empty payload is a LOUD
    setup error: running one unpaired cycle instead answers a different question than the one posed."""
    if not getattr(args, "sweep", False):
        return None
    from promptpotter.application.sweep import load_sweep_payloads, resolve_sweep_dir

    sweep_dir = resolve_sweep_dir(dataset_config_dir)
    if sweep_dir is None:
        raise PayloadInvalidError(
            f"--sweep-batch needs a sweep/ directory of payloads, and {dataset_config_dir} "
            "has none. Author one YAML OperatorSweepFile per arm there, or drop the flag."
        )
    sweep_payloads = load_sweep_payloads(sweep_dir)
    if not sweep_payloads:
        raise PayloadInvalidError(
            f"--sweep-batch found {sweep_dir} but no *.yaml payloads in it. Author one "
            "OperatorSweepFile per arm, or drop the flag."
        )
    return await _run_sweep_batch(args, ctx, campaign_config, train_data, sweep_payloads)


async def _run_loop(
    args: argparse.Namespace,
    ctx: SessionCtx,
    campaign_config: CampaignConfig,
    session: Session,
    train_data: list[Sample],
) -> CommandResult:
    from promptpotter.application.runner.entry import RunMode

    cycle_result, _ = await drive_cycle(
        args,
        ctx,
        campaign_config,
        session,
        train_data,
        mode=RunMode(
            sweep=getattr(args, "sweep", False),
            diag=getattr(args, "diag", False),
            halt_at_accuracy=getattr(args, "halt_at_accuracy", None),
        ),
    )
    return cycle_result_command(ctx, session, cycle_result)


async def cmd_new(args: argparse.Namespace) -> CommandResult:
    """Mint a fresh campaign and run from round 0. The positional is a dataset name or a raw CSV; both
    produce the same session bundle, so the tail (backend → dataset → pipeline → task → loop) is one."""
    from promptpotter.shared.spend import refresh_rates_in_background

    refresh_rates_in_background()

    if (pos := getattr(args, "dataset", None)) and Path(pos).is_file():
        session, campaign_config, dataset_name, _sid = await _ingest_and_prepare_checkin(args)
    else:
        session, campaign_config, dataset_name, _sid = await _mint_fresh_session(args)

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return backend_unreachable_result(args.backend_url)
    checkin_line("backend", f"reachable at {args.backend_url}")

    train_data = session.samples
    checkin_line("dataset", f"{dataset_name} ({len(train_data)} queries)")
    checkin_line("pipeline", pipeline_summary(session, session.pipeline_params))

    ctx = load_session(args)
    campaign_config = ctx.campaign_config
    bind_session_identity(session, ctx)

    logger.info("Session: %s", session.store.sessions.session_dir(ctx.session_id))
    logger.info("Campaign: %s", session.store.campaigns.campaign_root_dir(ctx.campaign_id))

    if (
        sweep_result := await _maybe_dispatch_sweep_batch(
            args, ctx, campaign_config, train_data, session.dataset_config_dir
        )
    ) is not None:
        return sweep_result

    checkin_line("origin", "launching origin scoring")
    return await _run_loop(args, ctx, campaign_config, session, train_data)


__all__ = ["cmd_new"]
