"""CLI entry point — argparse schema, ``cmd_init`` + ``cmd_optimize``, ``main()``.

The CLI is two write verbs: ``init`` creates a session+cycle, ``optimize`` runs
a campaign against it. Reads happen by opening the on-disk artifact tree
(``sessions/{id}/``, ``campaigns/{cycle_id}/``) — ``dashboard.json`` for live
state, ``log.md`` for the digest, ``index.json`` for the final summary
including ``stop_reason``. Stop with Ctrl+C — there is no mid-run pause/resume.

``session.py`` carries ``SessionCtx``/``load_session``/``load_campaign_config``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Windows consoles default to cp1252 which can't print Unicode symbols.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.infrastructure.store.base import read_text_optional
from promptpotter.presentation.cli.session import (
    load_campaign_config,
    load_session,
)

if TYPE_CHECKING:
    from promptpotter.application.bootstrap import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.runner import RunListener
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.infrastructure.projections import (
        AuditTrailProjection,
        LiveDashboardProjection,
    )
    from promptpotter.presentation.cli.session import SessionCtx
    from promptpotter.presentation.views.live import LiveDisplay

logger = logging.getLogger("promptpotter.presentation.cli")


@dataclass
class CommandResult:
    """``data`` is machine-readable; ``human`` is pre-rendered text. ``main()`` picks one."""

    data: dict[str, Any] | None = None
    human: str | None = None


_VERBOSE = False


def set_verbose(value: bool) -> None:
    """Toggle verbose mode. Called once from ``main()`` before dispatch."""
    global _VERBOSE
    _VERBOSE = value


def log_startup_summary(
    session: Session,
    pipeline_params: dict | None,
    dataset_len: int,
    backend_url: str,
    dataset_name: str | None,
) -> None:
    """One-line collapsed summary of pipeline + backend + dataset + active nodes."""
    ps = session.pipeline_schema
    pipe = f"{ps.name} v{ps.version}" if ps else "pipeline unavailable"
    active = list((pipeline_params or {}).get("steps") or [])
    nodes = f"{len(active)} node{'s' if len(active) != 1 else ''}"
    if active:
        nodes += f" ({', '.join(active)})"
    ds = f"{dataset_name or '?'} ({dataset_len} queries)"
    logger.info("%s · %s · backend %s · dataset %s", pipe, nodes, backend_url, ds)


async def init_services_cli(
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    dataset_name: str | None = None,
    take_over: bool = False,
    tenant_id: str = "default",
) -> Session:
    """Initialize services for a CLI command (logging style + service init)."""
    from promptpotter.application.bootstrap import init_services
    from promptpotter.config.logging import setup_logging

    setup_logging(style="full" if _VERBOSE else "cli")
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return await init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        on_status=lambda msg: logger.info(msg) if _VERBOSE else None,
        take_over=take_over,
        tenant_id=tenant_id,
    )


def _prepare_cycle(session: Session, campaign_config: CampaignConfig, dataset: list):
    """Apply pipeline → load baseline → compute cycle_id. Returns (pipeline_params, baseline, cycle_id)."""
    from promptpotter.application.baseline import load_baseline_prompt
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.runner import build_baseline_cycle_id

    schema = session.pipeline_schema
    pipeline_params = configure_and_apply_pipeline(
        session, campaign_config, log=logger.info if _VERBOSE else (lambda *_a, **_k: None)
    )
    baseline = load_baseline_prompt(
        session.experiment_extract,
        prompt_node_names=schema.prompt_node_names() if schema else [],
        dataset_name=session.dataset_name,
    )
    return pipeline_params, baseline, build_baseline_cycle_id(baseline, schema, dataset)


def _add_global_args(parser: argparse.ArgumentParser) -> None:
    """Tenant + session + verbosity flags shared across every command."""
    parser.add_argument("--session", default=None, help="Session ID (default: active)")
    parser.add_argument(
        "--tenant",
        default="default",
        help="Tenant partition under .promptpotter/projects/ (default: 'default')",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logs (timestamps, module tags, every INFO line)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON instead of human-formatted text",
    )


def _add_init_args(p_init: argparse.ArgumentParser) -> None:
    """Backend + dataset + task overrides for ``init``."""
    p_init.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    p_init.add_argument("--backend-id", default=DEFAULT_BACKEND_ID)
    p_init.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    p_init.add_argument("--dataset-name", default=None)
    p_init.add_argument("--excel-path", default=None)
    p_init.add_argument("--config", default=None, help="Campaign config JSON file")
    p_init.add_argument(
        "--task-file", default=None, help="Override datasets/<name>/task_description.md"
    )
    p_init.add_argument(
        "--task-text", default=None, help="Override datasets/<name>/task_description.md inline"
    )


def _add_optimize_args(p_opt: argparse.ArgumentParser) -> None:
    """Resume / divergence / mode flags for ``optimize``."""
    p_opt.add_argument(
        "--from",
        dest="resume_from_round",
        type=int,
        default=None,
        metavar="ROUND",
        help="Resume after round N (archives trials > N, reloads trial_N). "
        "Omit to resume from the latest completed round.",
    )
    p_opt.add_argument(
        "--no-divergence-check",
        dest="no_divergence_check",
        action="store_true",
        help="On resume, rescore but skip the decision-replay halt.",
    )
    p_opt.add_argument(
        "--fork-on-divergence",
        dest="fork_on_divergence",
        action="store_true",
        help="On divergence, mint a sibling cycle (with parent_cycle_id) "
        "and re-run the divergent round under the current scorer.",
    )
    mode_group = p_opt.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--sweep",
        dest="sweep",
        action="store_true",
        help="M10 cheap-trial mode: baseline → 1 full scored round → "
        "1 generation-only round (variants emitted, no scoring) → halt. "
        "index.json::final.mode lands as 'sweep' so the leaderboard can "
        "pair sweep cycles with their full counterparts.",
    )
    mode_group.add_argument(
        "--diag",
        dest="diag",
        action="store_true",
        help="M10 diagnostic mode: baseline → 1 full scored round → "
        "force L2-context (regardless of stall) → 1 generation-only "
        "round 2 (with L2 overrides applied, no scoring) → halt. "
        "index.json::final.mode lands as 'diag' and final.diag carries "
        "L2's evolved L1 surface for the operator to promote.",
    )


def _add_compare_args(p_cmp: argparse.ArgumentParser) -> None:
    """Cycle list + PoBB knobs for ``compare``."""
    p_cmp.add_argument(
        "cycle_ids",
        nargs="*",
        help="cycle ids to compare (each contributes one arm). "
        "Omit (or pass --all) to auto-discover every cycle in the active "
        "family with a final winner.",
    )
    p_cmp.add_argument(
        "--all",
        action="store_true",
        dest="all_family",
        help="Auto-discover every cycle in the active family with a final winner. "
        "Implied when no positional cycle_ids are given.",
    )
    p_cmp.add_argument("--epsilon", type=float, default=0.05, help="PoBB threshold (default 0.05)")
    p_cmp.add_argument(
        "--max-topups",
        type=int,
        default=16,
        dest="max_topups",
        help="Upper bound on extra LLM calls (default 16; -1 = unbounded, Ctrl+C to stop).",
    )
    p_cmp.add_argument(
        "--n-min-per-arm",
        type=int,
        default=4,
        dest="n_min_per_arm",
        help="Sample floor before SE-driven selection kicks in (default 4)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Argparse schema for ``init`` + ``optimize`` + ``compare``."""
    parser = argparse.ArgumentParser(
        prog="python -m promptpotter",
        description="PromptPotter optimization CLI — init creates a session+cycle, "
        "optimize runs a campaign against it. Reads happen by opening the artifact "
        "tree (sessions/{id}/, campaigns/{cycle_id}/) directly.",
    )
    _add_global_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    _add_init_args(sub.add_parser("init", help="Create session+cycle for a dataset"))
    _add_optimize_args(
        sub.add_parser("optimize", help="Run optimization loop on the active session")
    )
    _add_compare_args(
        sub.add_parser(
            "compare",
            help="PoBB-compare cycle winners across the family with adaptive top-up. "
            "Each cycle's index.json::final.winner_pipeline_params is one arm; "
            "under-measured arms get one extra score per round until a decisive "
            "P(best) emerges or the topup budget is exhausted.",
        )
    )

    return parser


