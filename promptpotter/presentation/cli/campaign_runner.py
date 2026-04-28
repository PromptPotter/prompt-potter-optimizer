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
from promptpotter.presentation.cli.session import (
    load_campaign_config,
    load_session,
)

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.application.campaign.config import CampaignConfig

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


def _status_sink(msg: str) -> None:
    if _VERBOSE:
        logger.info(msg)


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
    from promptpotter.application.campaign.campaign_setup import init_services
    from promptpotter.config.logging import setup_logging

    setup_logging(style="full" if _VERBOSE else "cli")
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return await init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        on_status=_status_sink,
        take_over=take_over,
        tenant_id=tenant_id,
    )


def _prepare_cycle(session: Session, campaign_config: CampaignConfig, dataset: list):
    """Apply pipeline → load baseline → compute cycle_id. Returns (pipeline_params, baseline, cycle_id)."""
    from promptpotter.application.campaign.config import configure_and_apply_pipeline
    from promptpotter.application.campaign.data import load_baseline_prompt
    from promptpotter.domain.cycle_identity import cycle_config_identity

    schema = session.pipeline_schema
    pipeline_params = configure_and_apply_pipeline(
        session, campaign_config, log=logger.info if _VERBOSE else (lambda *_a, **_k: None)
    )
    baseline = load_baseline_prompt(
        session.experiment_extract,
        prompt_node_names=schema.prompt_node_names() if schema else [],
        dataset_name=session.dataset_name,
    )
    base_pp = schema.to_pipeline_params() if schema else {}
    jsp = baseline.to_job_search_point(base_pipeline_params=base_pp, schema=schema)
    return pipeline_params, baseline, cycle_config_identity(jsp, dataset)


def build_parser() -> argparse.ArgumentParser:
    """Argparse schema for ``init`` + ``optimize``. Pure data."""
    parser = argparse.ArgumentParser(
        prog="python -m promptpotter",
        description="PromptPotter optimization CLI — init creates a session+cycle, "
        "optimize runs a campaign against it. Reads happen by opening the artifact "
        "tree (sessions/{id}/, campaigns/{cycle_id}/) directly.",
    )
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
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create session+cycle for a dataset")
    p_init.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    p_init.add_argument("--backend-id", default=DEFAULT_BACKEND_ID)
    p_init.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    p_init.add_argument("--dataset-name", default=None)
    p_init.add_argument("--excel-path", default=None)
    p_init.add_argument("--config", default=None, help="Campaign config JSON file")
    p_init.add_argument(
        "--task-file",
        default=None,
        help="Override datasets/<name>/task_description.md",
    )
    p_init.add_argument(
        "--task-text",
        default=None,
        help="Override datasets/<name>/task_description.md inline",
    )

    p_opt = sub.add_parser("optimize", help="Run optimization loop on the active session")
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
    """Mint session+cycle, snapshot resolved pipeline into state, create campaign dir.

    Wraps ``auto_mint_session`` (which writes the session + active pointer)
    with the CLI-only extras: explicit ``init_params`` snapshot for resume,
    pipeline_params + active_steps in state, and ``campaigns.create()``.
    """
    from promptpotter.application.campaign.campaign_setup import auto_mint_session

    session_id, _ = auto_mint_session(
        session,
        campaign_config,
        cycle_hash=cycle_id.removeprefix("cycle_"),
        baseline_prompt_fields=baseline.prompt_field_dict(),
        dataset_size=dataset_count,
        experiment_id=init_params.get("experiment_id"),
    )
    state = session.store.sessions.read(session_id) or {}
    state["init_params"] = dict(init_params)
    state["pipeline_params"] = pipeline_params
    state["active_steps"] = list(pipeline_params.get("steps", []))
    session.store.sessions.update(session_id, state)
    session.store.campaigns.create(
        init_params["backend_id"], cycle_id, {"parent_session_id": session_id}
    )
    session.session_id = session_id
    session.cycle_id = cycle_id
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
    from promptpotter.application.campaign.config import create_llm_client
    from promptpotter.application.optimization.pipeline import decompose_task_context

    if task_file:
        task_description = Path(task_file).read_text(encoding="utf-8")
    elif task_text:
        task_description = task_text
    else:
        default_task_path = Path("datasets") / dataset_name / "task_description.md"
        if not default_task_path.exists():
            return
        task_description = default_task_path.read_text(encoding="utf-8")

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
    from promptpotter.application.campaign.config import load_campaign_config as _load_cfg
    from promptpotter.application.campaign.data import prepare_datasets

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
    from promptpotter.presentation.views.display_primitives import set_display_tags

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


