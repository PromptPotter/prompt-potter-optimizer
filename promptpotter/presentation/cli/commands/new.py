"""``cmd_new`` — mint a fresh campaign + run the loop from round 0.

``campaign_id = {dataset}__{rand6_hex}``, fresh per call. Declaration
(target + optimizer-prompt hash) is recorded as properties on
``campaign.json`` for resume-time drift detection, not used to derive the id."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from promptpotter.application.jobs.mint import prepare_fresh_cycle
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    bind_session_identity,
    campaign_result_human,
    get_verbose,
    identity_from_args,
    init_services_cli,
)
from promptpotter.presentation.cli.session import load_session
from promptpotter.presentation.views.startup_checklist import checkin_line

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.datasets.draft_campaign import DraftCampaign
    from promptpotter.application.run_observers import RunObservers
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.cli.session import SessionCtx
    from promptpotter.presentation.views.live import LiveDisplay

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
    "max_rounds": "max_rounds",
}


def _coerce_set(field: str, raw: str) -> Any:
    """Coerce a ``--set`` string to the field's type; raise ``SystemExit`` on bad input."""
    if field == "max_rounds":
        try:
            n = int(raw)
        except ValueError:
            raise SystemExit(f"ERROR: --set max_rounds={raw!r} is not an integer.") from None
        if not 1 <= n <= 100:
            raise SystemExit(f"ERROR: --set max_rounds={n} out of range (1-100).")
        return n
    return raw


def _apply_sets(draft: DraftCampaign, sets: list[str]) -> DraftCampaign:
    """Apply operator ``field=value`` confirmations (CONFIRMED + STATED)."""
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
        attr = _SET_ATTR.get(field)
        if attr is None:
            raise SystemExit(
                f"ERROR: --set field {field!r} is not settable. One of: "
                f"{', '.join(sorted({*_SET_ATTR, 'column.query', 'column.ground_truth'}))}."
            )
        value = _coerce_set(field, raw)
        if field == "task_description":
            # Gated — confirming the framing opens the origin-readiness gate.
            draft = draft.apply_resolution(
                values={attr: value}, provenance={field: Provenance.CONFIRMED}
            )
        else:
            # Config — not gated, just set the value (parity with the web Advanced block).
            draft = draft.apply_resolution(values={attr: value})
    return draft


