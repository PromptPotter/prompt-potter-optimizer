"""
Optimization loop orchestrator for iterative prompt optimization.

Runs ``l1_generate()`` → ``l1_score()`` in a
counter-based loop.  Each round generates Layer 1 variants and evaluates
them against the current best.

Stopping conditions:
- ``max_rounds`` reached
- ``patience`` consecutive non-improving rounds exhausted
- ``winner_accuracy >= 1.0`` (perfect score)
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.services.campaign.campaign_setup import SessionEnv
from promptpotter.services.campaign.config import LoopConfig
from promptpotter.services.campaign.data import extract_campaign_baseline
from promptpotter.services.campaign.lifecycle import finalize_campaign, init_cycle_state
from promptpotter.services.campaign.nodes.escalation import escalate_l2
from promptpotter.services.campaign.nodes.round_execution import (
    PauseForReviewError,
    execute_round,
    update_round_state,
)
from promptpotter.services.campaign.state import (
    CampaignPhase,
    LoopState,
    PhaseEvent,
    RoundResult,
    RunCallbacks,
    RunResult,
    StopReason,
    emit_phase,
    get_obs_trace,
)
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.search.scan_results import ScanBrief
    from promptpotter.services.store.campaign_store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["run_optimization"]


# ---------------------------------------------------------------------------
# Round bookkeeping — query flips, SearchMemory refresh, adaptive eval
# ---------------------------------------------------------------------------


def record_query_flips(state: LoopState, round_num: int) -> None:
    """Record query flips into SearchMemory for improvement attribution."""
    if not state.search_memory or len(state.rounds) < 2:
        return
    prev_round = state.rounds[-2]
    curr_round = state.rounds[-1]
    if not prev_round.results or not curr_round.results:
        return
    desc = (
        curr_round.candidate_scores[0].get("changes_description", "")
        if curr_round.candidate_scores
        else ""
    )
    _flips = state.search_memory.record_query_flips(
        round_num,
        desc,
        prev_round.results,
        curr_round.results,
    )
    if _flips:
        logger.debug("Round %d: %d query flips recorded", round_num, _flips)


def refresh_search_memory(
    state: LoopState,
    config: LoopConfig,
    round_num: int,
) -> None:
    """Refresh SearchMemory from latest dataset runs, save, recompute correlations."""
    if not state.search_memory or not state.scoring_ctx or not state.scoring_ctx.store:
        return

    from pathlib import Path

    _sm_store = state.scoring_ctx.store
    if state.search_memory.refresh(_sm_store, config.backend_id):
        # Periodically recompute failure group correlations
        if (
            round_num > 0
            and round_num % 5 == 0
            and state.search_memory.recompute_failure_group_correlations()
        ):
            logger.info(
                "SearchMemory: recomputed failure group correlations at round %d",
                round_num,
            )
        _sm_path = Path(_sm_store.base_dir) / config.backend_id / "search_memory.json"
        state.search_memory.save(_sm_path)


def try_adapt_eval_set(
    state: LoopState,
    full_dataset: list,
    config: LoopConfig,
    round_num: int,
) -> None:
    """Swap dead queries for discriminating ones based on round history."""
    if round_num < 2:
        return
    _hist = [r.results for r in state.rounds if r.results]
    if len(_hist) < 3:
        return
    from promptpotter.services.campaign.nodes.round_execution import adapt_eval_set
    from promptpotter.services.metrics import compile_query_difficulty

    _qd = compile_query_difficulty(_hist)
    state.scoring_dataset, _adapt = adapt_eval_set(
        state.scoring_dataset,
        _qd,
        full_dataset,
        seed=config.seed + round_num,
    )
    if not _adapt.get("unchanged"):
        logger.info("Adaptive eval: %s", _adapt)


async def _handle_escalation_signal(
    state: LoopState,
    config: LoopConfig,
    round_result: RoundResult,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    obs_campaign_id: str,
    campaign_store: "CampaignStore | None",
    cycle_id: str | None,
    on_checkpoint: Callable[[str], str | None] | None = None,
) -> StopReason | None:
    """Handle a degradation escalation signal from a round result.

    Returns a StopReason if the cycle should stop, None to continue.
    """
    signal = round_result.escalation_signal
    assert signal is not None
    emit_phase(
        on_phase,
        CampaignPhase.ESCALATION,
        "enter",
        round=round_num,
        check_name=signal["check_name"],
        target=signal["target"],
        degraded_rate=signal["context"].get("degraded_rate"),
        warning_types=signal["context"].get("warning_types"),
    )
    logger.debug(
        "Escalation '%s' at round %d — target=%s, degraded_rate=%.1f%%",
        signal["check_name"],
        round_num,
        signal["target"],
        signal["context"].get("degraded_rate", 0) * 100,
    )

    esc_check_result = signal["check_result"]

    from promptpotter.services.campaign.nodes.escalation import EscalationTarget

    # Dispatch by target
    if signal["target"] in (EscalationTarget.L2, EscalationTarget.L3) and config.enable_l2:
        # Fill outcome of previous journal entry (if any)
        journal = state.opt_sp.escalation_journal
        if journal and journal[-1].get("outcome_degraded_rate") is None:
            journal[-1]["outcome_degraded_rate"] = esc_check_result.get("degraded_rate", 0)

        # Record journal entry BEFORE L2 so L2 sees the current event
        # in the journal (not just history). Also ensures the entry is
        # persisted even if L2 returns a stop signal.
        dominant = esc_check_result.get("dominant_warning", "unknown:unknown")
        problem_step = dominant.split(":")[0] if ":" in dominant else "unknown"
        step_cfg = ((state.current_sp.pipeline_params if state.current_sp else None) or {}).get(
            problem_step, {}
        )
        state.opt_sp.escalation_journal.append(
            {
                "round": round_num,
                "degraded_rate": esc_check_result.get("degraded_rate", 0),
                "problem_step": problem_step,
                "step_config": dict(step_cfg) if isinstance(step_cfg, dict) else {},
                "warning_types": esc_check_result.get("warning_types", {}),
                "outcome_degraded_rate": None,
            }
        )

        _obs, _tid = get_obs_trace(state, obs_campaign_id)
        try:
            l2_stop = await escalate_l2(
                state,
                config,
                round_num,
                on_phase,
                on_checkpoint=on_checkpoint,
                obs=_obs,
                trace_id=_tid,
                from_degradation=True,
                escalation_check_result=esc_check_result,
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception:
            logger.warning("L2 escalation failed", exc_info=True)
            l2_stop = None
        if l2_stop:
            return l2_stop
    elif signal["target"] == EscalationTarget.ABORT:
        return StopReason.ABORT

    # Common escalation exit (L2 continued, retry, or L2 disabled)
    if campaign_store and cycle_id:
        campaign_store.delete_round_candidates(
            config.backend_id,
            cycle_id,
            round_num + 1,
        )
    emit_phase(on_phase, CampaignPhase.ESCALATION, "exit", round=round_num)
    return None


def _checkpoint_round(
    campaign_store: "CampaignStore",
    cycle_id: str,
    config: LoopConfig,
    state: LoopState,
    round_result: RoundResult,
    round_num: int,
) -> None:
    """Persist round results to the campaign store."""
    with graceful("Round checkpoint failed"):
        campaign_store.add_trial(
            config.backend_id,
            cycle_id,
            {
                "trial_id": f"round_{round_num}",
                "round": round_num,
                "label": round_result.label,
                "accuracy": round_result.accuracy,
                "composite": round_result.composite,
                "hits": round_result.hits,
                "total": round_result.total,
                "improved": round_result.improved,
                "prompt_fields": round_result.prompt_fields,
                "results": round_result.results,
                "candidates_scored": round_result.candidates_scored,
                "candidate_scores": list(round_result.candidate_scores),
                "stall_count": state.stall_count,
                **state.escalation.to_checkpoint_dict(),
                "opt_search_point": state.opt_sp.model_dump(),
            },
        )


async def _check_stopping_conditions(
    state: LoopState,
    config: LoopConfig,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    obs_campaign_id: str,
    on_checkpoint: Callable[[str], str | None] | None = None,
) -> StopReason | None:
    """Check patience, max_rounds, and perfect score.

    Returns a StopReason if the cycle should stop, None to continue.
    """
    if state.current_accuracy >= 1.0:
        return StopReason.PERFECT
    if state.stall_count >= config.l1_patience:
        if config.enable_l2:
            _obs, _tid = get_obs_trace(state, obs_campaign_id)
            _l2_stop = await escalate_l2(
                state,
                config,
                round_num,
                on_phase,
                on_checkpoint=on_checkpoint,
                obs=_obs,
                trace_id=_tid,
            )
            if _l2_stop:
                return _l2_stop
            state.reset_stall_count()  # L2/L3 changed prompt — fresh window
        else:
            return StopReason.PATIENCE
    return None


async def _run_round_loop(
    state: LoopState,
    dataset: list[dict[str, Any]],
    config: LoopConfig,
    cb: RunCallbacks,
) -> StopReason:
    """Execute the round loop: generate → score → escalate → stop.

    Reads infrastructure from *state* (campaign_store, cycle_id, etc.).
    Returns the ``StopReason`` that ended the loop.
    """
    stop_reason: StopReason | None = None
    hard_cap = config.hard_cap

    try:
        round_num = state.resumed_from_round
        clean_rounds = state.resumed_from_round
        max_rounds = config.max_rounds or 999

        while clean_rounds < max_rounds and round_num < hard_cap:
            # --- Probe vs normal round data ---
            is_probe = state.probe_next_round
            if is_probe:
                warned = {q for q, e in state.opt_sp.warning_inventory.items() if e.get("warnings")}
                round_eval_data = [d for d in dataset if d.get("query") in warned]
                _round_checks = None  # no escalation checks during probe
                logger.debug(
                    "PROBE round %d: %d warned queries (from %d tracked)",
                    round_num,
                    len(round_eval_data),
                    len(warned),
                )
            else:
                round_eval_data = state.scoring_dataset
                _round_checks = state.degradation_checks

            logger.debug(
                "Optimization round %d (clean=%d/%d, acc=%.3f, stall=%d/%d%s)",
                round_num,
                clean_rounds,
                max_rounds,
                state.current_accuracy,
                state.stall_count,
                config.l1_patience,
                ", PROBE" if is_probe else "",
            )

            # Round recorder: begin new round
            from promptpotter.services.optimizer.pipeline import get_round_recorder

            _rr = get_round_recorder()
            if _rr:
                _rr.begin_round(round_num)

            round_result = await execute_round(
                round_num,
                state,
                round_eval_data,
                config,
                state.obs_campaign_id,
                state.campaign_store,
                state.cycle_id,
                cb,
                degradation_checks=_round_checks,
                search_memory=state.search_memory,
            )
            update_round_state(
                state,
                round_result,
                round_num,
                schema=config.pipeline_schema,
            )

            record_query_flips(state, round_num)

            # --- After probe round: reset flag + force L2 ---
            if is_probe:
                state.exit_probe_mode()
                probe_stop = None
                if config.enable_l2:
                    _obs, _tid = get_obs_trace(state, state.obs_campaign_id)
                    probe_stop = await escalate_l2(
                        state,
                        config,
                        round_num,
                        cb.on_phase,
                        on_checkpoint=cb.on_checkpoint,
                        obs=_obs,
                        trace_id=_tid,
                    )
                if probe_stop:
                    stop_reason = probe_stop
                    break
                round_num += 1
                clean_rounds += 1
                continue  # skip normal escalation/stopping — L2 handled it

            # --- Escalation path ---
            if round_result.escalation_signal:
                esc_stop = await _handle_escalation_signal(
                    state,
                    config,
                    round_result,
                    round_num,
                    cb.on_phase,
                    state.obs_campaign_id,
                    state.campaign_store,
                    state.cycle_id,
                    on_checkpoint=cb.on_checkpoint,
                )
                if esc_stop:
                    stop_reason = esc_stop
                    break
                # Degradation rounds don't count toward max_rounds
                round_num += 1
                continue

            # --- Normal path (no escalation) ---
            state.record_round_outcome(round_result.improved)
            # L2 directive is valid for one round only — clear when L2 didn't fire
            if round_result.improved:
                state.opt_sp.l2_directive = ""

            if cb.on_round_complete:
                cb.on_round_complete(round_result, state.stall_count)

            # Checkpoint round to disk
            if state.campaign_store and state.cycle_id:
                _checkpoint_round(
                    state.campaign_store,
                    state.cycle_id,
                    config,
                    state,
                    round_result,
                    round_num,
                )

            # Round recorder: flush the round's LLM call audit trail.
            # Round outcome (accuracy/hits/opt_sp) is already in trial_NNNN.json
            # via _checkpoint_round above.
            _rr = get_round_recorder()
            if _rr:
                _rr.flush()

            # Bidirectional control checkpoint — user may pause/stop via dashboard
            if cb.on_checkpoint:
                _ctrl = cb.on_checkpoint("after_round")
                if _ctrl == "pause":
                    raise PauseForReviewError([], round_num, pause_point="user_pause")
                if _ctrl == "stop":
                    stop_reason = StopReason.USER_STOPPED
                    break

            refresh_search_memory(state, config, round_num)

            if not is_probe:
                try_adapt_eval_set(state, dataset, config, round_num)

            # Stopping conditions
            _stop = await _check_stopping_conditions(
                state,
                config,
                round_num,
                cb.on_phase,
                state.obs_campaign_id,
                on_checkpoint=cb.on_checkpoint,
            )
            if _stop:
                stop_reason = _stop
                break

            round_num += 1
            clean_rounds += 1

        # Loop completed without break
        if stop_reason is None:
            stop_reason = StopReason.HARD_CAP if round_num >= hard_cap else StopReason.MAX_ROUNDS

    except PauseForReviewError as pause:
        stop_reason = (
            StopReason.USER_PAUSED
            if pause.pause_point == "user_pause"
            else StopReason.PAUSED_FOR_REVIEW
        )
        logger.info(
            "HITL: paused at %s (round %d, %d candidates).",
            pause.pause_point,
            pause.round_num,
            len(pause.candidates),
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop_reason = StopReason.INTERRUPTED
        logger.warning(
            "Optimization interrupted at round %d. Completed rounds are checkpointed.",
            len(state.rounds),
        )

    # Cleanup round recorder
    from promptpotter.services.optimizer.pipeline import set_round_recorder

    set_round_recorder(None)

    assert stop_reason is not None
    return stop_reason


async def _run_optimization_core(
    instruction: str,
    dataset: list[dict[str, Any]],
    config: LoopConfig,
    *,
    baseline_prompt_fields: dict | None = None,
    baseline_accuracy: float = 0.0,
    baseline_results: list | None = None,
    callbacks: RunCallbacks | None = None,
    langfuse_session_id: str | None = None,
    cycle_id: str | None = None,
    experiment_id: str = "",
    session: SessionEnv | None = None,
) -> RunResult:
    """Core optimization loop: init → round loop → finalize."""
    started_at = datetime.now(UTC).isoformat()

    cb = callbacks or RunCallbacks()

    # -- Init --
    state, _emitter = await init_cycle_state(
        instruction,
        dataset,
        config,
        baseline_prompt_fields,
        baseline_accuracy,
        baseline_results,
        cb.on_phase,
        langfuse_session_id,
        cycle_id,
        experiment_id,
        session,
        started_at,
    )

    # Chain persistence callbacks (fires first) with caller display callbacks
    if _emitter:
        from promptpotter.services.campaign.state import chain_callbacks

        cb = chain_callbacks(
            RunCallbacks(
                on_phase=_emitter.on_phase,
                on_sample_scored=_emitter.on_sample_scored,
                on_candidate_scored=_emitter.on_candidate_scored,
                on_round_complete=_emitter.on_round_complete,
            ),
            cb,
        )

    stop_reason = await _run_round_loop(state, dataset, config, cb)

    # -- Finalize --
    finished_at = datetime.now(UTC).isoformat()
    obs = state.scoring_ctx.obs if state.scoring_ctx else None
    campaign_status = (
        "paused"
        if stop_reason in (StopReason.PAUSED_FOR_REVIEW, StopReason.USER_PAUSED)
        else "stopped"
        if stop_reason == StopReason.USER_STOPPED
        else "interrupted"
        if stop_reason == StopReason.INTERRUPTED
        else "completed"
    )
    finalize_campaign(
        state.campaign_store,
        state.cycle_id,
        config,
        state,
        stop_reason,
        finished_at,
        obs,
        state.obs_campaign_id,
        status=campaign_status,
    )

    # Persistence emitter: finalize artifacts
    if _emitter:
        _emitter.finalize(
            n_rounds=len(state.rounds),
            best_accuracy=state.best_accuracy,
            best_round=state.best_round,
            stop_reason=stop_reason,
            cycle_id=state.cycle_id,
        )

    cloud_trace_id = (
        None
        if stop_reason in (StopReason.INTERRUPTED, StopReason.PAUSED_FOR_REVIEW)
        else (obs.get_cloud_trace_id(state.obs_campaign_id) if obs else None)
    )

    return RunResult(
        rounds=state.rounds,
        n_rounds=len(state.rounds),
        best_accuracy=state.best_accuracy,
        best_round=state.best_round,
        baseline_accuracy=(state.rounds[0].accuracy if state.rounds else state.current_accuracy),
        winner_prompt_fields=state.opt_sp.prompt_field_dict() if state.best_sp else {},
        winner_pipeline_params=state.best_sp.pipeline_params if state.best_sp else None,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        langfuse_trace_id=cloud_trace_id,
        cycle_id=state.cycle_id,
        resumed_from_round=state.resumed_from_round,
    )


# ---------------------------------------------------------------------------
# Campaign-level entry point (config assembly + callback chaining)
# ---------------------------------------------------------------------------


def _make_round_append_callback(
    campaign_rounds: list,
) -> Callable:
    """Build the round-entry append callback shared by all entry points."""

    def _on_round(round_result: Any, stall_count: int) -> None:
        round_entry = round_result.model_dump()
        ps_raw = round_entry.get("prompt_fields", {})
        round_entry["prompt_fields"] = (
            OptSearchPoint.from_prompt_fields(ps_raw) if isinstance(ps_raw, dict) else ps_raw
        )
        round_entry["round"] = len(campaign_rounds)
        campaign_rounds.append(round_entry)

    return _on_round


async def run_optimization(
    campaign_rounds: list,
    dataset: list,
    campaign_config: CampaignConfig,
    *,
    session: SessionEnv,
    scan_brief: ScanBrief | None = None,
    experiment_id: str | None = None,
    task_context: TaskDecomposition | dict | None = None,
    session_id: str = "",
    callbacks: RunCallbacks | None = None,
    langfuse_session_id: str | None = None,
    cycle_id: str | None = None,
    max_rounds_override: int | None = None,
) -> RunResult | None:
    """Build config, extract baseline, wire round-append callback, and run loop.

    Callers pass display-specific callbacks via *callbacks*; the round-entry
    append is handled internally and chained before the caller's
    ``on_round_complete``.

    *max_rounds_override* caps ``max_rounds`` without mutating
    *campaign_config* — used by ``--round`` to run a single round
    ephemerally.

    Returns ``RunResult`` (or ``None`` if interrupted before any rounds).
    """
    config = LoopConfig.from_campaign_config(
        campaign_config,
        backend_id=session.backend_id,
        project_root=str(session.store.base_dir),
        session_id=session_id,
        scan_brief=scan_brief,
        pipeline_schema=session.pipeline_schema,
        task_context=task_context,
    )
    if max_rounds_override is not None:
        config = config.model_copy(update={"max_rounds": max_rounds_override})

    bl = extract_campaign_baseline(campaign_rounds)

    # Chain: round-append fires first, then caller's display callback
    append_cb = _make_round_append_callback(campaign_rounds)
    caller_cb = callbacks or RunCallbacks()

    original_on_round = caller_cb.on_round_complete

    def _chained_on_round(round_result: Any, stall_count: int) -> None:
        append_cb(round_result, stall_count)
        if original_on_round:
            original_on_round(round_result, stall_count)

    merged_cb = RunCallbacks(
        on_round_complete=_chained_on_round,
        on_candidate_scored=caller_cb.on_candidate_scored,
        on_sample_scored=caller_cb.on_sample_scored,
        on_phase=caller_cb.on_phase,
        on_checkpoint=caller_cb.on_checkpoint,
    )

    return await _run_optimization_core(
        bl.instruction,
        dataset,
        config,
        baseline_prompt_fields=bl.baseline_ps,
        baseline_accuracy=bl.baseline_acc,
        baseline_results=bl.baseline_results,
        callbacks=merged_cb,
        langfuse_session_id=langfuse_session_id,
        cycle_id=cycle_id,
        experiment_id=experiment_id or "",
        session=session,
    )
