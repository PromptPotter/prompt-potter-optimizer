"""Optimization loop orchestrator.

Runs L1 generate → L1 score in a counter-based loop; each round generates
Layer 1 variants and evaluates them against the current best.

Stopping: ``max_rounds``, ``patience`` exhausted, or perfect score.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from promptpotter.application.campaign.callbacks import RunListener
from promptpotter.application.campaign.campaign_setup import SessionEnv
from promptpotter.application.campaign.config import CampaignConfig, run_preflight_checks
from promptpotter.application.campaign.data import CampaignBaseline
from promptpotter.application.datasets.builder import sample_dataset
from promptpotter.application.intelligence.search_memory import SearchMemory
from promptpotter.application.optimization.loop_env import LoopEnv
from promptpotter.application.optimization.loop_state import LoopState
from promptpotter.application.optimization.nodes.critique import sample_thinking_styles
from promptpotter.application.optimization.nodes.escalation import (
    EscalationTarget,
    build_degradation_checks,
    escalate_l2,
)
from promptpotter.application.optimization.nodes.round_execution import (
    PauseForReviewError,
    execute_round,
    update_round_state,
)
from promptpotter.application.optimization.phases import (
    CampaignPhase,
    StopLoop,
    StopReason,
    emit_phase,
)
from promptpotter.application.optimization.pipeline import get_round_recorder
from promptpotter.application.optimization.results import RoundResult, RunResult
from promptpotter.application.scoring.zero_signal_filter import apply_zero_signal_exclusions
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.scoring import ScoringEnv
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.persistence.session_emitter import CampaignPersistenceEmitter
from promptpotter.infrastructure.store.campaign_store import CampaignStore
from promptpotter.infrastructure.tracing import ObservabilityBridge
from promptpotter.shared.errors import graceful
from promptpotter.shared.scoring import compile_round_scorer

logger = logging.getLogger(__name__)

__all__ = ["run_optimization"]


_CAMPAIGN_STATUS_BY_STOP: dict[StopReason, str] = {
    StopReason.PAUSED_FOR_REVIEW: "paused",
    StopReason.USER_PAUSED: "paused",
    StopReason.USER_STOPPED: "stopped",
    StopReason.INTERRUPTED: "interrupted",
}


# ---------------------------------------------------------------------------
# Escalation dispatch
# ---------------------------------------------------------------------------


async def _try_escalate_l2(
    state: LoopState,
    env: LoopEnv,
    config: CampaignConfig,
    round_num: int,
    cb: RunListener,
    *,
    pipeline_schema: Any = None,
    from_degradation: bool = False,
    esc_check_result: dict | None = None,
) -> None:
    """Single entry point for all ``escalate_l2`` calls.

    Raises ``StopLoop`` if L2/L3 says to stop.
    """
    obs = env.scoring_ctx.obs if env.scoring_ctx else None
    stop = await escalate_l2(
        state,
        config,
        pipeline_schema,
        round_num,
        cb.on_phase,
        on_checkpoint=cb.on_checkpoint,
        obs=obs,
        obs_campaign_id=env.obs_campaign_id,
        from_degradation=from_degradation,
        escalation_check_result=esc_check_result,
    )
    if stop:
        raise StopLoop(stop)


async def _handle_escalation_signal(
    state: LoopState,
    env: LoopEnv,
    config: CampaignConfig,
    session: SessionEnv,
    round_result: RoundResult,
    round_num: int,
    cb: RunListener,
) -> None:
    """Handle a degradation escalation signal from a round result."""
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

    esc_check_result = signal.check_result

    if (
        signal.target in (EscalationTarget.L2, EscalationTarget.L3)
        and config.optimization.enable_l2
    ):
        state.opt_sp.memory.record_escalation_event(
            round_num,
            esc_check_result,
            state.current_sp.pipeline_params if state.current_sp else None,
        )
        try:
            await _try_escalate_l2(
                state,
                env,
                config,
                round_num,
                cb,
                pipeline_schema=session.pipeline_schema,
                from_degradation=True,
                esc_check_result=esc_check_result,
            )
        except StopLoop:
            raise
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception:
            logger.warning("L2 escalation failed", exc_info=True)
    elif signal.target == EscalationTarget.ABORT_CAMPAIGN:
        # Elimination signals (target=ELIMINATE_CANDIDATE) are absorbed inside
        # _score_candidates — only true degradation that L2/L3 cannot rescue
        # should ever reach this branch.
        raise StopLoop(StopReason.ABORT)

    # Common escalation exit (L2 continued, retry, or L2 disabled)
    if env.campaign_store and env.cycle_id:
        env.campaign_store.delete_round_candidates(
            session.backend_id,
            env.cycle_id,
            round_num + 1,
        )
    emit_phase(cb.on_phase, CampaignPhase.ESCALATION, "exit", round=round_num)


# ---------------------------------------------------------------------------
# Round loop
# ---------------------------------------------------------------------------


def _trial_entry(state: LoopState, rr: RoundResult, round_num: int) -> dict[str, Any]:
    """Build the checkpoint dict for ``campaign_store.add_trial``.

    ``all_candidate_results`` + ``decisions`` make the trial self-contained
    for divergence replay: the resume flow rescores everything under the
    current scorer and re-runs registered replayers (see
    ``application/campaign/decisions.py``).
    """
    return {
        "trial_id": f"round_{round_num}",
        "round": round_num,
        "label": rr.label,
        "accuracy": rr.accuracy,
        "composite": rr.composite,
        "hits": rr.hits,
        "total": rr.total,
        "improved": rr.improved,
        "prompt_fields": rr.prompt_fields,
        "results": rr.results,
        "all_candidate_results": dict(rr.all_candidate_results),
        "candidates_scored": rr.candidates_scored,
        "candidate_scores": list(rr.candidate_scores),
        "decisions": list(rr.decisions),
        "evaluators": dict(rr.evaluators),
        **state.escalation.to_checkpoint_dict(),
        "opt_search_point": state.opt_sp.model_dump(),
    }


def _maybe_apply_zero_signal_filter(
    state: LoopState,
    env: LoopEnv,
    config: CampaignConfig,
    session: SessionEnv,
    round_num: int,
    dataset: list[dict[str, Any]],
    cb: RunListener,
) -> None:
    """Prune always-hit/always-miss queries from the active dataset.

    Off by default — gated on ``config.optimization.zero_signal_filter_enabled``.
    When the filter fires, excluded queries are physically moved to the
    dataset's ``excluded`` sidelist on disk AND removed from the
    in-memory ``dataset`` list so the next round sees the smaller set.
    """
    if not config.optimization.zero_signal_filter_enabled:
        return
    dataset_name = session.dataset_name
    if not dataset_name:
        return
    memory = state.search_memory
    if memory is None or env.scoring_ctx is None or env.scoring_ctx.store is None:
        return

    with graceful("Zero-signal filter failed"):
        excluded = apply_zero_signal_exclusions(
            store=env.scoring_ctx.store,
            dataset_name=dataset_name,
            memory=memory,
            active_dataset=dataset,
            min_observations=config.optimization.zero_signal_filter_min_observations,
            campaign_id=env.cycle_id or "",
        )
        if excluded:
            # Also prune any sampled scoring dataset the next round will read.
            excluded_queries = {e["query"] for e in excluded}
            env.scoring_dataset[:] = [
                d for d in env.scoring_dataset if d.get("query", "") not in excluded_queries
            ]
            always_miss = sum(1 for e in excluded if e["hit_rate"] == 0.0)
            always_hit = len(excluded) - always_miss
            emit_phase(
                cb.on_phase,
                "zero_signal_filter",
                "applied",
                round=round_num,
                count=len(excluded),
                always_miss=always_miss,
                always_hit=always_hit,
                examples=[e["query"] for e in excluded[:3]],
                dataset_name=dataset_name,
            )


async def _post_round(
    state: LoopState,
    env: LoopEnv,
    round_result: RoundResult,
    round_num: int,
    config: CampaignConfig,
    session: SessionEnv,
    dataset: list[dict[str, Any]],
    cb: RunListener,
) -> None:
    """Normal-path bookkeeping after a non-escalation round.

    Raises ``StopLoop`` if any stopping condition fires.
    """
    state.escalation.l1_stall_count = (
        0 if round_result.improved else state.escalation.l1_stall_count + 1
    )
    if round_result.improved:
        state.opt_sp.memory.clear_volatile()

    cb.on_round_complete(round_result, state.escalation.l1_stall_count)

    # Flush any escalation decisions accumulated at end of the *previous*
    # round (patience-triggered L2/L3, probe commits, degradation-triggered
    # escalations) into this round's trial so divergence replay sees them.
    if state.pending_decisions:
        round_result.decisions.extend(state.pending_decisions)
        state.pending_decisions.clear()

    if env.campaign_store and env.cycle_id:
        with graceful("Round checkpoint failed"):
            env.campaign_store.add_trial(
                session.backend_id,
                env.cycle_id,
                _trial_entry(state, round_result, round_num),
            )

    _rr = get_round_recorder()
    if _rr:
        _rr.flush()

    # Bidirectional control — user may pause/stop via dashboard
    _ctrl = cb.on_checkpoint("after_round")
    if _ctrl == "pause":
        raise PauseForReviewError([], round_num, pause_point="user_pause")
    if _ctrl == "stop":
        raise StopLoop(StopReason.USER_STOPPED)

    if state.search_memory:
        state.search_memory.on_round_complete(state, env, config, round_num, dataset)
        _maybe_apply_zero_signal_filter(state, env, config, session, round_num, dataset, cb)

    # Stopping conditions
    if state.current_accuracy >= 1.0:
        raise StopLoop(StopReason.PERFECT)
    if state.escalation.l1_stall_count >= config.optimization.l1_patience:
        if not config.optimization.enable_l2:
            raise StopLoop(StopReason.PATIENCE)
        await _try_escalate_l2(
            state, env, config, round_num, cb, pipeline_schema=session.pipeline_schema
        )
        state.escalation.l1_stall_count = 0  # L2/L3 changed prompt — fresh window


async def _run_round_loop(
    state: LoopState,
    env: LoopEnv,
    dataset: list[dict[str, Any]],
    config: CampaignConfig,
    session: SessionEnv,
    cb: RunListener,
) -> StopReason:
    """Execute the round loop: generate → score → escalate → stop."""
    opt = config.optimization
    hard_cap = opt.hard_cap
    round_num = env.resumed_from_round
    clean_rounds = env.resumed_from_round
    max_rounds = opt.max_rounds or 999

    try:
        while clean_rounds < max_rounds and round_num < hard_cap:
            is_probe = state.probe_next_round
            if is_probe:
                warned = {
                    q for q, e in state.opt_sp.memory.warning_inventory.items() if e.get("warnings")
                }
                round_eval_data = [d for d in dataset if d.get("query") in warned]
                round_checks = None
            else:
                round_eval_data, round_checks = env.scoring_dataset, env.degradation_checks

            logger.debug(
                "Round %d (clean=%d/%d, acc=%.3f, stall=%d/%d%s)",
                round_num,
                clean_rounds,
                max_rounds,
                state.current_accuracy,
                state.escalation.l1_stall_count,
                opt.l1_patience,
                ", PROBE" if is_probe else "",
            )

            _rr = get_round_recorder()
            if _rr:
                _rr.begin_round(round_num)

            round_result = await execute_round(
                round_num,
                state,
                env,
                round_eval_data,
                config,
                session,
                cb,
                degradation_checks=round_checks,
                search_memory=state.search_memory,
            )
            update_round_state(state, round_result, round_num, schema=session.pipeline_schema)

            if state.search_memory and len(state.rounds) >= 2:
                state.search_memory.record_flips_from_rounds(state.rounds, round_num)

            if is_probe:
                state.probe_next_round = False
                if opt.enable_l2:
                    await _try_escalate_l2(
                        state, env, config, round_num, cb, pipeline_schema=session.pipeline_schema
                    )
                round_num += 1
                clean_rounds += 1
                continue

            if round_result.escalation_signal:
                await _handle_escalation_signal(
                    state, env, config, session, round_result, round_num, cb
                )
                round_num += 1  # degradation rounds don't count toward max_rounds
                continue

            await _post_round(state, env, round_result, round_num, config, session, dataset, cb)
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
        logger.warning("Optimization interrupted at round %d.", len(state.rounds))
        return StopReason.INTERRUPTED


# ---------------------------------------------------------------------------
# Init: cycle resume, obs, scoring env, search memory
# ---------------------------------------------------------------------------


def _resume_with_divergence_check(
    campaign_store: CampaignStore,
    backend_id: str,
    cycle_id: str,
    resumed_from_round: int,
    scoring_ctx: ScoringEnv,
    state: LoopState,
    *,
    skip_divergence_check: bool,
) -> None:
    """Rescore prior trials under the active scorer and halt on first divergence.

    Side effects on clean resume:

    - Mutates each loaded trial's ``results`` and
      ``all_candidate_results`` in place by rescoring via
      :func:`rescore_results` — this is the boundary where persisted
      stale views get replaced with the current-policy projection.
    - Calls ``state.restore_from_trial`` with the last trial.

    On divergence: writes ``campaigns/{cycle_id}/fork_hint.json`` so
    ``fork`` can mint a branched cycle rooted at the divergence point,
    then raises :class:`ResumeDivergenceError`.

    ``skip_divergence_check`` keeps the rescoring (always correct) but
    silences the halt — the opt-out behind ``--no-divergence-check``.
    """
    from promptpotter.application.campaign.decisions import replay_decisions
    from promptpotter.infrastructure.store.base import write_json
    from promptpotter.shared.errors import ResumeDivergenceError
    from promptpotter.shared.scoring import rescore_results

    prior = campaign_store.load_trials_range(backend_id, cycle_id, 0, resumed_from_round - 1)
    for t in prior:
        rescore_results(
            list(t.get("results") or []),
            scoring_ctx.scorer,
            scoring_ctx.scorer_id,
            scoring_ctx.scorer_formula,
        )
        for items in (t.get("all_candidate_results") or {}).values():
            rescore_results(
                list(items or []),
                scoring_ctx.scorer,
                scoring_ctx.scorer_id,
                scoring_ctx.scorer_formula,
            )

    if not skip_divergence_check:
        for i, t in enumerate(prior):
            div = replay_decisions(t, prior_trials=prior[:i])
            if div is None:
                continue
            fork_hint = {
                "cycle_id": cycle_id,
                "fork_from_round": div.round_num,
                "divergence": {
                    "kind": div.kind,
                    "recorded_outcome": div.recorded_outcome,
                    "current_outcome": div.current_outcome,
                    "inputs_ref": dict(div.inputs_ref),
                },
                "scorer_id": scoring_ctx.scorer_id,
                "scorer_formula": scoring_ctx.scorer_formula,
            }
            write_json(
                campaign_store.campaign_dir(cycle_id) / "fork_hint.json",
                fork_hint,
            )
            raise ResumeDivergenceError(
                round_num=div.round_num,
                kind=div.kind,
                recorded_outcome=div.recorded_outcome,
                current_outcome=div.current_outcome,
                diagnostics={
                    "scorer_id": scoring_ctx.scorer_id,
                    "fork_hint": "run `python -m promptpotter fork` to branch here",
                },
            )

    if prior:
        state.restore_from_trial(prior[-1])


async def _init_optimization(
    baseline: CampaignBaseline,
    dataset: list[dict[str, Any]],
    config: CampaignConfig,
    *,
    cb: RunListener,
    task_context: TaskDecomposition,
    scoring_formula: str | None,
    scoring_round_formula: str | None,
    scorer_id: str,
    no_divergence_check: bool,
    langfuse_session_id: str | None,
    cycle_id: str | None,
    resume_from_round_override: int | None,
    experiment_id: str,
    session: SessionEnv,
    started_at: str,
) -> tuple[LoopState, LoopEnv]:
    """Build LoopState + LoopEnv: baseline, cycle resume, obs, scoring env, search memory."""
    opt = config.optimization
    preflight_warnings = run_preflight_checks(config, dataset)
    for w in preflight_warnings:
        logger.warning("preflight[%s]: %s — %s", w.code, w.title, w.detail)
    emit_phase(
        cb.on_phase,
        CampaignPhase.INIT,
        "enter",
        config=config,
        dataset=dataset,
        env=session,
        warnings=preflight_warnings,
    )

    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)
    if baseline.baseline_ps is None:
        raise ValueError("baseline.baseline_ps is required; run baseline evaluation first.")

    baseline_osp = OptSearchPoint.from_prompt_fields(baseline.baseline_ps)
    baseline_round_scorer = (
        compile_round_scorer(scoring_round_formula) if scoring_round_formula else None
    )
    state = LoopState.from_baseline(
        baseline_osp,
        baseline.baseline_acc,
        task_context=task_context,
        schema=session.pipeline_schema,
        baseline_results=baseline.baseline_results,
        round_scorer=baseline_round_scorer,
    )

    active_steps = list(session.pipeline_schema.active_steps) if session.pipeline_schema else []
    campaign_store, resolved_cycle_id, resumed_from_round = CampaignStore.bootstrap_cycle(
        config,
        session,
        baseline_osp.render(),
        baseline.baseline_acc,
        dataset,
        active_steps,
        cycle_id,
        parent_session_id=session.session_id,
        resume_from_round_override=resume_from_round_override,
    )

    obs_campaign_id = resolved_cycle_id or f"campaign_{started_at[:19].replace(':', '')}"
    obs = ObservabilityBridge.start_campaign(
        session.project_root,
        session.backend_id,
        config_snapshot=config.model_dump(mode="json"),
        baseline_accuracy=baseline.baseline_acc,
        dataset=dataset,
        obs_campaign_id=obs_campaign_id,
        langfuse_session_id=langfuse_session_id or resolved_cycle_id,
    )

    scoring_ctx = ScoringEnv.for_loop(
        session.backend_client,
        session.store,
        session.backend_id,
        session.pipeline_schema,
        obs,
        experiment_id,
        resolved_cycle_id,
        max_consecutive_errors=opt.max_consecutive_errors,
        stale_data_load_protocol=opt.stale_data_load_protocol,
        scoring_formula=scoring_formula,
        scoring_round_formula=scoring_round_formula,
        scorer_id=scorer_id,
    )

    if resumed_from_round > 0 and campaign_store and resolved_cycle_id:
        _resume_with_divergence_check(
            campaign_store,
            session.backend_id,
            resolved_cycle_id,
            resumed_from_round,
            scoring_ctx,
            state,
            skip_divergence_check=no_divergence_check,
        )
    else:
        # Only seed thinking_styles on fresh init — restore_from_trial's
        # value must win on resume, so don't overwrite it there.
        state.opt_sp.memory.thinking_styles = sample_thinking_styles(n=3, seed=opt.seed)
    if session.store:
        session.store.dataset_runs.register_prompt_alias(
            session.backend_id, baseline.instruction, baseline_osp.render()
        )

    search_memory = SearchMemory.ensure_for(
        session.store,
        session.backend_id,
        scorer=scoring_ctx.scorer,
        scorer_id=scoring_ctx.scorer_id,
        scorer_formula=scoring_ctx.scorer_formula,
    )
    state.search_memory = search_memory
    if search_memory:
        scoring_ctx.search_memory = search_memory

    env = LoopEnv(
        scoring_ctx=scoring_ctx,
        campaign_store=campaign_store,
        cycle_id=resolved_cycle_id,
        obs_campaign_id=obs_campaign_id,
        scoring_dataset=sample_dataset(dataset, config.sp_budget_ttest, opt.seed),
        degradation_checks=build_degradation_checks(config),
        resumed_from_round=resumed_from_round,
    )

    emit_phase(
        cb.on_phase,
        CampaignPhase.INIT,
        "exit",
        state=state,
        env=env,
        config=config,
        dataset=dataset,
    )
    return state, env


def _finalize_run(
    state: LoopState,
    env: LoopEnv,
    session: SessionEnv,
    emitter: CampaignPersistenceEmitter | None,
    stop_reason: StopReason,
    finished_at: str,
) -> str | None:
    """Finalize store, obs logger, and emitter; return cloud trace id (or None)."""
    if env.campaign_store and env.cycle_id:
        env.campaign_store.mark_finished(
            session.backend_id,
            env.cycle_id,
            status=_CAMPAIGN_STATUS_BY_STOP.get(stop_reason, "completed"),
            stop_reason=stop_reason,
            best_accuracy=state.best_accuracy,
            best_round=state.best_round,
            n_rounds=len(state.rounds),
            finished_at=finished_at,
        )
    obs: ObservabilityBridge | None = env.scoring_ctx.obs if env.scoring_ctx else None
    if obs:
        obs.end_campaign(
            env.obs_campaign_id,
            best_accuracy=state.best_accuracy,
            n_rounds=len(state.rounds),
            stop_reason=stop_reason,
            best_round=state.best_round,
        )
    if emitter:
        emitter.finalize(
            n_rounds=len(state.rounds),
            best_accuracy=state.best_accuracy,
            best_round=state.best_round,
            stop_reason=stop_reason,
            cycle_id=env.cycle_id,
        )
    if stop_reason in (StopReason.INTERRUPTED, StopReason.PAUSED_FOR_REVIEW) or obs is None:
        return None
    return obs.get_langfuse_trace_id(env.obs_campaign_id)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_optimization(
    dataset: list[dict[str, Any]],
    campaign_config: CampaignConfig,
    *,
    baseline: CampaignBaseline,
    session: SessionEnv,
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
    """Run the full optimization loop from a prepared baseline.

    Callers must provide an already-scored ``baseline`` (see
    ``prepare_scoring_context``). Returns ``RunResult`` — never None; any
    interrupt is reflected in ``stop_reason``.
    """
    started_at = datetime.now(UTC).isoformat()

    active_steps = list(session.pipeline_schema.active_steps) if session.pipeline_schema else []

    if not session_id:
        from promptpotter.application.campaign.session_state import auto_mint_session
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
        # Use the minted cycle if the caller didn't pin one explicitly.
        cycle_id = cycle_id or minted_cycle_id
    session.session_id = session_id
    if cycle_id:
        session.cycle_id = cycle_id

    from promptpotter.shared.scoring import split_scoring_block

    scoring_spec = split_scoring_block(campaign_config.scoring)
    scoring_formula = scoring_spec.per_query
    scoring_round_formula = scoring_spec.per_round

    if isinstance(task_context, TaskDecomposition):
        resolved_task_context = task_context
    elif isinstance(task_context, dict):
        resolved_task_context = TaskDecomposition.from_dict(task_context)
    else:
        resolved_task_context = TaskDecomposition()

    cb = RunListener(display=display, control=control)

    state, env = await _init_optimization(
        baseline,
        dataset,
        campaign_config,
        cb=cb,
        task_context=resolved_task_context,
        scoring_formula=scoring_formula,
        scoring_round_formula=scoring_round_formula,
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
            env.cycle_id,
            project_root=session.project_root,
            session_id=session.session_id,
            max_rounds=opt.max_rounds or 999,
            l1_patience=opt.l1_patience,
            active_nodes=active_steps,
            model=campaign_config.optimizer_llm.model or "",
            n_variants=opt.n_variants,
            sp_budget_ttest=campaign_config.sp_budget_ttest,
            pause_before_scoring=opt.pause_before_scoring,
            resumed_from_round=env.resumed_from_round,
        )
    cb.emitter = emitter

    stop_reason = await _run_round_loop(state, env, dataset, campaign_config, session, cb)

    finished_at = datetime.now(UTC).isoformat()
    langfuse_trace_id = _finalize_run(state, env, session, emitter, stop_reason, finished_at)

    run_result = RunResult(
        rounds=state.rounds,
        n_rounds=len(state.rounds),
        best_accuracy=state.best_accuracy,
        best_round=state.best_round,
        baseline_accuracy=baseline.baseline_acc,
        winner_prompt_fields=state.opt_sp.prompt_field_dict() if state.best_sp else {},
        winner_pipeline_params=state.best_sp.pipeline_params if state.best_sp else None,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        langfuse_trace_id=langfuse_trace_id,
        cycle_id=env.cycle_id,
        session_id=session_id or None,
        resumed_from_round=env.resumed_from_round,
    )
    if emitter:
        emitter.write_result(run_result)
    return run_result