_DIVERGENCE_HINT = (
    "Checked decisions: round_winner, elimination_cut, "
    "l2_escalation_trigger, l3_escalation_trigger. "
    "(probe_round_commitment is recorded but not divergence-gated — "
    "it depends on L2's LLM output, which is invariant under a "
    "pure scorer swap.)\n\n"
    "Rerun with `--fork-on-divergence` to branch a sibling cycle "
    "here under the current scorer, revert "
    "`campaign.json::scoring` to continue the original trajectory, "
    "or pass `--no-divergence-check` to accept the divergence."
)


async def cmd_optimize(args: argparse.Namespace) -> CommandResult:
    """Run optimization loop. Live state is ``campaigns/{cycle_id}/dashboard.json``;
    digest is ``log.md``; final summary is ``index.json::final``. Stop with Ctrl+C.
    """
    from promptpotter.application.campaign.data import build_campaign_emitter
    from promptpotter.application.campaign.runner import (
        RunListener,
    )
    from promptpotter.application.campaign.runner import (
        run_optimization as _orch_run_optimization,
    )
    from promptpotter.infrastructure.persistence.round_recorder import RoundRecorder
    from promptpotter.shared.errors import ResumeDivergenceError

    ctx = load_session(args)
    campaign_config = ctx.campaign_config
    session = await init_services_cli(**ctx.init_params)
    session.session_id = ctx.session_id
    session.cycle_id = ctx.cycle_id

    status = await session.backend_client.check_status()
    if status.get("status") == "unreachable":
        return CommandResult(
            data={"error": "backend_unreachable", "backend_url": ctx.backend_url},
            human=f"Backend unreachable at {ctx.backend_url}. Start the backend and retry.",
        )

    train_data = session.queries or []
    resume_from_round: int | None = getattr(args, "resume_from_round", None)
    pipeline_params, baseline_now, expected_cycle_id = _prepare_cycle(
        session, campaign_config, train_data
    )

    # Pipeline divergence-detect: if pipeline.json (model, temperature, …), baseline
    # prompt, or dataset changed since the active session was init'd, the recomputed
    # cycle hash will no longer match the pointer's cycle_id. Auto-mint a fresh
    # session+cycle so a model swap starts a new campaign root instead of silently
    # mixing measurements from the old model.
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

    # Build emitter + display BEFORE baseline so the dashboard ticks through the
    # BASELINE phase and per-query output reaches the terminal. Without this, the
    # CLI goes dark for the entire BASELINE phase. Recorder is built first so the
    # emitter can hold a direct reference (no callback indirection).
    recorder = RoundRecorder(campaign_dir / "rounds")
    recorder.rehydrate_sticky()
    session.round_recorder = recorder

    pre_baseline_acc = ctx.state.get("baseline_accuracy", 0.0)
    emitter = build_campaign_emitter(
        session,
        campaign_config,
        baseline_accuracy=pre_baseline_acc,
        resumed_from_round=resume_from_round,
        recorder=recorder,
    )
    display = _build_live_display(
        args, session=session, campaign_config=campaign_config, baseline_acc=pre_baseline_acc
    )
    listener = RunListener(emitter=emitter, display=display)

    ctx.save_phase("optimizing")

    try:
        cycle_result = await _orch_run_optimization(
            train_data,
            campaign_config,
            session=session,
            listener=listener,
            experiment_id=ctx.state["experiment_id"],
            task_context=ctx.task_context,
            session_id=ctx.session_id,
            display=display,
            cycle_id=ctx.cycle_id,
            resume_from_round_override=resume_from_round,
            emitter=emitter,
            no_divergence_check=getattr(args, "no_divergence_check", False),
            fork_on_divergence=getattr(args, "fork_on_divergence", False),
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


COMMANDS = {
    "init": cmd_init,
    "optimize": cmd_optimize,
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