def _mint_session_and_cycle(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    cycle_id: str,
    init_params: dict,
    pipeline_params: dict,
    baseline,
    dataset_count: int,
) -> str:
    """Mint session+cycle with the CLI's pipeline-snapshot extras."""
    from promptpotter.application.bootstrap import auto_mint_session

    session_id, _ = auto_mint_session(
        session,
        campaign_config,
        cycle_id=cycle_id,
        baseline_prompt_fields=baseline.prompt_field_dict(),
        dataset_size=dataset_count,
        experiment_id=init_params.get("experiment_id"),
        pipeline_params=pipeline_params,
        active_steps=list(pipeline_params.get("steps", [])),
    )
    return session_id


async def _maybe_decompose_task(
    session: Session,
    campaign_config: CampaignConfig,
    session_id: str,
    *,
    dataset_name: str,
    task_file: str | None,
    task_text: str | None,
) -> None:
    """Decompose task description once at session-creation time.

    ``datasets/{name}/task_description.md`` is the canonical source;
    ``--task-file`` and ``--task-text`` override for ad-hoc cases. Result
    is disk-cached, so re-init against the same dataset is free.
    """
    from promptpotter.application.config import create_llm_client
    from promptpotter.application.optimization.decomposition import decompose_task_context

    if task_file:
        task_description = Path(task_file).read_text(encoding="utf-8")
    elif task_text:
        task_description = task_text
    else:
        task_description = read_text_optional(
            Path("datasets") / dataset_name / "task_description.md"
        )

    if not task_description:
        return

    llm_client, model = create_llm_client(campaign_config)
    task_context, _consultation, was_cached = await decompose_task_context(
        task_description,
        llm_client,
        model,
        store_base_dir=session.store.base_dir if session.store else None,
        backend_id=session.backend_id,
    )
    logger.info(
        "Task context decomposed%s: %d fields",
        " (cached)" if was_cached else "",
        len(task_context),
    )
    state = session.store.sessions.read(session_id) or {}
    state["task_context"] = task_context.to_dict()
    session.store.sessions.update(session_id, state)