def _raise_incomplete(gaps: Any, last: Any) -> NoReturn:
    """Print the still-open gaps + the resolver's questions, then exit non-zero.

    The deterministic gate refused mint — surface every blocking field and how to
    close it (``--set field=value``) rather than minting a half-specified origin."""
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
    """Parse the file → apply ``--set`` → resolve the origin → return the durable
    check-in campaign id (gated).

    The check-in campaign is minted on the first action and persists: on a residual
    gap, this prints the open fields + resolver questions and exits non-zero, but the
    campaign survives so a later ``--set`` + ``resume`` can complete it — no silent
    default reaches the run."""
    from promptpotter.application.datasets.csv_ingest import IngestError
    from promptpotter.application.datasets.ingest import SlugTakenError, ingest_draft
    from promptpotter.application.datasets.origin_readiness import origin_readiness
    from promptpotter.application.datasets.origin_resolve import resolve_origin_turn
    from promptpotter.application.jobs.launcher import save_checkin_draft
    from promptpotter.infrastructure.store import build_stores

    file_path = Path(args.dataset)
    stores = build_stores(identity_from_args(args))

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
    """File → durable check-in → flip to ``active`` + build the run session, inline.

    The CLI tail of the check-in Start: shares :func:`prepare_checkin_run` with the
    web detach path (the ONLY difference is run-invocation). Returns the same
    4-tuple as :func:`_mint_fresh_session`, so ``cmd_new``'s common tail (backend
    status, task check-in, loop) runs unchanged for both file + dataset-name inputs.

    Backend reachability isn't preflighted here: a check-in is durable, so if the
    backend is down the campaign is minted + flipped + left ready, and the common
    tail's status check reports it — ``resume`` runs it once the backend is up."""
    from promptpotter.application.jobs.launcher import load_checkin_draft, prepare_checkin_run
    from promptpotter.infrastructure.store import build_stores

    campaign_id = await _ingest_checkin(args)
    stores = build_stores(identity_from_args(args))
    campaign = stores.campaigns.load_campaign(campaign_id)
    assert campaign is not None  # just minted
    draft = load_checkin_draft(stores, campaign_id)
    assert draft is not None  # just authored

    async def make_session(dataset_name: str) -> Session:
        return await init_services_cli(
            backend_url=args.backend_url,
            backend_id=args.backend_id,
            experiment_id=args.experiment_id,
            dataset_name=dataset_name,
            identity=identity_from_args(args),
        )

    prepared = await prepare_checkin_run(
        stores,
        campaign_id=campaign_id,
        cycle_id=campaign.root_cycle_id,
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


async def _checkin_task(
    session: Session,
    session_id: str,
    *,
    task_file: str | None,
    task_text: str | None,
) -> None:
    """Resolve the run's ``task_context`` and stash it on session state.

    ``{dataset_config_dir}/task_context.json`` is the canonical framing — written
    at commit from the check-in's decomposition, or decomposed once on first sight
    for a repo benchmark. ``--task-file``/``--task-text`` decompose an ad-hoc
    override instead. Either way the result rides session state so ``resume`` /
    ``sweep`` read it back; no second check-in call recomputes what ingest already
    decomposed."""
    from promptpotter.application.optimization.task_context import (
        decompose_prompt_fields,
        load_or_build_task_context,
    )
    from promptpotter.domain.search_point import TaskDecomposition

    if task_file:
        override: str | None = Path(task_file).read_text(encoding="utf-8")
    else:
        override = task_text
    if override:
        result = await decompose_prompt_fields(override)
        tc_dict = dict(result.get("task_context") or {})
        tc_dict["raw_description"] = override
        task_context = TaskDecomposition.from_dict(tc_dict)
    else:
        task_context = await load_or_build_task_context(session.dataset_config_dir)

    if not task_context:
        checkin_line("task check-in", "no task description — skipped")
        return

    n = len(task_context)
    checkin_line("task check-in", f"resolved ({n} field{'' if n == 1 else 's'})")
    state = session.store.sessions.read(session_id) or {}
    state["task_context"] = task_context.to_dict()
    session.store.sessions.update(session_id, state)


async def _mint_fresh_session(
    args: argparse.Namespace,
) -> tuple[Session, CampaignConfig, str, str]:
    """Find-or-create campaign + mint session + root cycle. No scoring (origin runs as phase 0 of the loop).
    Returns ``(session, campaign_config, dataset_name, session_id)``."""
    from promptpotter.application.config import load_campaign_config as _load_cfg
    from promptpotter.application.datasets import read_campaign_config_file
    from promptpotter.application.origin import prepare_datasets
    from promptpotter.infrastructure.store import session_index

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
        experiment_id=args.experiment_id,
        dataset_name=dataset_name,
        identity=identity_from_args(args),
    )
    backend_id = session.backend_id

    # Auto-load dataset's campaign.json from the resolved config dir (tenant-first
    # via session.dataset_config_dir) when --config wasn't given — else the session
    # persists with scoring=null + default knobs. file_config only feeds
    # campaign_config below, so reading it post-init is safe.
    if not args.config and session.dataset_config_dir is not None:
        default_config_path = session.dataset_config_dir / "campaign.json"
        if default_config_path.exists():
            file_config = read_campaign_config_file(default_config_path)

    profile = session.store.backends.load_connector_profile(backend_id) or {}
    campaign_config = _load_cfg({**profile, **file_config})

    if args.excel_path:
        train_data = prepare_datasets(session.store, args.excel_path).train_data or []
    else:
        train_data = session.samples or []

    # The one shared mint prologue — same application seam the web mint runs
    # (detached). The CLI keeps only the inline check-in lines + task-context step.
    minted = prepare_fresh_cycle(
        session,
        campaign_config,
        train_data,
        log=logger.info if get_verbose() else None,
    )

    sess_n = session_index(minted.cycle_id)
    if sess_n == 1:
        checkin_line("campaign", f"minted {minted.campaign_id} — session #1")
    else:
        checkin_line("campaign", f"joined {minted.campaign_id} — session #{sess_n}")

    return session, campaign_config, dataset_name, minted.session_id


def _build_live_display(
    args: argparse.Namespace,
    *,
    session: Session,
    campaign_config: CampaignConfig,
    origin_acc: float,
) -> LiveDisplay:
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
    train_data: list[Sample],
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
    root_ctx: SessionCtx,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
    sweep_payloads: list[tuple[Path, Any]],
) -> CommandResult:
    """Thin shim → ``application.sweep.run_sweep_batch``; binds observer factory to CLI args + campaign_config."""
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
    args: argparse.Namespace,
    ctx: SessionCtx,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
    dataset_config_dir: Path | None,
) -> CommandResult | None:
    """Multi-fork sweep dispatch: with ``--sweep-batch`` AND ``{dataset_dir}/sweep/*.json``,
    mint one fork per OperatorSweepFile. ``None`` falls through to the normal path."""
    if not getattr(args, "sweep", False):
        return None
    from promptpotter.application.sweep import (
        load_sweep_payloads,
        resolve_sweep_dir,
    )

    sweep_dir = resolve_sweep_dir(dataset_config_dir)
    if sweep_dir is None:
        return None
    sweep_payloads = load_sweep_payloads(sweep_dir)
    if not sweep_payloads:
        return None
    ctx.save_phase("optimizing")
    return await _run_sweep_batch(args, ctx, campaign_config, train_data, sweep_payloads)


