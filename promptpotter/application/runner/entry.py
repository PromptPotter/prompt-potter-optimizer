"""``run_optimization`` — optimize-loop entry + teardown.

Wires origin → ``init_optimization_loop`` → ``run_round_loop`` → ``_finalize_run``.
Operator forks stamp L1-surface deltas on the fresh OSP; fork-on-divergence
rebuilds observers + re-seeds ``phase_ctx`` so RoundStartView keeps reading
parent's max_rounds + patience scalars."""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import UTC, datetime
from typing import Any

from promptpotter.application.bootstrap import init_optimization_loop
from promptpotter.application.bootstrap.session import Session
from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.escalation import apply_fork_payload_to_osp
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    cleanup_stub_fork_if_empty,
)
from promptpotter.application.origin import (
    CampaignOrigin,
    extract_campaign_origin,
    prepare_scoring_context,
)
from promptpotter.application.run_observers import (
    ForkInfo,
    RunObservers,
    build_run_observers,
)
from promptpotter.application.runner.loop import run_round_loop
from promptpotter.application.scoring.formula import split_scoring_block
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import CycleResult
from promptpotter.domain.run_records import ForkPayload
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.shared.errors import ResumeDivergenceError

logger = logging.getLogger(__name__)


async def run_optimization(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    session: Session,
    observers: RunObservers,
    origin: CampaignOrigin | None = None,
    experiment_id: str | None = None,
    task_context: TaskDecomposition | dict[str, Any] | None = None,
    langfuse_session_id: str | None = None,
    resume_from_round_override: int | None = None,
    no_divergence_check: bool = False,
    fork_on_divergence: bool = False,
    sweep: bool = False,
    diag: bool = False,
    fork_payload: ForkPayload | None = None,
    halt_at_accuracy: float | None = None,
    max_spend_usd: float | None = None,
) -> CycleResult:
    """End-to-end optimization. *observers* MUST be pre-built (ledger bound before origin).
    *origin* omitted ⇒ scored as phase 0 (CLI); supplied ⇒ reused (notebook path)."""
    started_at = datetime.now(UTC).isoformat()
    cb = observers.callbacks

    if origin is None:
        _, _, campaign_rounds, _ = await prepare_scoring_context(
            session.experiment_extract,
            dataset,
            campaign_config,
            pipeline_params=session.pipeline_params,
            pipeline_schema=session.pipeline_schema,
            svc=session,
            listener=cb,
        )
        origin = extract_campaign_origin(campaign_rounds)
        if observers.display is not None and hasattr(observers.display, "set_origin"):
            observers.display.set_origin(origin.origin_acc)

    scoring_spec = split_scoring_block(campaign_config.scoring)

    if isinstance(task_context, TaskDecomposition):
        resolved_task_context = task_context
    elif isinstance(task_context, dict):
        resolved_task_context = TaskDecomposition.from_dict(task_context)
    else:
        resolved_task_context = TaskDecomposition()

    pre_loop_cycle_id = session.state.cycle_id

    # Outer try/except: init-phase crashes (stale OSP rejected by extra="forbid", etc.) land in CRASHED with stashed traceback.
    cycle: Cycle | None = None
    try:
        cycle = await init_optimization_loop(
            origin,
            dataset,
            campaign_config,
            cb=cb,
            task_context=resolved_task_context,
            scoring_formula=scoring_spec.per_sample,
            scoring_round_formula=scoring_spec.per_round,
            scorer_id=scoring_spec.scorer_id,
            no_divergence_check=no_divergence_check,
            fork_on_divergence=fork_on_divergence,
            langfuse_session_id=langfuse_session_id,
            cycle_id=session.state.cycle_id or None,
            resume_from_round_override=resume_from_round_override,
            experiment_id=experiment_id or "",
            session=session,
            started_at=started_at,
        )

        # Operator forks (sweep, rebase) stamp L1-surface deltas; triggers without deltas skip.
        if fork_payload is not None and fork_payload.l1_layout is not None:
            apply_fork_payload_to_osp(cycle.opt_sp, fork_payload)

        # Fork-on-divergence: rebuild observers around the fork's own ledger.
        forked = (
            pre_loop_cycle_id
            and session.state.cycle_id
            and pre_loop_cycle_id != session.state.cycle_id
        )
        if forked and pre_loop_cycle_id:
            # Carry phase_ctx across the rebuild — INIT.enter (max_rounds, patience, formulas)
            # fired on the parent callbacks and won't re-fire (else RoundStartView reads zeros on every forked round).
            parent_phase_ctx = observers.callbacks._phase_ctx
            observers = build_run_observers(
                session=session,
                campaign_config=campaign_config,
                dataset=dataset,
                display=observers.display,
                resumed_from_round=session.state.resumed_from_round,
                origin_accuracy=origin.origin_acc,
                fork=ForkInfo(
                    parent_cycle_id=pre_loop_cycle_id,
                    parent_dashboard=observers.dashboard,
                ),
            )
            observers.callbacks._phase_ctx = parent_phase_ctx
            cb = observers.callbacks

        def _probe_cycle_spend() -> float:
            if observers.dashboard is None:
                return 0.0
            spend = observers.dashboard.state.get("spend") or {}
            return float(spend.get("total_used_usd") or 0.0)

        stop_reason = await run_round_loop(
            cycle,
            dataset,
            campaign_config,
            session,
            cb,
            sweep=sweep,
            diag=diag,
            halt_at_accuracy=halt_at_accuracy,
            max_spend_usd=max_spend_usd,
            spend_probe=_probe_cycle_spend if max_spend_usd is not None else None,
        )
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        cause = (
            "user-initiated" if isinstance(exc, KeyboardInterrupt) else "programmatic cancellation"
        )
        logger.warning("Optimization interrupted before round loop entered (%s).", cause)
        stop_reason = StopReason.INTERRUPTED
    except ResumeDivergenceError as exc:
        # Operator-recoverable; fix is ``--fork-on-divergence``.
        logger.warning("Resume halted on divergence:\n%s", exc)
        stop_reason = StopReason.DIVERGED
    except Exception:
        session.state.crash_traceback = traceback.format_exc()
        logger.exception("Optimization crashed before round loop entered.")
        stop_reason = StopReason.CRASHED

    finished_at = datetime.now(UTC).isoformat()
    # Init-crash fallback: cycle_id was minted upstream, so mark_finished can still stamp final with traceback.
    if cycle is not None:
        best_sp = cycle.tracking.best_sp
        cycle_result = CycleResult(
            rounds=cycle.rounds,
            n_rounds=len(cycle.rounds),
            best_accuracy=cycle.tracking.best_accuracy,
            best_round=cycle.tracking.best_round,
            origin_accuracy=origin.origin_acc,
            winner_prompt_fields=cycle.opt_sp.prompt_field_dict() if best_sp else {},
            winner_pipeline_params=best_sp.pipeline_params if best_sp else None,
            stop_reason=stop_reason,
            started_at=started_at,
            finished_at=finished_at,
            cycle_id=session.state.cycle_id,
            session_id=session.session_id or None,
            resumed_from_round=session.state.resumed_from_round,
        )
    else:
        cycle_result = CycleResult(
            rounds=[],
            n_rounds=0,
            best_accuracy=0.0,
            best_round=0,
            origin_accuracy=origin.origin_acc,
            winner_prompt_fields={},
            winner_pipeline_params=None,
            stop_reason=stop_reason,
            started_at=started_at,
            finished_at=finished_at,
            cycle_id=session.state.cycle_id,
            session_id=session.session_id or None,
            resumed_from_round=session.state.resumed_from_round,
        )
    _finalize_run(session, observers, cycle_result, sweep=sweep)

    # Stub-fork cleanup: if this run forked during init but never completed a round, delete the
    # empty dir so interrupts between fork-mint and round-1 don't accumulate stubs.
    forked_in_this_run = (
        pre_loop_cycle_id and session.state.cycle_id and pre_loop_cycle_id != session.state.cycle_id
    )
    if forked_in_this_run and cycle_result.n_rounds == 0:
        cleanup_stub_fork_if_empty(
            campaign_store=session.store.campaigns,
            campaign_id=session.campaign_id,
            tenant_id=session.store.tenant_id,
            session_id=session.session_id or "",
            cycle_id=session.state.cycle_id,
            parent_cycle_id=pre_loop_cycle_id,
        )
    return cycle_result