async def cmd_init(args: argparse.Namespace) -> CommandResult:
    """Initialize services, load datasets, configure pipeline, create session.

    Pure prep: no backend calls, no baseline scoring. The baseline runs as
    phase 0 of ``optimize`` on the ``sp_budget_ttest`` slice. If
    ``datasets/<name>/task_description.md`` exists (or ``--task-file`` /
    ``--task-text`` is given), the task description is decomposed once and
    stored on the session — ``optimize`` reads it from there.
    """
    from promptpotter.application.baseline import prepare_datasets
    from promptpotter.application.config import load_campaign_config as _load_cfg

    file_config = load_campaign_config(args.config)
    dataset_name = args.dataset_name or file_config.get("dataset_name")
    if not dataset_name:
        from promptpotter.presentation.cli.session import no_dataset_hint

        raise SystemExit(
            "ERROR: init requires a dataset name. Silent defaults to the "
            "TermNorm production experiment are no longer allowed.\n\n" + no_dataset_hint()
        )

    # Auto-load the dataset's campaign.json when --config wasn't given. Without this,
    # the session persists with scoring=null and default optimization knobs — the
    # dataset's own file is the intended source of truth.
    if not args.config:
        default_config_path = Path("datasets") / dataset_name / "campaign.json"
        if default_config_path.exists():
            file_config = load_campaign_config(str(default_config_path))

    session = await init_services_cli(
        backend_url=args.backend_url,
        backend_id=args.backend_id,
        experiment_id=args.experiment_id,
        dataset_name=dataset_name,
        take_over=True,
        tenant_id=getattr(args, "tenant", "default"),
    )
    backend_id = session.backend_id

    profile = session.store.backends.load_connector_profile(backend_id) or {}
    campaign_config = _load_cfg({**profile, **file_config})

    excluded = list(campaign_config.exclude_nodes)

    if args.excel_path:
        train_data = prepare_datasets(session.store, args.excel_path).train_data or []
    else:
        train_data = session.queries or []

    pipeline_params, baseline, cycle_id = _prepare_cycle(session, campaign_config, train_data)
    active = list(pipeline_params.get("steps", [])) if pipeline_params else []
    init_params = {
        "backend_url": args.backend_url,
        "backend_id": backend_id,
        "experiment_id": args.experiment_id,
        "dataset_name": dataset_name,
    }
    session_id = _mint_session_and_cycle(
        session,
        campaign_config,
        cycle_id=cycle_id,
        init_params=init_params,
        pipeline_params=pipeline_params,
        baseline=baseline,
        dataset_count=len(train_data),
    )

    await _maybe_decompose_task(
        session,
        campaign_config,
        session_id,
        dataset_name=dataset_name,
        task_file=args.task_file,
        task_text=args.task_text,
    )

    return CommandResult(
        data={
            "session_id": session_id,
            "cycle_id": cycle_id,
            "backend_id": backend_id,
            "phase": "init",
            "dataset_count": len(train_data),
            "active_steps": active,
            "excluded_nodes": excluded,
        },
        human=(
            f"\nSession created: {session_id}\n"
            f"Cycle: {cycle_id}\n"
            f"Dataset: {len(train_data)} queries (baseline runs in optimize phase 0)"
        ),
    )


def _build_live_display(args: argparse.Namespace, *, session, campaign_config, baseline_acc: float):
    """Pick the live display: full notebook parity in ``-v``, concise otherwise."""
    from promptpotter.application.scoring.formula import split_scoring_block
    from promptpotter.presentation.views.display import set_display_tags

    set_display_tags(session.pipeline_schema)
    scoring_formula = split_scoring_block(campaign_config.scoring).per_query
    opt = campaign_config.optimization

    from promptpotter.presentation.views.live import LiveDisplay

    if getattr(args, "verbose", False):
        return LiveDisplay(
            campaign_rounds=[],
            baseline_acc=baseline_acc,
            l1_patience=opt.l1_patience,
            pipeline_schema=session.pipeline_schema,
            store=session.store,
            scoring_formula=scoring_formula,
        )
    return LiveDisplay(
        baseline_acc=baseline_acc,
        l1_patience=opt.l1_patience,
        sp_budget_ttest=campaign_config.sp_budget_ttest,
        scoring_formula=scoring_formula,
        pipeline_schema=session.pipeline_schema,
    )


