"""``run_optimization`` — the optimize-loop entry point + final teardown.

Wires origin → init_optimization_loop → run_round_loop → finalize_run.
Operator-issued forks (sweep, rebase) stamp their L1-surface deltas onto
the fresh OSP; fork-on-divergence rebuilds observers around the new
fork's ledger and re-seeds ``phase_ctx`` so RoundStartView keeps reading
the parent campaign's max_rounds + patience scalars.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from promptpotter.application.bootstrap import init_optimization_loop
from promptpotter.application.bootstrap.session import Session
from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.escalation import apply_fork_payload_to_osp
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

logger = logging.getLogger(__name__)


async def run_optimization(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    session: Session,
    observers: RunObservers,
    origin: CampaignOrigin | None = None,
    experiment_id: str | None = None,
    task_context: TaskDecomposition | dict | None = None,
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
    """Run optimization end-to-end. ``observers`` MUST be pre-built via
    ``build_run_observers`` so the ledger is bound before origin ticks.

    ``origin`` is optional: when omitted, the runner scores origin as
    phase 0 (CLI path); when provided, it's reused as-is (notebook path,
    where origin ran in an earlier cell against the same observers).
    """
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

    # Operator-issued forks (sweep, future rebase): stamp the payload's
    # L1-surface deltas onto the fresh OSP. Triggers without OSP deltas
    # (SCORING_DIVERGENCE always; SCORING-bookkeeping for future triggers)
    # have no l1_layout to apply — skip.
    if fork_payload is not None and fork_payload.l1_layout is not None:
        apply_fork_payload_to_osp(cycle.opt_sp, fork_payload)

    # Fork-on-divergence: rebuild observers around the fork's own ledger.
    forked = (
        pre_loop_cycle_id and session.state.cycle_id and pre_loop_cycle_id != session.state.cycle_id
    )
    if forked and pre_loop_cycle_id:
        # Persist phase_ctx across the rebuild: INIT.enter fired on the
        # parent callbacks (max_rounds, patience, original_sp_flat,
        # composite formulas) and won't re-fire on the new ledger.
        # Without this copy, RoundStartView reads zeros — the operator
        # sees "ROUND N/999" and "patience N/0" on every forked round.
        parent_phase_ctx = dict(observers.callbacks._phase_ctx)
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
        observers.callbacks._phase_ctx.update(parent_phase_ctx)
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

    finished_at = datetime.now(UTC).isoformat()
    cycle_result = CycleResult(
        rounds=cycle.rounds,
        n_rounds=len(cycle.rounds),
        best_accuracy=cycle.tracking.best_accuracy,
        best_round=cycle.tracking.best_round,
        origin_accuracy=origin.origin_acc,
        winner_prompt_fields=cycle.opt_sp.prompt_field_dict() if cycle.tracking.best_sp else {},
        winner_pipeline_params=cycle.tracking.best_sp.pipeline_params
        if cycle.tracking.best_sp
        else None,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        cycle_id=session.state.cycle_id,
        session_id=session.session_id or None,
        resumed_from_round=session.state.resumed_from_round,
    )
    _finalize_run(cycle, session, observers, cycle_result, campaign_config, sweep=sweep, diag=diag)
    return cycle_result


def _finalize_run(
    cycle: Cycle,
    session: Session,
    observers: RunObservers,
    cycle_result: CycleResult,
    campaign_config: CampaignConfig,
    *,
    sweep: bool = False,
    diag: bool = False,
) -> None:
    """Mark cycle finished, fold summary into index.json::final, render log.md, drain projections."""
    del campaign_config, sweep, diag  # reserved for future hooks
    stop_reason = cycle_result.stop_reason
    is_interrupted = stop_reason == StopReason.INTERRUPTED
    emitter = observers.dashboard
    if session.state.cycle_id:
        status_map = {
            str(StopReason.INTERRUPTED): "interrupted",
        }
        # The active round number when the interrupt fired lives on the
        # callbacks (set by ``cb.set_round`` at each iteration). Surfacing
        # it on ``index.json::interrupted_round`` lets the operator see
        # which round is partial without diffing the on-disk tree.
        interrupted_round = int(observers.callbacks._current_round) if is_interrupted else None
        session.store.campaigns.mark_finished(
            session.backend_id,
            session.state.cycle_id,
            status=status_map.get(stop_reason, "completed"),
            stop_reason=stop_reason,
            best_accuracy=cycle_result.best_accuracy,
            best_round=cycle_result.best_round,
            n_rounds=cycle_result.n_rounds,
            finished_at=cycle_result.finished_at,
            interrupted_round=interrupted_round,
        )
    # Drain projections AFTER mark_stopped so dashboard.json's stopped-state
    # is in place before the audit cache settles. ``_cycle_was_interrupted``
    # threads ``"interrupted": true`` into any partial round_NNNN.json the
    # audit projection writes on its way out.
    if emitter is not None:
        emitter.mark_stopped(str(stop_reason or ""))
    observers.audit._cycle_was_interrupted = is_interrupted
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
