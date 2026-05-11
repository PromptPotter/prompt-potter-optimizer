"""Optimization loop orchestrator — L1 generate → score → escalate, counter-based."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from promptpotter.application.bootstrap import init_optimization_loop
from promptpotter.application.bootstrap.session import Session
from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.escalation import (
    NextAction,
    apply_fork_payload_to_osp,
    escalate_l2,
)
from promptpotter.application.optimization.l1 import execute_round, generate_or_load_candidates
from promptpotter.application.optimization.observers import (
    ForkInfo,
    RunCallbacks,
    RunObservers,
    build_run_observers,
)
from promptpotter.application.origin import (
    CampaignOrigin,
    extract_campaign_origin,
    prepare_scoring_context,
)
from promptpotter.application.scoring.formula import split_scoring_block
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import (
    CampaignPhase,
    StopLoop,
    StopReason,
    emit_phase,
)
from promptpotter.domain.results import CycleResult, RoundResult
from promptpotter.domain.run_records import (
    ForkPayload,
    PhaseRecord,
    ResumeCheckpointRecord,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.presentation.writers import write_log_md, write_review_md
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.projections import LiveDashboardView

logger = logging.getLogger(__name__)

__all__ = [
    "build_origin_cycle_id",
    "cycle_config_identity",
    "run_optimization",
]


def cycle_config_identity(jsp: JobSearchPoint, dataset: list) -> str:
    """Stable identity hash for a feedback cycle's origin ``JobSearchPoint``.

    Covers the rendered prompt, dataset, and full ``pipeline_params`` (active
    steps + per-node target-layer config). Loop-control / strategy knobs on
    ``CampaignConfig`` are deliberately excluded — tweaking optimizer
    strategy or resuming with different budgets does not start a new cycle.
    """
    return f"cycle_{jsp.content_hash(dataset)[:12]}"


def build_origin_cycle_id(
    osp: OptSearchPoint,
    schema: PipelineSchema | None,
    dataset: list,
) -> str:
    """Cycle ID for an origin ``OptSearchPoint`` — the OSP → JSP projection ceremony."""
    base_pp = schema.to_pipeline_params() if schema else {}
    jsp = osp.to_job_search_point(base_pipeline_params=base_pp, schema=schema)
    return cycle_config_identity(jsp, dataset)


async def _escalate_or_stop(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    round_num: int,
    cb: RunCallbacks,
) -> None:
    """Run L2 escalation; raise ``StopLoop`` if it returned a stop reason."""
    stop = await escalate_l2(
        cycle,
        config,
        session.pipeline_schema,
        round_num,
        cb.on_phase,
        obs=session.state.obs,
        tracing_campaign_id=session.state.tracing_campaign_id,
    )
    if stop:
        raise StopLoop(stop)


def _persist_round(
    cycle: Cycle,
    round_result: RoundResult,
    trial_dict: dict,
    round_num: int,
    session: Session,
) -> None:
    """Flush decisions, mirror to ledger, write round_data + log.md/review.md, flush recorder."""
    flushed: list[ResumeCheckpointRecord] = []
    if cycle.pending_decisions:
        flushed = list(cycle.pending_decisions)
        cycle.pending_decisions.clear()
        round_result.decisions.extend(d.to_dict() for d in flushed)
        trial_dict["decisions"] = list(round_result.decisions)

    if (ledger := session.state.ledger) is not None:
        for d in flushed:
            ledger.append(d)
        ledger.append(
            PhaseRecord(
                phase="round",
                event="complete",
                round=round_num,
                payload={
                    "accuracy": round_result.accuracy,
                    "composite_fitness": round_result.composite_fitness,
                    "improved": round_result.improved,
                    "label": round_result.label,
                },
            )
        )

    if session.state.cycle_id:
        with graceful("Round checkpoint failed"):
            session.store.campaigns.save_round_file(
                session.backend_id,
                session.state.cycle_id,
                trial_dict,
            )
        write_log_md(session)
        write_review_md(session, cycle)

    if _rr := session.state.audit_projection:
        _rr.flush()


def _count_positive_yield_axes(cycle: Cycle) -> int | None:
    """Count axes with effect_size above the AxisIndex noise floor.

    Returns ``None`` when AxisIndex isn't initialised (pre-first-round); a
    rule consulting this signal must treat ``None`` as "no evidence yet."
    The threshold mirrors :data:`promptpotter.application.intelligence.indexes.axis.NOISE_THRESHOLD`
    (= 0.02) — same definition the digest formatter uses.
    """
    if cycle.axes is None:
        return None
    from promptpotter.application.intelligence.indexes.axis import NOISE_THRESHOLD

    return sum(1 for r in cycle.axes.axis_rankings() if r.effect_size > NOISE_THRESHOLD)


async def _post_round(
    cycle: Cycle,
    round_result: RoundResult,
    trial_dict: dict,
    round_num: int,
    config: CampaignConfig,
    session: Session,
    dataset: list[Sample],
    cb: RunCallbacks,
) -> None:
    """Normal-path bookkeeping after a non-escalation round. Raises StopLoop on stop condition.

    The state machine observes the round outcome up front (bumping L1 stall,
    deciding CONTINUE / FIRE_L2 / STOP_*); the rest of this function persists
    the post-observe state and dispatches the chosen action.
    """
    axes_with_positive_yield = _count_positive_yield_axes(cycle)
    event = cycle.escalation.observe_round(
        improved=round_result.improved,
        current_accuracy=cycle.tracking.current_accuracy,
        l1_patience=config.optimization.l1_patience,
        enable_l2=config.optimization.enable_l2,
        axes_with_positive_yield=axes_with_positive_yield,
        escalate_on_yield_drought=config.optimization.escalate_on_yield_drought,
    )
    cb.on_round_complete(round_result, cycle.escalation.l1_stall_count)

    _persist_round(cycle, round_result, trial_dict, round_num, session)

    if cycle.axes and session.store and session.backend_id:
        cycle.axes.refresh(
            session.store,
            session.backend_id,
            scorer=session.scoring.scorer,
            scorer_id=session.scoring.scorer_id,
            scorer_formula=session.scoring.scorer_formula,
        )

    if event.stop_reason is not None:
        raise StopLoop(event.stop_reason)
    if event.next_action == NextAction.FIRE_L2:
        await _escalate_or_stop(cycle, config, session, round_num, cb)


async def _run_sweep_generation_only(
    cycle: Cycle,
    session: Session,
    cb: RunCallbacks,
    round_num: int,
    *,
    label: str = "sweep_gen_only",
) -> None:
    """L1 variants without scoring; round_data JSON is minimal status='generation_only'."""
    cb.set_round(round_num)
    if (ledger := session.state.ledger) is not None:
        ledger.append(PhaseRecord(phase="round", event="enter", round=round_num))
    elif _rr := session.state.audit_projection:
        _rr.begin_round(round_num)

    candidates, yield_stats = await generate_or_load_candidates(
        round_num,
        cycle,
        cb.on_phase,
        n_eval_queries=0,
    )

    if session.state.cycle_id:
        with graceful("Sweep generation-only round_data write failed"):
            session.store.campaigns.save_round_file(
                session.backend_id,
                session.state.cycle_id,
                {
                    "round_id": f"round_{round_num}",
                    "round": round_num,
                    "label": label,
                    "status": "generation_only",
                    "accuracy": 0.0,
                    "composite_fitness": 0.0,
                    "hits": 0,
                    "total": 0,
                    "improved": False,
                    "candidates_scored": 0,
                    "candidate_scores": [],
                    "decisions": [],
                    "evaluators": {},
                    "l1_yield": yield_stats.l1_yield,
                    "l1_n_no_op": yield_stats.l1_n_no_op,
                    "l1_n_duplicate": yield_stats.l1_n_duplicate,
                    "opt_search_point": cycle.opt_sp.model_dump(),
                },
            )
        write_log_md(session)
        write_review_md(session, cycle)

    if (ledger := session.state.ledger) is not None:
        ledger.append(
            PhaseRecord(
                phase="round",
                event="complete",
                round=round_num,
                payload={"status": "generation_only", "n_candidates": len(candidates)},
            )
        )
    elif _rr := session.state.audit_projection:
        _rr.flush()


async def _force_l2(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    round_num: int,
    cb: RunCallbacks,
) -> None:
    """Force L2 (bypass stall counter) — diag-mode bridge to round-2 peek."""
    await _escalate_or_stop(cycle, config, session, round_num, cb)


async def _run_round_loop(
    cycle: Cycle,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
    *,
    sweep: bool = False,
    diag: bool = False,
) -> StopReason:
    """Round loop: generate → score → escalate → stop. sweep/diag halt after round 2."""
    opt = config.optimization
    hard_cap = opt.hard_cap
    round_num = session.state.resumed_from_round
    clean_rounds = session.state.resumed_from_round
    max_rounds = opt.max_rounds or 999

    try:
        while clean_rounds < max_rounds and round_num < hard_cap:
            is_probe = cycle.probe_next_round
            if is_probe:
                round_eval_data = [s for s in dataset if s.query in cycle.warned_queries]
                round_checks = None
            else:
                round_eval_data = session.scoring.scoring_set
                round_checks = session.scoring.degradation_checks

            logger.debug(
                "Round %d (clean=%d/%d, acc=%.3f, stall=%d/%d%s)",
                round_num,
                clean_rounds,
                max_rounds,
                cycle.tracking.current_accuracy,
                cycle.escalation.l1_stall_count,
                opt.l1_patience,
                ", PROBE" if is_probe else "",
            )

            # Ledger drives AuditTrailView.begin_round; direct call is
            # kept as no-ledger fallback (test fixtures, headless tools).
            cb.set_round(round_num)
            if (ledger := session.state.ledger) is not None:
                ledger.append(PhaseRecord(phase="round", event="enter", round=round_num))
            elif _rr := session.state.audit_projection:
                _rr.begin_round(round_num)

            round_result = await execute_round(
                cycle,
                round_num,
                round_eval_data,
                cb,
                degradation_checks=round_checks,
                skip_critique=sweep,
            )
            trial_dict = cycle.absorb_round(round_result, round_num)

            if cycle.axes and len(cycle.rounds) >= 2:
                cycle.axes.record_flips_from_rounds(cycle.rounds, round_num)

            if is_probe:
                cycle.probe_next_round = False
                if opt.enable_l2:
                    await _escalate_or_stop(cycle, config, session, round_num, cb)
                round_num += 1
                clean_rounds += 1
                continue

            if round_result.escalation_signal:
                signal = round_result.escalation_signal
                emit_phase(
                    cb.on_phase,
                    CampaignPhase.ESCALATION,
                    "enter",
                    round=round_num,
                    check_name=signal.check_name,
                    target=signal.target,
                    degraded_rate=signal.check_result.get("degraded_rate"),
                    warning_types=signal.check_result.get("warning_types"),
                )
                if signal.routes_to_optimizer and opt.enable_l2:
                    await _escalate_or_stop(cycle, config, session, round_num, cb)
                elif signal.is_abort:
                    raise StopLoop(StopReason.ABORT)
                if session.state.cycle_id:
                    session.store.campaigns.delete_round_candidates(
                        session.backend_id,
                        session.state.cycle_id,
                        round_num + 1,
                    )
                emit_phase(cb.on_phase, CampaignPhase.ESCALATION, "exit", round=round_num)
                round_num += 1
                continue

            await _post_round(
                cycle, round_result, trial_dict, round_num, config, session, dataset, cb
            )
            round_num += 1
            clean_rounds += 1

            if sweep and clean_rounds >= 1:
                await _run_sweep_generation_only(cycle, session, cb, round_num)
                return StopReason.SWEEP_COMPLETE

            if diag and clean_rounds >= 1:
                # Force L2 on round-1 evidence (bypass stall), then peek round 2
                # with L2 overrides. round_num was incremented after _post_round.
                await _force_l2(cycle, config, session, round_num - 1, cb)
                await _run_sweep_generation_only(
                    cycle, session, cb, round_num, label="diag_gen_only"
                )
                return StopReason.DIAG_COMPLETE

        return StopReason.HARD_CAP if round_num >= hard_cap else StopReason.MAX_ROUNDS

    except StopLoop as sl:
        return sl.reason
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning("Optimization interrupted at round %d.", len(cycle.rounds))
        return StopReason.INTERRUPTED


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
        cb = observers.callbacks

    stop_reason = await _run_round_loop(
        cycle, dataset, campaign_config, session, cb, sweep=sweep, diag=diag
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
    _finalize_run(
        cycle, session, observers.dashboard, cycle_result, campaign_config, sweep=sweep, diag=diag
    )
    return cycle_result


def _finalize_run(
    cycle: Cycle,
    session: Session,
    emitter: LiveDashboardView | None,
    cycle_result: CycleResult,
    campaign_config: CampaignConfig,
    *,
    sweep: bool = False,
    diag: bool = False,
) -> None:
    """Mark cycle finished, fold summary into index.json::final, render log.md, finalize emitter."""
    stop_reason = cycle_result.stop_reason
    if session.state.cycle_id:
        status_map = {
            str(StopReason.INTERRUPTED): "interrupted",
        }
        session.store.campaigns.mark_finished(
            session.backend_id,
            session.state.cycle_id,
            status=status_map.get(stop_reason, "completed"),
            stop_reason=stop_reason,
            best_accuracy=cycle_result.best_accuracy,
            best_round=cycle_result.best_round,
            n_rounds=cycle_result.n_rounds,
            finished_at=cycle_result.finished_at,
        )
    if emitter is not None:
        emitter.mark_stopped(str(stop_reason or ""))

    obs = session.state.obs
    if obs:
        obs.end_campaign(
            session.state.tracing_campaign_id,
            best_accuracy=cycle_result.best_accuracy,
            n_rounds=cycle_result.n_rounds,
            stop_reason=stop_reason,
            best_round=cycle_result.best_round,
        )

    cycle_result.langfuse_trace_id = (
        None
        if stop_reason == StopReason.INTERRUPTED or obs is None
        else obs.get_langfuse_trace_id(session.state.tracing_campaign_id)
    )

    if session.state.cycle_id:
        with graceful("Final summary write failed"):
            from promptpotter.application.scoring.evaluators import (
                default_per_round_formula,
                default_per_round_formula_short,
            )

            schema = session.pipeline_schema
            if session.scoring.scorer_round_formula:
                formula_full: str | None = session.scoring.scorer_round_formula
                formula_short: str | None = None
            elif schema is not None:
                formula_full = default_per_round_formula(schema)
                formula_short = default_per_round_formula_short(schema)
            else:
                formula_full = None
                formula_short = None
            origin_composite_fitness = cycle.tracking.origin_composite_fitness
            final_block = cycle_result.model_dump(exclude={"rounds"}, mode="json")
            final_block["scorer_round_formula"] = formula_full
            final_block["scorer_round_formula_short"] = formula_short
            final_block["origin_composite_fitness"] = origin_composite_fitness
            from promptpotter.application.optimization.llm_call import (
                compute_optimizer_prompt_hashes,
            )

            final_block["prompt_hashes"] = compute_optimizer_prompt_hashes()
            if sweep:
                final_block["mode"] = "sweep"
            elif diag:
                final_block["mode"] = "diag"
            else:
                final_block["mode"] = "full"
            if diag:
                # L2-evolved L1 surface that operator promotes by running
                # plain optimize on the diag fork.
                osp = cycle.opt_sp
                final_block["diag"] = {
                    "l1_layout": osp.l1_layout.model_dump(),
                }
            # Seal top-level origin against final.origin_accuracy drift.
            session.store.campaigns.update(
                session.backend_id,
                session.state.cycle_id,
                {"final": final_block, "origin_accuracy": cycle_result.origin_accuracy},
            )

    if emitter:
        from promptpotter.application.intelligence.hard_sample_sorter import (
            build_hard_samples_artifact,
        )

        exp_cfg = campaign_config.optimization.exploration
        artifact: dict | None = None
        if exp_cfg.hard_sample_sorter_enabled:
            with graceful("Hard-sample artifact build failed"):
                artifact = build_hard_samples_artifact(
                    cycle.rounds,
                    cycle_id=session.state.cycle_id,
                    top_k_candidates=exp_cfg.top_k_candidates,
                    top_k_samples=exp_cfg.top_k_samples,
                    posterior=cycle.last_rasch_posterior,
                )
        write_log_md(session, hard_samples_artifact=artifact)
        write_review_md(session, cycle)