def _build_divergence_hint() -> str:
    """Derive the divergence-checked-kinds list from the DECISION_GATING table.

    The hint used to hardcode the gated kinds, which silently rotted
    every time a new ``DecisionKind`` member landed. Now it walks the
    enum so adding a kind (with its gating choice) updates the operator
    message automatically.
    """
    from promptpotter.domain.run_records import DECISION_GATING, GatingMode

    replayed = sorted(k.value for k, m in DECISION_GATING.items() if m is GatingMode.REPLAYED)
    archival = sorted(k.value for k, m in DECISION_GATING.items() if m is GatingMode.ARCHIVAL)
    return (
        f"Checked decisions: {', '.join(replayed)}.\n"
        f"(Archival, not divergence-gated: {', '.join(archival)}.)\n\n"
        "Rerun with `--fork-on-divergence` to branch a sibling cycle "
        "here under the current scorer, revert "
        "`campaign.json::scoring` to continue the original trajectory, "
        "or pass `--no-divergence-check` to accept the divergence."
    )


_DIVERGENCE_HINT = _build_divergence_hint()


def _prepare_cycle_for_optimize(
    args: argparse.Namespace,
    ctx: SessionCtx,
    session: Session,
    campaign_config: CampaignConfig,
    train_data: list,
) -> tuple[dict, OptSearchPoint]:
    """Resolve cycle context: drift-detect + auto-mint + ``--from`` validation.

    Pipeline divergence-detect: if pipeline.json (model, temperature, …),
    baseline prompt, or dataset changed since the active session was
    init'd, the recomputed cycle hash no longer matches the pointer's
    cycle_id. Auto-mint a fresh session+cycle so a model swap starts a
    new campaign root instead of silently mixing measurements from the
    old model. Mutates ``ctx`` (cycle_id / session_id / state) in place.
    Returns ``(pipeline_params, baseline)`` for the emitter.
    """
    resume_from_round: int | None = getattr(args, "resume_from_round", None)
    pipeline_params, baseline_now, expected_cycle_id = _prepare_cycle(
        session, campaign_config, train_data
    )

    minted_fresh = False
    if ctx.cycle_id and resume_from_round is None and expected_cycle_id != ctx.cycle_id:
        old_cycle_id = ctx.cycle_id
        ctx.session_id = _mint_session_and_cycle(
            session,
            campaign_config,
            cycle_id=expected_cycle_id,
            init_params=ctx.init_params,
            pipeline_params=pipeline_params,
            baseline=baseline_now,
            dataset_count=len(train_data),
        )
        ctx.cycle_id = expected_cycle_id
        ctx.state = session.store.sessions.read(ctx.session_id) or ctx.state
        minted_fresh = True
        logger.info(
            "Pipeline changed since init (was %s, now %s) — minted new cycle",
            old_cycle_id,
            expected_cycle_id,
        )

    if resume_from_round is not None:
        if not ctx.cycle_id:
            raise ValueError(
                "--from requires an active cycle on this session; run `optimize` first"
            )
        if resume_from_round < 0:
            raise ValueError(f"--from must be >= 0, got {resume_from_round}")
        logger.info("Resuming cycle %s from after round %d", ctx.cycle_id, resume_from_round)
    elif ctx.cycle_id and not minted_fresh:
        logger.info("Resuming cycle %s", ctx.cycle_id)

    return pipeline_params, baseline_now


def _build_run_observers(
    args: argparse.Namespace,
    session: Session,
    campaign_config: CampaignConfig,
    campaign_dir: Path,
    baseline_acc: float,
) -> tuple[RunListener, LiveDashboardProjection, AuditTrailProjection, LiveDisplay]:
    """Wire audit trail + live dashboard + display + listener.

    Built BEFORE baseline so the dashboard ticks through the BASELINE
    phase and per-query output reaches the terminal. Without this, the
    CLI goes dark for the entire BASELINE phase. Recorder is built first
    so the emitter can hold a direct reference (no callback indirection).
    Side-effect: assigns the recorder to ``session.state.round_recorder``.
    Display is returned as a separate handle so the entry point can pass
    it to ``run_optimization`` (which binds it to the cycle ledger).
    """
    from promptpotter.application.baseline import build_campaign_emitter
    from promptpotter.application.runner import RunListener as _RunListener
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.infrastructure.projections import (
        AuditTrailProjection as _AuditTrailProjection,
    )

    recorder = _AuditTrailProjection.from_cycle_dir(CycleDir(campaign_dir))
    recorder.rehydrate_sticky()
    session.state.round_recorder = recorder

    emitter = build_campaign_emitter(
        session,
        campaign_config,
        baseline_accuracy=baseline_acc,
        resumed_from_round=getattr(args, "resume_from_round", None),
        recorder=recorder,
    )
    display = _build_live_display(
        args, session=session, campaign_config=campaign_config, baseline_acc=baseline_acc
    )
    listener = _RunListener()
    return listener, emitter, recorder, display