async def _run_loop(
    args: argparse.Namespace,
    ctx: SessionCtx,
    campaign_config: CampaignConfig,
    session: Session,
    train_data: list[Sample],
) -> CommandResult:
    """Build observers, drive the optimization loop."""
    from promptpotter.application.runner import (
        RunMode,
    )
    from promptpotter.application.runner import (
        run_optimization as _orch_run_optimization,
    )

    pre_origin_acc = ctx.state.get("origin_accuracy", 0.0)
    observers = _build_observers(args, session, campaign_config, train_data, pre_origin_acc)
    ctx.save_phase("optimizing")

    # Control-local hooks (stop.flag / pause.flag under .runtime/) are bound
    # centrally in run_optimization (the single runner seam) so CLI and API
    # launches behave identically — no per-entry-point wiring here.
    cycle_result = await _orch_run_optimization(
        train_data,
        campaign_config,
        session=session,
        observers=observers,
        experiment_id=ctx.state["experiment_id"],
        task_context=ctx.task_context,
        mode=RunMode(
            sweep=getattr(args, "sweep", False),
            diag=getattr(args, "diag", False),
            halt_at_accuracy=getattr(args, "halt_at_accuracy", None),
        ),
        # CLI ``--spend-budget`` overrides ``OptimizationConfig.spend_budget_usd``;
        # falls back to the config value when no flag is given.
        spend_budget_usd=getattr(args, "spend_budget_usd", None)
        or campaign_config.optimization.spend_budget_usd,
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


def _pipeline_detail(session: Session) -> str:
    """Pipeline name + active-node summary for the pre-flight check-in line."""
    ps = session.pipeline_schema
    pipe = f"{ps.name} v{ps.version}" if ps else "pipeline unavailable"
    active = list((session.pipeline_params or {}).get("steps") or [])
    nodes = f"{len(active)} node{'' if len(active) == 1 else 's'}"
    if active:
        nodes += f" ({', '.join(active)})"
    return f"{pipe} · {nodes}"


async def cmd_new(args: argparse.Namespace) -> CommandResult:
    """Mint a fresh campaign and run the loop from round 0.

    The positional is a dataset name *or* a raw file (CSV). A file folds onto the
    durable check-in path (``_ingest_and_prepare_checkin``): ingest → resolve origin
    → flip the check-in campaign to ``active`` + build the session, all sharing
    :func:`prepare_checkin_run` with the web. A dataset name mints fresh
    (``_mint_fresh_session``). Both produce the same session bundle, so the tail
    below (backend → dataset → pipeline → task → origin → loop) is one path.
    Live state: ``cycles/{cycle_id}/dashboard.json``; digest: ``campaigns/{campaign_id}/log.md``;
    final: ``cycles/{cycle_id}/index.json::final``. Stop with Ctrl+C."""
    from promptpotter.shared.spend import refresh_rates

    refresh_rates()

    if (pos := getattr(args, "dataset", None)) and Path(pos).is_file():
        session, campaign_config, dataset_name, session_id = await _ingest_and_prepare_checkin(args)
    else:
        session, campaign_config, dataset_name, session_id = await _mint_fresh_session(args)

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return CommandResult(
            data={"error": "backend_unreachable", "backend_url": args.backend_url},
            human=(
                f"Backend unreachable at {args.backend_url}.\n\n"
                "The TermNorm backend ships in a sibling repo. Clone it next to "
                "PromptPotter, then start it:\n"
                "  TermNorm-excel\\backend-api\\start-server-py-LLMs.bat\n\n"
                "Install guide: docs/manual/02-install.md"
            ),
        )
    checkin_line("backend", f"reachable at {args.backend_url}")

    train_data = session.samples or []
    checkin_line("dataset", f"{dataset_name} ({len(train_data)} queries)")
    checkin_line("pipeline", _pipeline_detail(session))

    await _checkin_task(
        session,
        session_id,
        task_file=args.task_file,
        task_text=args.task_text,
    )

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
