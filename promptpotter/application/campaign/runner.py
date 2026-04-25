"""Optimization loop orchestrator — L1 generate → score → escalate, counter-based."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from promptpotter.application.campaign.callbacks import RunListener
from promptpotter.application.campaign.campaign_setup import (
    Session,
    finalize_optimization_run,
    init_optimization_loop,
)
from promptpotter.application.campaign.config import CampaignConfig
from promptpotter.application.campaign.data import CampaignBaseline
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.layer_escalation import (
    build_escalation_entry,
    escalate_l2,
)
from promptpotter.application.optimization.nodes.l1 import (
    PauseForReviewError,
    execute_round,
)
from promptpotter.application.optimization.pipeline import get_round_recorder
from promptpotter.application.optimization.results import RoundResult, RunResult
from promptpotter.application.scoring.zero_signal_filter import apply_zero_signal_exclusions
from promptpotter.domain.analysis import EscalationTarget
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import (
    CampaignPhase,
    StopLoop,
    StopReason,
    emit_phase,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.persistence.session_emitter import CampaignPersistenceEmitter
from promptpotter.shared.errors import graceful

logger = logging.getLogger(__name__)

__all__ = ["run_optimization"]


async def _escalate_or_stop(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    round_num: int,
    cb: RunListener,
    *,
    from_degradation: bool = False,
    escalation_check_result: dict | None = None,
) -> None:
    """Run L2 escalation; raise ``StopLoop`` if it returned a stop reason."""
    stop = await escalate_l2(
        cycle,
        config,
        session.pipeline_schema,
        round_num,
        cb.on_phase,
        on_checkpoint=cb.on_checkpoint,
        obs=session.obs,
        obs_campaign_id=session.obs_campaign_id,
        from_degradation=from_degradation,
        escalation_check_result=escalation_check_result,
    )
    if stop:
        raise StopLoop(stop)


async def _handle_escalation_signal(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    round_result: RoundResult,
    round_num: int,
    cb: RunListener,
) -> None:
    signal = round_result.escalation_signal
    assert signal is not None
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

    if (
        signal.target in (EscalationTarget.L2, EscalationTarget.L3)
        and config.optimization.enable_l2
    ):
        cycle.opt_sp.memory.append_escalation(
            build_escalation_entry(
                round_num,
                signal.check_result,
                cycle.current_sp.pipeline_params if cycle.current_sp else None,
            )
        )
        await _escalate_or_stop(
            cycle,
            config,
            session,
            round_num,
            cb,
            from_degradation=True,
            escalation_check_result=signal.check_result,
        )
    elif signal.target == EscalationTarget.ABORT_CAMPAIGN:
        raise StopLoop(StopReason.ABORT)

    if session.campaign_store and session.cycle_id:
        session.campaign_store.delete_round_candidates(
            session.backend_id,
            session.cycle_id,
            round_num + 1,
        )
    emit_phase(cb.on_phase, CampaignPhase.ESCALATION, "exit", round=round_num)


async def _post_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    config: CampaignConfig,
    session: Session,
    dataset: list[Sample],
    cb: RunListener,
) -> None:
    """Normal-path bookkeeping after a non-escalation round. Raises StopLoop on stop condition."""
    cycle.escalation.l1_stall_count = (
        0 if round_result.improved else cycle.escalation.l1_stall_count + 1
    )
    if round_result.improved:
        cycle.opt_sp.memory.clear_volatile()

    cb.on_round_complete(round_result, cycle.escalation.l1_stall_count)

    if cycle.pending_decisions:
        round_result.decisions.extend(cycle.flush_decisions())

    if session.campaign_store and session.cycle_id:
        with graceful("Round checkpoint failed"):
            session.campaign_store.add_trial(
                session.backend_id,
                session.cycle_id,
                cycle.checkpoint(round_result, round_num),
            )

    _rr = get_round_recorder()
    if _rr:
        _rr.flush()

    _ctrl = cb.on_checkpoint("after_round")
    if _ctrl == "pause":
        raise PauseForReviewError([], round_num, pause_point="user_pause")
    if _ctrl == "stop":
        raise StopLoop(StopReason.USER_STOPPED)

    if cycle.search_memory:
        cycle.search_memory.on_round_complete(cycle, session, config, round_num, dataset)
        # Round-end zero-signal filter — prune always-hit/always-miss queries from the active set.
        zsf = config.optimization.zero_signal_filter_enabled
        dataset_name = session.dataset_name
        if zsf and dataset_name and session.store is not None:
            with graceful("Zero-signal filter failed"):
                excluded = apply_zero_signal_exclusions(
                    store=session.store,
                    dataset_name=dataset_name,
                    memory=cycle.search_memory,
                    active_dataset=dataset,
                    min_observations=config.optimization.zero_signal_filter_min_observations,
                    campaign_id=session.cycle_id or "",
                )
                if excluded:
                    excluded_queries = {e["query"] for e in excluded}
                    session.scoring_dataset[:] = [
                        s for s in session.scoring_dataset if s.query not in excluded_queries
                    ]
                    always_miss = sum(1 for e in excluded if e["hit_rate"] == 0.0)
                    emit_phase(
                        cb.on_phase,
                        "zero_signal_filter",
                        "applied",
                        round=round_num,
                        count=len(excluded),
                        always_miss=always_miss,
                        always_hit=len(excluded) - always_miss,
                        examples=[e["query"] for e in excluded[:3]],
                        dataset_name=dataset_name,
                    )

    # Round-end Rasch + KG swap of the active scoring prefix.
    ap_cfg = config.optimization.adaptive_prefix
    if ap_cfg.enabled and cycle.rounds:
        from promptpotter.application.intelligence.adaptive_prefix import (
            build_prefix_event,
            evolve_prefix,
        )

        with graceful("AdaptivePrefix evolve failed"):
            ap_result = evolve_prefix(
                full_dataset=dataset,
                current_prefix=session.scoring_dataset,
                rounds=cycle.rounds,
                config=ap_cfg,
                elimination_n_min=config.optimization.elimination_n_min,
            )
            if ap_result.swapped_in or ap_result.swapped_out:
                session.scoring_dataset[:] = ap_result.new_prefix
                event = build_prefix_event(round_num=round_num, result=ap_result)
                cycle.prefix_events.append(event)
                emit_phase(
                    cb.on_phase,
                    "adaptive_prefix",
                    "evolved",
                    round=round_num,
                    swapped_out=event["swapped_out"],
                    swapped_in=event["swapped_in"],
                    new_prefix_size=event["new_prefix_size"],
                )

    if cycle.current_accuracy >= 1.0:
        raise StopLoop(StopReason.PERFECT)
    if cycle.escalation.l1_stall_count >= config.optimization.l1_patience:
        if not config.optimization.enable_l2:
            raise StopLoop(StopReason.PATIENCE)
        await _escalate_or_stop(cycle, config, session, round_num, cb)
        cycle.escalation.l1_stall_count = 0


async def _run_round_loop(
    cycle: Cycle,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunListener,
) -> StopReason:
    """Execute the round loop: generate → score → escalate → stop."""
    opt = config.optimization
    hard_cap = opt.hard_cap
    round_num = session.resumed_from_round
    clean_rounds = session.resumed_from_round
    max_rounds = opt.max_rounds or 999

    try:
        while clean_rounds < max_rounds and round_num < hard_cap:
            is_probe = cycle.probe_next_round
            if is_probe:
                warned = {
                    q for q, e in cycle.opt_sp.memory.warning_inventory.items() if e.get("warnings")
                }
                round_eval_data = [s for s in dataset if s.query in warned]
                round_checks = None
            else:
                round_eval_data, round_checks = session.scoring_dataset, session.degradation_checks

            logger.debug(
                "Round %d (clean=%d/%d, acc=%.3f, stall=%d/%d%s)",
                round_num,
                clean_rounds,
                max_rounds,
                cycle.current_accuracy,
                cycle.escalation.l1_stall_count,
                opt.l1_patience,
                ", PROBE" if is_probe else "",
            )

            _rr = get_round_recorder()
            if _rr:
                _rr.begin_round(round_num)

            round_result = await execute_round(
                cycle,
                round_num,
                round_eval_data,
                cb,
                degradation_checks=round_checks,
            )
            cycle.record_round(round_result, round_num)

            if cycle.search_memory and len(cycle.rounds) >= 2:
                cycle.search_memory.record_flips_from_rounds(cycle.rounds, round_num)

            if is_probe:
                cycle.set_probe(False)
                if opt.enable_l2:
                    await _escalate_or_stop(cycle, config, session, round_num, cb)
                round_num += 1
                clean_rounds += 1
                continue

            if round_result.escalation_signal:
                await _handle_escalation_signal(cycle, config, session, round_result, round_num, cb)
                round_num += 1
                continue

            await _post_round(cycle, round_result, round_num, config, session, dataset, cb)
            round_num += 1
            clean_rounds += 1

        return StopReason.HARD_CAP if round_num >= hard_cap else StopReason.MAX_ROUNDS

    except StopLoop as sl:
        return sl.reason
    except PauseForReviewError as pause:
        logger.info("HITL: paused at %s (round %d).", pause.pause_point, pause.round_num)
        return (
            StopReason.USER_PAUSED
            if pause.pause_point == "user_pause"
            else StopReason.PAUSED_FOR_REVIEW
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning("Optimization interrupted at round %d.", len(cycle.rounds))
        return StopReason.INTERRUPTED


async def run_optimization(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    baseline: CampaignBaseline,
    session: Session,
    experiment_id: str | None = None,
    task_context: TaskDecomposition | dict | None = None,
    session_id: str = "",
    display: Any = None,
    control: Callable[[str], str | None] | None = None,
    langfuse_session_id: str | None = None,
    cycle_id: str | None = None,
    resume_from_round_override: int | None = None,
    emitter: CampaignPersistenceEmitter | None = None,
    no_divergence_check: bool = False,
) -> RunResult:
    """Run the full optimization loop from a prepared baseline. Returns RunResult (never None)."""
    started_at = datetime.now(UTC).isoformat()
    active_steps = list(session.pipeline_schema.active_steps) if session.pipeline_schema else []

    if not session_id:
        from promptpotter.application.campaign.campaign_setup import auto_mint_session
        from promptpotter.domain.cycle_identity import cycle_hash_suffix

        ps = baseline.baseline_ps
        baseline_prompt_fields = (
            ps.prompt_field_dict() if isinstance(ps, OptSearchPoint) else (ps or {})
        )
        baseline_render = OptSearchPoint.from_prompt_fields(baseline_prompt_fields).render()
        session_id, minted_cycle_id = auto_mint_session(
            session,
            campaign_config,
            cycle_hash=cycle_hash_suffix(
                campaign_config,
                baseline_render,
                dataset,
                active_steps,
                strict=campaign_config.optimization.strict_cycle_identity,
            ),
            baseline_acc=baseline.baseline_acc,
            baseline_prompt_fields=baseline_prompt_fields,
            dataset_size=len(dataset),
            experiment_id=experiment_id,
        )
        cycle_id = cycle_id or minted_cycle_id
    session.session_id = session_id
    if cycle_id:
        session.cycle_id = cycle_id

    from promptpotter.shared.scoring import split_scoring_block

    scoring_spec = split_scoring_block(campaign_config.scoring)

    if isinstance(task_context, TaskDecomposition):
        resolved_task_context = task_context
    elif isinstance(task_context, dict):
        resolved_task_context = TaskDecomposition.from_dict(task_context)
    else:
        resolved_task_context = TaskDecomposition()

    cb = RunListener(display=display, control=control)

    cycle = await init_optimization_loop(
        baseline,
        dataset,
        campaign_config,
        cb=cb,
        task_context=resolved_task_context,
        scoring_formula=scoring_spec.per_query,
        scoring_round_formula=scoring_spec.per_round,
        scorer_id=scoring_spec.scorer_id,
        no_divergence_check=no_divergence_check,
        langfuse_session_id=langfuse_session_id,
        cycle_id=cycle_id,
        resume_from_round_override=resume_from_round_override,
        experiment_id=experiment_id or "",
        session=session,
        started_at=started_at,
    )

    if emitter is None:
        opt = campaign_config.optimization
        emitter = CampaignPersistenceEmitter.for_session(
            baseline.baseline_acc,
            session.cycle_id,
            project_root=session.project_root,
            session_id=session.session_id,
            max_rounds=opt.max_rounds or 999,
            l1_patience=opt.l1_patience,
            active_nodes=active_steps,
            model=campaign_config.optimizer_llm.model or "",
            n_variants=opt.n_variants,
            sp_budget_ttest=campaign_config.sp_budget_ttest,
            pause_before_scoring=opt.pause_before_scoring,
            resumed_from_round=session.resumed_from_round,
            dataset_count=len(session.scoring_dataset) if session.scoring_dataset else None,
            backend_id=session.backend_id,
            recorder_provider=get_round_recorder,
        )
    cb.emitter = emitter

    stop_reason = await _run_round_loop(cycle, dataset, campaign_config, session, cb)

    finished_at = datetime.now(UTC).isoformat()
    langfuse_trace_id = finalize_optimization_run(
        cycle, session, emitter, stop_reason, finished_at, campaign_config
    )

    run_result = RunResult(
        rounds=cycle.rounds,
        n_rounds=len(cycle.rounds),
        best_accuracy=cycle.best_accuracy,
        best_round=cycle.best_round,
        baseline_accuracy=baseline.baseline_acc,
        winner_prompt_fields=cycle.opt_sp.prompt_field_dict() if cycle.best_sp else {},
        winner_pipeline_params=cycle.best_sp.pipeline_params if cycle.best_sp else None,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        langfuse_trace_id=langfuse_trace_id,
        cycle_id=session.cycle_id,
        session_id=session_id or None,
        resumed_from_round=session.resumed_from_round,
    )
    if emitter:
        emitter.write_result(run_result)
    return run_result