def _resolve_sweep_dir(dataset_name: str | None) -> Path | None:
    """Path to ``datasets/{name}/sweep/`` if it exists, else None."""
    if not dataset_name:
        return None
    sweep_dir = Path("datasets") / dataset_name / "sweep"
    return sweep_dir if sweep_dir.is_dir() else None


def _load_sweep_payloads(sweep_dir: Path) -> list[tuple[Path, Any]]:
    """Parse every ``*.json`` under ``sweep_dir`` into ``(path, SweepPayload)``.

    Pydantic ``extra='forbid'`` raises ``ValidationError`` on unknown keys,
    so operator typos halt the batch before any fork mints.
    """
    from promptpotter.domain.run_records import SweepPayload

    files = sorted(sweep_dir.glob("*.json"))
    return [(p, SweepPayload.model_validate_json(p.read_text(encoding="utf-8"))) for p in files]


def _existing_fork_source_files(store: Any, parent_cycle_id: str) -> dict[str, str]:
    """Read the batch parent's ledger; return ``{source_file: fork_cycle_id}`` for prior FORK_CUTs.

    Lets the batch dispatcher skip payloads that already minted a fork
    from this exact parent on a previous run, so re-invoking
    ``optimize --sweep`` after an interrupt at the same active pointer
    picks up where it stopped instead of duplicating hypotheses.
    Scoped to the active pointer's current cycle — sweep can branch
    from a root, a fork, a fork-of-fork, or any deeper node; the
    branch point is whatever ``cycle_id`` the active pointer (or
    direct ``--cycle`` arg, when added) names.

    A FORK_CUT pointing at a fork dir that no longer exists is
    ignored: the ledger is append-only, so an operator who deleted a
    bogus fork (e.g., post-bug cleanup) would otherwise be permanently
    blocked from re-running that payload from this parent.
    """
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.domain.run_records import DecisionKind
    from promptpotter.infrastructure.ledger import RunLedger

    out: dict[str, str] = {}
    parent_dir = store.campaigns.campaign_dir(parent_cycle_id)
    if not (parent_dir / "ledger.jsonl").exists():
        return out
    ledger = RunLedger.open(CycleDir(parent_dir))
    for record in ledger.iter():
        if (
            getattr(record, "record_type", None) == "decision"
            and getattr(record, "kind", None) == DecisionKind.FORK_CUT
        ):
            data = getattr(record, "data", {}) or {}
            src = data.get("source_file")
            outcome = getattr(record, "outcome", None)
            if src and outcome and store.campaigns.campaign_dir(str(outcome)).exists():
                out[str(src)] = str(outcome)
    return out