def _finalize_run(
    session: Session,
    observers: RunObservers,
    cycle_result: CycleResult,
    *,
    sweep: bool = False,
) -> None:
    """Mark cycle finished, fold summary into index.json::final, render log.md, drain projections."""
    stop_reason = cycle_result.stop_reason
    is_interrupted = stop_reason == StopReason.INTERRUPTED
    is_crashed = stop_reason == StopReason.CRASHED
    is_render_error = stop_reason == StopReason.RENDER_ERROR
    is_prompt_budget = stop_reason == StopReason.PROMPT_BUDGET
    is_optimizer_timeout = stop_reason == StopReason.OPTIMIZER_TIMEOUT
    # All five reasons leave the round partial. Render-error stashes a traceback like crash does;
    # prompt-budget + optimizer-timeout are graceful (cause is in the log).
    halted_mid_round = (
        is_interrupted or is_crashed or is_render_error or is_prompt_budget or is_optimizer_timeout
    )
    has_traceback = is_crashed or is_render_error
    emitter = observers.dashboard
    if session.state.cycle_id:
        status_map = {
            str(StopReason.INTERRUPTED): "interrupted",
            str(StopReason.CRASHED): "crashed",
            str(StopReason.DIVERGED): "diverged",
            str(StopReason.RENDER_ERROR): "render_error",
            str(StopReason.PROMPT_BUDGET): "prompt_budget",
            str(StopReason.OPTIMIZER_TIMEOUT): "optimizer_timeout",
        }
        # Active round at teardown — surfaces on `interrupted_round` so the operator sees which
        # round is partial without diffing the on-disk tree (works for crash too; traceback is the
        # discriminator).
        interrupted_round = int(observers.callbacks._current_round) if halted_mid_round else None
        # Active exception is gone from sys.exc_info() by now — the except clause stashed the
        # formatted traceback on session.state.crash_traceback before returning.
        crash_traceback = session.state.crash_traceback if has_traceback else None
        cycle_status = status_map.get(stop_reason, "completed")
        # index.json::final — terminal-summary namespace the `potter-l1-meta-campaign` skill gates
        # on; review.md + variant leaderboard read it for the frozen verdict.
        from promptpotter.application.optimization.dispatch.llm_call import (
            compute_optimizer_prompt_hashes,
        )

        rounds = cycle_result.rounds
        rounds_to_95 = next((r.round for r in rounds if r.accuracy >= 0.95), None)
        final_block: dict[str, Any] = {
            "stop_reason": stop_reason,
            "final_accuracy": cycle_result.best_accuracy,
            "rounds_to_95": rounds_to_95,
            "prompt_hashes": compute_optimizer_prompt_hashes(),
            "origin_composite_fitness": (rounds[0].matched_origin_composite if rounds else 0.0),
            "mode": "sweep" if sweep else "full",
        }
        session.store.campaigns.mark_finished(
            session.campaign_id,
            session.state.cycle_id,
            status=cycle_status,
            stop_reason=stop_reason,
            best_accuracy=cycle_result.best_accuracy,
            best_round=cycle_result.best_round,
            n_rounds=cycle_result.n_rounds,
            finished_at=cycle_result.finished_at,
            interrupted_round=interrupted_round,
            crash_traceback=crash_traceback,
            final=final_block,
        )
        if session.campaign_id:
            session.store.campaigns.mark_campaign_finished(
                session.campaign_id,
                status=cycle_status,
                finished_at=cycle_result.finished_at,
            )
    # Drain AFTER mark_stopped so dashboard.json's stopped state is in place before audit settles.
    # `_cycle_was_interrupted` threads `"interrupted": true` into partial round_NNNN.json — true
    # for both Ctrl+C and uncaught-exception teardowns.
    if emitter is not None:
        emitter.mark_stopped(str(stop_reason or ""))
    observers.audit._cycle_was_interrupted = halted_mid_round
    observers.drain_all()

    obs = session.state.obs
    if obs:
        obs.end_campaign(
            session.state.tracing_campaign_id,
            best_accuracy=cycle_result.best_accuracy,
            n_rounds=cycle_result.n_rounds,
            stop_reason=stop_reason,
            best_round=cycle_result.best_round,
        )


__all__ = ["run_optimization"]
