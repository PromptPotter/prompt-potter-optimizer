"""
Optimization loop orchestrator for iterative prompt optimization.

Runs ``l1_generate()`` → ``l1_evaluate()`` in a
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

from promptpotter.models.phase_event import PhaseEvent
from promptpotter.services.backend_client import BackendClient
from promptpotter.services.campaign.callbacks import RunCallbacks, emit_phase, get_obs_trace
from promptpotter.services.campaign.config import RunConfig
from promptpotter.services.campaign.escalation import escalate_l2
from promptpotter.services.campaign.lifecycle import finalize_campaign
from promptpotter.services.campaign.loop_init import init_cycle_state
from promptpotter.services.campaign.round_execution import (
    PauseForReviewError,
    adapt_eval_set,
    execute_round,
    update_round_state,
)
from promptpotter.services.campaign.state import (
    LoopState,
    RoundResult,
    RunResult,
    StopReason,
)
from promptpotter.services.metrics import compile_query_difficulty
from promptpotter.services.search.scan_results import ScanContext
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.services.stores.campaign_store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["run_optimization"]


# ---------------------------------------------------------------------------
# Extracted loop helpers (probe + escalation)
# ---------------------------------------------------------------------------


def _prepare_probe_data(
    state: LoopState,
    dataset: list[dict],
    round_num: int,
) -> tuple[list[dict], list | None]:
    """Build eval data for a probe round (warned queries only, no escalation)."""
    warned_queries = {q for q, e in state.opt_sp.warning_inventory.items() if e.get("warnings")}
    round_data = [d for d in dataset if d.get("query") in warned_queries]
    logger.debug(
        "PROBE round %d: %d warned queries (from %d tracked)",
        round_num,
        len(round_data),
        len(warned_queries),
    )
    return round_data, None  # no escalation checks during probe


async def _handle_post_probe(
    state: LoopState,
    config: RunConfig,
    round_num: int,
    round_data: list[dict],
    on_phase: Callable[[PhaseEvent], None] | None,
    obs_campaign_id: str,
    on_checkpoint: Callable[[str], str | None] | None = None,
) -> StopReason | None:
    """After a probe round: reset flag and force L2 if enabled."""
    state.exit_probe_mode()
    if config.enable_l2:
        _obs, _tid = get_obs_trace(state, obs_campaign_id)
        return await escalate_l2(
            state,
            config,
            round_num,
            on_phase,
            on_checkpoint=on_checkpoint,
            obs=_obs,
            trace_id=_tid,
        )
    return None


async def _handle_escalation_signal(
    state: LoopState,
    config: RunConfig,
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
        "escalation",
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

    esc_context = signal["context"]

    from promptpotter.services.campaign.escalation import EscalationTarget

    # Dispatch by target
    if signal["target"] in (EscalationTarget.L2, EscalationTarget.L3) and config.enable_l2:
        # Fill outcome of previous journal entry (if any)
        journal = state.opt_sp.escalation_journal
        if journal and journal[-1].get("outcome_degraded_rate") is None:
            journal[-1]["outcome_degraded_rate"] = esc_context.get("degraded_rate", 0)

        # Record journal entry BEFORE L2 so L2 sees the current event
        # in the journal (not just history). Also ensures the entry is
        # persisted even if L2 returns a stop signal.
        dominant = esc_context.get("dominant_warning", "unknown:unknown")
        problem_step = dominant.split(":")[0] if ":" in dominant else "unknown"
        step_cfg = ((state.current_sp.pipeline_params if state.current_sp else None) or {}).get(
            problem_step, {}
        )
        state.opt_sp.escalation_journal.append(
            {
                "round": round_num,
                "degraded_rate": esc_context.get("degraded_rate", 0),
                "problem_step": problem_step,
                "step_config": dict(step_cfg) if isinstance(step_cfg, dict) else {},
                "warning_types": esc_context.get("warning_types", {}),
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
                escalation_context=esc_context,
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
    emit_phase(on_phase, "escalation", "exit", round=round_num)
    return None


# ---------------------------------------------------------------------------
# Round loop helpers
# ---------------------------------------------------------------------------


def _checkpoint_round(
    campaign_store: "CampaignStore",
    cycle_id: str,
    config: RunConfig,
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
                "candidates_evaluated": round_result.candidates_evaluated,
                "candidate_scores": list(round_result.candidate_scores),
                "stall_count": state.stall_count,
                "l2_stall_count": state.escalation.l2_stall_count,
                "l3_stall_count": state.escalation.l3_stall_count,
                "l2_round": state.escalation.l2_round,
                "l3_round": state.escalation.l3_round,
                "opt_search_point": state.opt_sp.model_dump(),
            },
        )


async def _check_stopping_conditions(
    state: LoopState,
    config: RunConfig,
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_optimization(
    instruction: str,
    dataset: list[dict[str, Any]],
    config: RunConfig,
    *,
    baseline_prompt_fields: dict | None = None,
    baseline_accuracy: float = 0.0,
    baseline_results: list | None = None,
    callbacks: RunCallbacks | None = None,
    langfuse_session_id: str | None = None,
    scan_context: ScanContext | None = None,
    cycle_id: str | None = None,
    experiment_id: str = "",
    backend_client: "BackendClient | None" = None,
) -> RunResult:
    """Run iterative optimization with feedback cycling.

    Executes: init → round loop (l1_generate → l1_evaluate) → finalize.
    Stops on: patience exhaustion, max_rounds, perfect score, or interrupt.
    """
    if scan_context is not None:
        config = config.model_copy(update={"scan_context": scan_context})
    started_at = datetime.now(UTC).isoformat()

    cb = callbacks or RunCallbacks()

    # -- Init --
    init = await init_cycle_state(
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
        backend_client,
        started_at,
    )
    state = init.state
    campaign_store = init.campaign_store
    cycle_id = init.cycle_id
    obs_campaign_id = init.obs_campaign_id
    round_dataset = init.round_dataset
    escalation_checks = init.escalation_checks
    resumed_from_round = init.resumed_from_round
    search_memory = init.search_memory
    _emitter = init.persistence_emitter
    state.search_memory = search_memory
    if state.eval_ctx and search_memory:
        state.eval_ctx.search_memory = search_memory

    # Chain persistence callbacks (fires first) with caller display callbacks
    if _emitter:
        from promptpotter.services.campaign.callbacks import chain_callbacks

        persistence_cb = RunCallbacks(
            on_phase=_emitter.on_phase,
            on_query_eval=_emitter.on_query_eval,
            on_candidate_eval=_emitter.on_candidate_eval,
            on_round_complete=_emitter.on_round_complete,
        )
        cb = chain_callbacks(persistence_cb, cb)

    # -- Round loop --
    stop_reason: StopReason | None = None
    hard_cap = config.hard_cap

    try:
        round_num = 0
        clean_rounds = 0
        max_rounds = config.max_rounds or 999

        while clean_rounds < max_rounds and round_num < hard_cap:
            # --- Probe vs normal round data ---
            is_probe = state.probe_next_round
            if is_probe:
                _round_data, _round_checks = _prepare_probe_data(
                    state,
                    dataset,
                    round_num,
                )
            else:
                _round_data = round_dataset
                _round_checks = escalation_checks

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
            from promptpotter.config.optimizer_pipeline import get_round_recorder

            _rr = get_round_recorder()
            if _rr:
                _rr.begin_round(round_num)

            round_result = await execute_round(
                round_num,
                state,
                _round_data,
                config,
                obs_campaign_id,
                campaign_store,
                cycle_id,
                cb,
                escalation_checks=_round_checks,
                search_memory=search_memory,
            )
            update_round_state(state, round_result, round_num, active_steps=config.active_steps, prompt_node=config.prompt_node)

            # --- After probe round: reset flag + force L2 ---
            if is_probe:
                probe_stop = await _handle_post_probe(
                    state,
                    config,
                    round_num,
                    _round_data,
                    cb.on_phase,
                    obs_campaign_id,
                    on_checkpoint=cb.on_checkpoint,
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
                    obs_campaign_id,
                    campaign_store,
                    cycle_id,
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
            if campaign_store and cycle_id:
                _checkpoint_round(
                    campaign_store,
                    cycle_id,
                    config,
                    state,
                    round_result,
                    round_num,
                )

            # Round recorder: eval + decision + flush
            _rr = get_round_recorder()
            if _rr:
                _rr.record_round_outcome(round_result, state)

            # Bidirectional control checkpoint — user may pause/stop via dashboard
            if cb.on_checkpoint:
                _ctrl = cb.on_checkpoint("after_round")
                if _ctrl == "pause":
                    raise PauseForReviewError([], round_num, pause_point="user_pause")
                if _ctrl == "stop":
                    stop_reason = StopReason.USER_STOPPED
                    break

            # SearchMemory refresh — pick up results from this round
            if search_memory and state.eval_ctx and state.eval_ctx.store:
                from pathlib import Path

                _sm_store = state.eval_ctx.store
                if search_memory.refresh(_sm_store, config.backend_id):
                    _sm_path = Path(_sm_store.base_dir) / config.backend_id / "search_memory.json"
                    search_memory.save(_sm_path)

            # Adaptive eval set — swap dead queries for discriminating ones
            if round_num >= 2 and not is_probe:
                _hist = [r.results for r in state.rounds if r.results]
                if len(_hist) >= 3:
                    _qd = compile_query_difficulty(_hist)
                    round_dataset, _adapt = adapt_eval_set(
                        round_dataset, _qd, dataset,
                        seed=config.seed + round_num,
                    )
                    if not _adapt.get("unchanged"):
                        logger.info("Adaptive eval: %s", _adapt)

            # Stopping conditions
            _stop = await _check_stopping_conditions(
                state,
                config,
                round_num,
                cb.on_phase,
                obs_campaign_id,
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

    # -- Cleanup round recorder --
    from promptpotter.config.optimizer_pipeline import set_round_recorder
    set_round_recorder(None)

    # -- Finalize --
    finished_at = datetime.now(UTC).isoformat()
    obs = state.eval_ctx.obs if state.eval_ctx else None
    campaign_status = (
        "paused" if stop_reason in (StopReason.PAUSED_FOR_REVIEW, StopReason.USER_PAUSED)
        else "stopped" if stop_reason == StopReason.USER_STOPPED
        else "interrupted" if stop_reason == StopReason.INTERRUPTED
        else "completed"
    )
    finalize_campaign(
        campaign_store,
        cycle_id,
        config,
        state,
        stop_reason,
        finished_at,
        obs,
        obs_campaign_id,
        status=campaign_status,
    )

    # Persistence emitter: finalize artifacts
    if _emitter:
        _emitter.finalize(
            n_rounds=len(state.rounds),
            best_accuracy=state.best_accuracy,
            best_round=state.best_round,
            stop_reason=stop_reason,
            cycle_id=cycle_id,
        )

    cloud_trace_id = (
        None
        if stop_reason in (StopReason.INTERRUPTED, StopReason.PAUSED_FOR_REVIEW)
        else (obs.get_cloud_trace_id(obs_campaign_id) if obs else None)
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
        cycle_id=cycle_id,
        resumed_from_round=resumed_from_round,
    )