async def _run_sweep_batch(
    args: argparse.Namespace,
    root_ctx: SessionCtx,
    campaign_config: CampaignConfig,
    train_data: list,
    sweep_payloads: list[tuple[Path, Any]],
) -> CommandResult:
    """Mint one fork per ``SweepPayload`` and run sweep mode on each.

    Forks branch off ``root_ctx.cycle_id`` — the cycle the active
    pointer (or direct ``--cycle`` arg, when added) names. That can be
    a root, a fork, a fork-of-fork, or any deeper node in the
    campaign tree; sweep batches operate from a single reference
    point with no automatic walk-back. The first fork's baseline run
    populates the ``library/`` cache; subsequent forks cache-hit on
    identical baseline ``JobSearchPoint`` measurements, making
    per-fork baseline cost ≈ 0. Active pointer is restored to the
    branch point at the end so the operator's next command stays
    anchored. Re-invocation from the same branch point dedupes via
    ``data.source_file`` in the parent's FORK_CUT records — already
    forked payloads are skipped.
    """
    import secrets

    from promptpotter.application.optimization.cycle import fork_for_sweep_sibling
    from promptpotter.application.runner import (
        run_optimization as _orch_run_optimization,
    )
    from promptpotter.domain.phases import StopReason
    from promptpotter.infrastructure.store import (
        build_stores,
        save_active_pointer,
        sweep_batch_dir_for,
    )
    from promptpotter.infrastructure.store import (
        root_cycle_id as _root_cycle_id,
    )
    from promptpotter.infrastructure.store.base import write_json

    tenant_id = getattr(args, "tenant", "default")
    store = build_stores(tenant_id=tenant_id)
    # Branch point = whatever the active pointer (or direct --cycle
    # arg, when added) names. No walk-back: sweep forks must be
    # mintable from any node in the tree (root, fork, fork-of-fork).
    # An interrupted prior batch leaves the pointer on a partial
    # orphan fork; re-invoking from that pointer forks under it,
    # which is the documented behavior — operator who wants a
    # different branch point moves the pointer (or passes --cycle)
    # before re-invoking.
    parent_cycle_id = root_ctx.cycle_id
    # Idempotent re-runs from the same branch point: skip payloads
    # whose source_file already has a FORK_CUT in this parent's
    # ledger.
    existing = _existing_fork_source_files(store, parent_cycle_id)

    # Mint one batch_id per sweep invocation. Format: utc-ts + 4 random
    # hex chars (no underscores — sweep_batch_id is forbidden from
    # carrying underscores so the cycle-id regex can extract it cleanly).
    batch_id = datetime.now(UTC).strftime("b%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)
    family_root = _root_cycle_id(parent_cycle_id)
    batch_dir = sweep_batch_dir_for(store.campaigns._base_dir, family_root, batch_id)
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch_index_path = batch_dir / "index.json"
    write_json(
        batch_index_path,
        {
            "batch_id": batch_id,
            "parent_cycle_id": parent_cycle_id,
            "family_root": family_root,
            "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
            "payloads": [
                {
                    "source_file": p.name,
                    "status": ("skipped_already_forked" if p.name in existing else "pending"),
                    "cycle_id": existing.get(p.name, ""),
                }
                for p, _ in sweep_payloads
            ],
        },
    )

    new_cycle_ids: list[str] = []
    status_by_source: dict[str, str] = {}
    for path, payload in sweep_payloads:
        if path.name in existing:
            logger.info(
                "Sweep payload %s already forked → %s; skipping (re-run optimize "
                "without --sweep on that fork to resume it)",
                path.name,
                existing[path.name],
            )
            continue
        new_cycle_id = fork_for_sweep_sibling(
            campaign_store=store.campaigns,
            tenant_id=tenant_id,
            session_id=root_ctx.session_id,
            parent_cycle_id=parent_cycle_id,
            sweep_batch_id=batch_id,
            payload_source_file=path.name,
            payload=payload,
        )
        # fork_for_sweep_sibling retargeted the active pointer to new_cycle_id.
        # Re-load context for this fork and build a fresh session bound to it.
        fork_ctx = load_session(args)
        fork_session = await init_services_cli(**fork_ctx.init_params)
        fork_session.session_id = fork_ctx.session_id
        fork_session.state.cycle_id = fork_ctx.cycle_id
        # init_services_cli builds the pipeline_schema but leaves
        # session.pipeline_params empty until configure_and_apply_pipeline
        # runs. Without this, the backend receives no per-node model
        # override and falls back to its llm_defaults — i.e. wrong model.
        from promptpotter.application.config import configure_and_apply_pipeline

        configure_and_apply_pipeline(
            fork_session,
            campaign_config,
            log=logger.info if _VERBOSE else (lambda *_a, **_k: None),
        )
        logger.info(
            "Sweep fork session bound: session_id=%s cycle_id=%s pp.llm_only.model=%s",
            fork_session.session_id,
            fork_session.state.cycle_id,
            (fork_session.pipeline_params or {}).get("llm_only", {}).get("model"),
        )

        fork_campaign_dir = store.campaigns.campaign_dir(fork_ctx.cycle_id)
        listener, emitter, _recorder, display = _build_run_observers(
            args, fork_session, campaign_config, fork_campaign_dir, 0.0
        )

        fork_result = await _orch_run_optimization(
            train_data,
            campaign_config,
            session=fork_session,
            listener=listener,
            experiment_id=fork_ctx.state["experiment_id"],
            task_context=fork_ctx.task_context,
            display=display,
            emitter=emitter,
            sweep=True,
            fork_payload=payload,
        )
        new_cycle_ids.append(new_cycle_id)
        if fork_result.stop_reason == StopReason.INTERRUPTED:
            # Ctrl+C inside _run_round_loop is caught + returned as
            # INTERRUPTED instead of propagating; without this break the
            # batch silently rolls into the next fork and the operator has
            # to Ctrl+C once per remaining payload.
            status_by_source[path.name] = "interrupted"
            logger.warning(
                "Sweep fork %d/%d interrupted: %s (payload=%s) — halting "
                "batch. Resume the partial fork with `optimize` against "
                "that cycle, or re-run --sweep to pick up remaining payloads.",
                len(new_cycle_ids),
                len(sweep_payloads),
                new_cycle_id,
                path.name,
            )
            break
        status_by_source[path.name] = "completed"
        logger.info(
            "Sweep fork %d/%d complete: %s (payload=%s)",
            len(new_cycle_ids),
            len(sweep_payloads),
            new_cycle_id,
            path.name,
        )

    save_active_pointer(tenant_id, root_ctx.session_id, parent_cycle_id)

    # Finalize batch index + render summary.md so the operator has a
    # one-shot read for the whole batch.
    batch_index = json.loads(batch_index_path.read_text(encoding="utf-8"))
    batch_index["status"] = "completed"
    batch_index["completed_at"] = datetime.now(UTC).isoformat()
    cid_by_source = {
        p.name: cid for (p, _), cid in zip(sweep_payloads, new_cycle_ids, strict=False)
    }
    for entry in batch_index["payloads"]:
        if entry["status"] == "pending":
            cid = cid_by_source.get(entry["source_file"], "")
            if cid:
                entry["status"] = status_by_source.get(entry["source_file"], "completed")
            else:
                entry["status"] = "skipped"
            entry["cycle_id"] = cid
    write_json(batch_index_path, batch_index)

    summary_lines = [
        f"# Sweep batch {batch_id}",
        "",
        f"- Parent cycle: `{parent_cycle_id}`",
        f"- Family root: `{family_root}`",
        f"- Started: {batch_index['started_at']}",
        f"- Completed: {batch_index['completed_at']}",
        f"- Forks minted: {len(new_cycle_ids)} of {len(sweep_payloads)}",
        "",
        "## Payloads",
        "",
        "| Source | Status | Cycle |",
        "|---|---|---|",
    ]
    for entry in batch_index["payloads"]:
        summary_lines.append(
            f"| `{entry['source_file']}` | {entry['status']} | `{entry['cycle_id']}` |"
        )
    (batch_dir / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    logger.info(
        "Sweep batch %s complete: %d forks under %s; active pointer restored",
        batch_id,
        len(new_cycle_ids),
        parent_cycle_id,
    )
    return CommandResult(
        data={
            "sweep_batch": new_cycle_ids,
            "batch_id": batch_id,
            "parent_cycle_id": parent_cycle_id,
            "n_forks": len(new_cycle_ids),
        },
        human=(
            f"Sweep batch {batch_id}: {len(new_cycle_ids)} forks under {parent_cycle_id}\n"
            + "\n".join(f"  - {c}" for c in new_cycle_ids)
        ),
    )


async def cmd_optimize(args: argparse.Namespace) -> CommandResult:
    """Run optimization loop. Live state is ``campaigns/{cycle_id}/dashboard.json``;
    digest is ``log.md``; final summary is ``index.json::final``. Stop with Ctrl+C.
    """
    from promptpotter.application.runner import (
        run_optimization as _orch_run_optimization,
    )
    from promptpotter.shared.errors import ResumeDivergenceError

    ctx = load_session(args)
    campaign_config = ctx.campaign_config
    session = await init_services_cli(**ctx.init_params)

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return CommandResult(
            data={"error": "backend_unreachable", "backend_url": ctx.backend_url},
            human=f"Backend unreachable at {ctx.backend_url}. Start the backend and retry.",
        )

    train_data = session.queries or []
    pipeline_params, _baseline = _prepare_cycle_for_optimize(
        args, ctx, session, campaign_config, train_data
    )
    # ctx may have been re-minted; bind the now-resolved ids onto session.
    session.session_id = ctx.session_id
    session.state.cycle_id = ctx.cycle_id

    # Diag-BFS: re-running ``--diag`` against a finalized diag cycle branches
    # off a counted sibling (``{root}_diag_NNN``) instead of overwriting the
    # parent's archive. Each probe is its own cycle with ``parent_cycle_id``
    # set; baseline measurements stay shared via the JSP-keyed archive.
    if (
        getattr(args, "diag", False)
        and ctx.cycle_id
        and getattr(args, "resume_from_round", None) is None
    ):
        existing_index = session.store.campaigns.load(session.backend_id, ctx.cycle_id) or {}
        if (existing_index.get("final") or {}).get("mode") == "diag":
            from promptpotter.application.optimization.cycle import fork_for_diag_sibling

            new_cycle_id = fork_for_diag_sibling(
                session.store.campaigns,
                session.tenant.tenant_id if session.tenant else "default",
                ctx.session_id,
                ctx.cycle_id,
            )
            ctx.cycle_id = new_cycle_id
            session.state.cycle_id = new_cycle_id

    log_startup_summary(
        session,
        pipeline_params,
        len(train_data),
        ctx.backend_url,
        ctx.init_params["dataset_name"],
    )
    session_dir = session.store.sessions.session_dir(ctx.session_id)
    campaign_dir = session.store.campaigns.campaign_dir(ctx.cycle_id)
    logger.info("Session: %s", session_dir)
    logger.info("Campaign: %s", campaign_dir)

    # Multi-fork sweep dispatch: with --sweep AND a non-empty
    # ``datasets/{name}/sweep/*.json`` directory, mint one fork per
    # candidate ``SweepPayload`` and run sweep mode on each. Without the
    # sweep dir, --sweep falls through to the existing single-cycle path
    # (today's behavior, backwards compatible).
    if getattr(args, "sweep", False):
        sweep_dir = _resolve_sweep_dir(ctx.init_params.get("dataset_name"))
        if sweep_dir is not None:
            sweep_payloads = _load_sweep_payloads(sweep_dir)
            if sweep_payloads:
                ctx.save_phase("optimizing")
                return await _run_sweep_batch(
                    args, ctx, campaign_config, train_data, sweep_payloads
                )

    pre_baseline_acc = ctx.state.get("baseline_accuracy", 0.0)
    listener, emitter, _recorder, display = _build_run_observers(
        args, session, campaign_config, campaign_dir, pre_baseline_acc
    )

    ctx.save_phase("optimizing")

    try:
        cycle_result = await _orch_run_optimization(
            train_data,
            campaign_config,
            session=session,
            listener=listener,
            experiment_id=ctx.state["experiment_id"],
            task_context=ctx.task_context,
            display=display,
            resume_from_round_override=getattr(args, "resume_from_round", None),
            emitter=emitter,
            no_divergence_check=getattr(args, "no_divergence_check", False),
            fork_on_divergence=getattr(args, "fork_on_divergence", False),
            sweep=getattr(args, "sweep", False),
            diag=getattr(args, "diag", False),
        )
    except ResumeDivergenceError as div:
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

    ctx.state["best_accuracy"] = cycle_result.best_accuracy
    ctx.save_phase("optimize")
    return CommandResult(
        data=cycle_result.model_dump(),
        human=(
            f"Campaign: {cycle_result.cycle_id}\n"
            f"Dashboard: {campaign_dir / 'dashboard.json'}\n"
            f"Digest: {campaign_dir / 'log.md'}"
        ),
    )


async def cmd_compare(args: argparse.Namespace) -> CommandResult:
    """PoBB-compare cycle winners across the family with adaptive top-up."""
    from promptpotter.application.bootstrap import populate_session_scoring
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.optimization.elevation import elevate_to_decisive
    from promptpotter.application.scoring.formula import split_scoring_block
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store import root_cycle_id

    ctx = load_session(args)
    cycle_ids = list(args.cycle_ids)
    discover_family = args.all_family or not cycle_ids
    session = await init_services_cli(**ctx.init_params)
    session.session_id = ctx.session_id
    session.state.cycle_id = ctx.cycle_id

    campaign_config = ctx.campaign_config
    configure_and_apply_pipeline(
        session, campaign_config, log=logger.info if _VERBOSE else (lambda *_a, **_k: None)
    )

    scoring_spec = split_scoring_block(campaign_config.scoring)
    populate_session_scoring(
        session,
        obs=None,
        scoring_formula=scoring_spec.per_query,
        scoring_round_formula=scoring_spec.per_round,
        scorer_id=scoring_spec.scorer_id,
        experiment_id=ctx.state.get("experiment_id", ""),
        cycle_id=ctx.cycle_id,
        max_consecutive_errors=campaign_config.optimization.max_consecutive_errors,
    )

    if discover_family:
        family_root = root_cycle_id(ctx.cycle_id)
        summaries = session.store.campaigns.list_all(session.backend_id)
        cycle_ids = [
            s["campaign_id"] for s in summaries if root_cycle_id(s["campaign_id"]) == family_root
        ]
        if not cycle_ids:
            return CommandResult(human=f"ERROR: no cycles found under family {family_root}")
        logger.info("compare: discovered %d cycle(s) under family %s", len(cycle_ids), family_root)

    arms: dict[str, JobSearchPoint] = {}
    for cid in cycle_ids:
        idx = session.store.campaigns.load(session.backend_id, cid)
        if idx is None:
            if discover_family:
                logger.info("compare: skipping %s (not found)", cid)
                continue
            return CommandResult(human=f"ERROR: cycle {cid} not found")
        winner_pp = (idx.get("final") or {}).get("winner_pipeline_params")
        if not winner_pp:
            if discover_family:
                logger.info("compare: skipping %s (no final.winner_pipeline_params)", cid)
                continue
            return CommandResult(human=f"ERROR: cycle {cid} has no final.winner_pipeline_params")
        arms[cid] = JobSearchPoint(pipeline_params=winner_pp)

    if not arms:
        return CommandResult(human="ERROR: no cycles with final.winner_pipeline_params to compare")

    result = await elevate_to_decisive(
        arms,
        session,
        session.queries or [],
        epsilon=args.epsilon,
        max_topups=args.max_topups,
        n_min_per_arm=args.n_min_per_arm,
        stream=discover_family or args.max_topups < 0,
    )

    return CommandResult(
        data={
            "decision": result.decision,
            "best_arm": result.best_arm,
            "p_best": result.p_best,
            "topups_per_arm": result.topups_per_arm,
            "score_histories_n": {k: len(v) for k, v in result.score_histories.items()},
            "score_means": {
                k: (sum(v) / len(v) if v else 0.0) for k, v in result.score_histories.items()
            },
        },
        human=result.note,
    )


COMMANDS = {
    "init": cmd_init,
    "optimize": cmd_optimize,
    "compare": cmd_compare,
}


def main() -> None:
    from promptpotter.shared.errors import RequestTooLargeError

    args = build_parser().parse_args()
    set_verbose(bool(getattr(args, "verbose", False)))

    try:
        result = asyncio.run(COMMANDS[args.command](args))
    except RequestTooLargeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if result is None:
        return
    if args.json_output or result.human is None:
        print(json.dumps(result.data, indent=2, default=str))
    else:
        print(result.human)


if __name__ == "__main__":
    main()
