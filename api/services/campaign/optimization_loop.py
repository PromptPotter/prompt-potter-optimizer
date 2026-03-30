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
import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from api.models.eval_context import EvalContext
from api.models.opt_search_point import OptSearchPoint
from api.models.phase_event import PhaseEvent
from api.services.backend_client import BackendClient
from api.services.campaign.callbacks import CycleCallbacks
from api.services.campaign.campaign_lifecycle import (
    finalize_campaign,
    init_campaign,
)
from api.services.campaign.config import CycleConfig
from api.services.campaign.critique import sample_thinking_styles
from api.services.campaign.escalation import escalate_l2
from api.services.campaign.helpers import emit_phase, get_obs_trace, graceful
from api.services.campaign.results import CycleResult, CycleRoundResult, StopReason
from api.services.campaign.round_execution import (
    execute_round,
    update_round_state,
)
from api.services.campaign.state import CycleInitResult, LoopState
from api.services.metrics import compute_composite_score
from api.services.prompt_eval import subsample_eval_data
from api.shared.hashing import HASH_TRUNCATE

if TYPE_CHECKING:
    from api.services.stores.campaign_store import CampaignStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extracted loop helpers (probe + escalation)
# ---------------------------------------------------------------------------


def _prepare_probe_data(
    state: LoopState,
    eval_data: list[dict],
    round_num: int,
) -> tuple[list[dict], list | None]:
    """Build eval data for a probe round (warned queries only, no escalation)."""
    warned_queries = {q for q, e in state.opt_sp.warning_inventory.items() if e.get("warnings")}
    round_data = [d for d in eval_data if d.get("query") in warned_queries]
    logger.info(
        "PROBE round %d: %d warned queries (from %d tracked)",
        round_num,
        len(round_data),
        len(warned_queries),
    )
    return round_data, None  # no escalation checks during probe


async def _handle_post_probe(
    state: LoopState,
    config: CycleConfig,
    round_num: int,
    round_data: list[dict],
    on_phase: Callable[[PhaseEvent], None] | None,
    obs_campaign_id: str,
) -> StopReason | None:
    """After a probe round: reset flag and force L2 if enabled."""
    state.probe_next_round = False
    if config.enable_l2:
        _obs, _tid = get_obs_trace(state, obs_campaign_id)
        return await escalate_l2(
            state,
            config,
            round_num,
            round_data,
            on_phase,
            obs=_obs,
            trace_id=_tid,
        )
    return None


async def _handle_escalation_signal(
    state: LoopState,
    config: CycleConfig,
    round_result: CycleRoundResult,
    round_num: int,
    round_eval_data: list[dict],
    on_phase: Callable[[PhaseEvent], None] | None,
    obs_campaign_id: str,
    campaign_store: "CampaignStore | None",
    cycle_id: str | None,
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
    logger.warning(
        "Escalation '%s' at round %d — target=%s, degraded_rate=%.1f%%",
        signal["check_name"],
        round_num,
        signal["target"],
        signal["context"].get("degraded_rate", 0) * 100,
    )

    esc_context = signal["context"]

    from api.services.campaign.escalation import EscalationTarget

    # Dispatch by target
    if signal["target"] in (EscalationTarget.L2, EscalationTarget.L3) and config.enable_l2:
        # Fill outcome of previous journal entry (if any)
        journal = state.opt_sp.escalation_journal
        if journal and journal[-1].get("outcome_degraded_rate") is None:
            journal[-1]["outcome_degraded_rate"] = esc_context.get("degraded_rate", 0)

        _obs, _tid = get_obs_trace(state, obs_campaign_id)
        try:
            l2_stop = await escalate_l2(
                state,
                config,
                round_num,
                round_eval_data,
                on_phase,
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

        # Record journal entry after L2 transition
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
# Init helpers
# ---------------------------------------------------------------------------


def _build_baseline_state(
    config: CycleConfig,
    baseline_prompt_fields: dict | None,
    baseline_accuracy: float,
    baseline_results: list | None,
) -> tuple[LoopState, OptSearchPoint]:
    """Construct LoopState from baseline config and prompt fields.

    Returns:
        (state, baseline_osp) — the initial loop state and the parsed
        baseline OptSearchPoint (needed by caller for resume/alias logic).
    """
    if baseline_prompt_fields is None:
        raise ValueError(
            "baseline_prompt_fields is required. Run baseline evaluation in the "
            "notebook before starting the feedback cycle.",
        )
    baseline_osp = (
        OptSearchPoint.from_prompt_fields(baseline_prompt_fields)
        if isinstance(baseline_prompt_fields, dict)
        else baseline_prompt_fields
    )
    current_results: list = baseline_results or []
    logger.info("Using provided baseline (acc=%.3f)", baseline_accuracy)

    if current_results:
        _bl_composite = compute_composite_score(
            current_results,
            config.pipeline_schema,
        )["composite"]
    else:
        _bl_composite = baseline_accuracy

    opt_sp = OptSearchPoint(
        task_context=config.task_context or {},
        persona=baseline_osp.persona,
        task_intent=baseline_osp.task_intent,
        problem_description=baseline_osp.problem_description,
        instruction=baseline_osp.instruction,
        thinking_style=baseline_osp.thinking_style,
        answer_format=baseline_osp.answer_format,
        plan=baseline_osp.plan,
        optimizer_params=dict(baseline_osp.optimizer_params),
    )
    baseline_sp = opt_sp.to_job_search_point(
        base_pipeline_params=config.pipeline_params,
    )
    state = LoopState(
        current_sp=baseline_sp,
        current_accuracy=baseline_accuracy,
        current_composite=_bl_composite,
        current_results=current_results,
        best_accuracy=baseline_accuracy,
        best_composite=_bl_composite,
        best_sp=baseline_sp,
        opt_sp=opt_sp,
    )
    return state, baseline_osp


def _restore_from_checkpoint(
    state: LoopState,
    config: CycleConfig,
    campaign_store: "CampaignStore",
    cycle_id: str,
    resumed_from_round: int,
) -> None:
    """Restore optimizer state from a campaign checkpoint (in-place).

    Only called when ``resumed_from_round > 0``.
    """
    _latest_trial = campaign_store.load_trial(
        config.backend_id,
        cycle_id,
        resumed_from_round - 1,
    )
    if _latest_trial:
        _osp = _latest_trial.get("opt_search_point", {})
        if _osp:
            state.opt_sp = OptSearchPoint(
                **{k: v for k, v in _osp.items() if k in OptSearchPoint.model_fields}
            )
        state.escalation.l2_round = _latest_trial.get("l2_round", 0)
        state.escalation.l3_round = _latest_trial.get("l3_round", 0)
        state.escalation.l2_stall_count = _latest_trial.get("l2_stall_count", 0)
        state.escalation.l3_stall_count = _latest_trial.get("l3_stall_count", 0)
        state.stall_count = _latest_trial.get("stall_count", 0)
        logger.info(
            "Restored optimizer state from round %d "
            "(critique=%d chars, task_context=%d keys, "
            "escalation_journal=%d entries, l2_round=%d)",
            resumed_from_round - 1,
            len(state.opt_sp.critique_text),
            len(state.opt_sp.task_context),
            len(state.opt_sp.escalation_journal),
            state.escalation.l2_round,
        )


def _setup_eval_context(
    state: LoopState,
    config: CycleConfig,
    instruction: str,
    baseline_osp: OptSearchPoint,
    backend_client: BackendClient,
    obs: Any,
    experiment_id: str,
    cycle_id: str | None,
) -> list:
    """Wire up EvalContext on state and build escalation checks.

    Returns:
        escalation_checks list.
    """
    _store = None
    if config.project_root:
        from api.services.project_store import ProjectStore

        _store = ProjectStore(config.project_root)
    state.eval_ctx = EvalContext(
        backend_client=backend_client,
        store=_store,
        backend_id=config.backend_id,
        pipeline_schema=config.pipeline_schema,
        obs=obs,
        source="optimization_loop",
        experiment_id=experiment_id or (cycle_id.replace("cycle_", "")[:12] if cycle_id else ""),
        max_consecutive_errors=config.max_consecutive_errors,
    )

    # Alias: raw instruction ↔ restructured baseline
    if _store and config.backend_id and instruction:
        _raw_hash = hashlib.sha256(instruction.encode()).hexdigest()[:HASH_TRUNCATE]
        _restructured_hash = hashlib.sha256(
            baseline_osp.render().encode(),
        ).hexdigest()[:HASH_TRUNCATE]
        if _raw_hash != _restructured_hash:
            _store.dataset_runs.register_alias(
                config.backend_id,
                _raw_hash,
                _restructured_hash,
            )
            logger.info(
                "Registered prompt alias: %s ↔ %s",
                _raw_hash[:8],
                _restructured_hash[:8],
            )

    from api.services.campaign.escalation import build_escalation_checks

    return build_escalation_checks(config)


# ---------------------------------------------------------------------------
# Init coordinator
# ---------------------------------------------------------------------------


async def _init_cycle_state(
    instruction: str,
    eval_data: list[dict[str, Any]],
    config: CycleConfig,
    baseline_prompt_fields: dict | None,
    baseline_accuracy: float,
    baseline_results: list | None,
    on_phase: Callable[[PhaseEvent], None] | None,
    langfuse_session_id: str | None,
    cycle_id: str | None,
    experiment_id: str,
    backend_client: "BackendClient | None",
    started_at: str,
) -> CycleInitResult:
    """Initialize all cycle state: baseline, resume, obs, eval context."""
    emit_phase(
        on_phase,
        "init",
        "enter",
        max_rounds=config.max_rounds,
        patience=config.l1_patience,
        n_variants=config.n_variants,
        model=config.model or "(default)",
        sample_size=config.sample_size,
        enable_l2=config.enable_l2,
        enable_l3=config.enable_l3,
        eval_data_count=len(eval_data),
        baseline_accuracy=baseline_accuracy,
        has_scan_context=config.scan_context is not None,
        enable_critique=config.enable_critique,
        pipeline_params=config.pipeline_params,
        node_param_keys=(
            {s: sorted(k) for s, k in config.pipeline_schema.node_param_keys().items()}
            if config.pipeline_schema
            else None
        ),
    )

    _bc = backend_client or BackendClient(config.backend_url)
    if config.session_terms:
        await _bc.init_session(config.session_terms)

    round_eval_data = subsample_eval_data(eval_data, config.sample_size, config.seed)

    # 1. Build baseline state
    state, baseline_osp = _build_baseline_state(
        config,
        baseline_prompt_fields,
        baseline_accuracy,
        baseline_results,
    )

    # 2. Resume detection + obs init
    campaign_store, cycle_id, resumed_from_round, obs, obs_campaign_id = init_campaign(
        config,
        eval_data,
        baseline_osp.prompt_field_dict(),
        baseline_prompt_fields,
        baseline_accuracy,
        started_at,
        cycle_id_override=cycle_id,
        langfuse_session_id=langfuse_session_id,
    )
    if resumed_from_round > 0 and campaign_store and cycle_id:
        _restore_from_checkpoint(
            state,
            config,
            campaign_store,
            cycle_id,
            resumed_from_round,
        )
    state.opt_sp.thinking_styles = sample_thinking_styles(n=3, seed=config.seed)

    # 3. Eval context + escalation checks
    escalation_checks = _setup_eval_context(
        state,
        config,
        instruction,
        baseline_osp,
        _bc,
        obs,
        experiment_id,
        cycle_id,
    )

    emit_phase(
        on_phase,
        "init",
        "exit",
        cycle_id=cycle_id,
        resumed_from_round=resumed_from_round,
        baseline_accuracy=baseline_accuracy,
        obs_enabled=obs is not None,
        sample_count=len(round_eval_data),
        enable_critique=config.enable_critique,
    )

    return CycleInitResult(
        state=state,
        campaign_store=campaign_store,
        cycle_id=cycle_id,
        obs_campaign_id=obs_campaign_id,
        round_eval_data=round_eval_data,
        escalation_checks=escalation_checks,
        resumed_from_round=resumed_from_round,
    )


# ---------------------------------------------------------------------------
# Round loop helpers
# ---------------------------------------------------------------------------


def _checkpoint_round(
    campaign_store: "CampaignStore",
    cycle_id: str,
    config: CycleConfig,
    state: LoopState,
    round_result: CycleRoundResult,
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
    config: CycleConfig,
    round_result: CycleRoundResult,
    round_num: int,
    round_eval_data: list[dict],
    on_phase: Callable[[PhaseEvent], None] | None,
    obs_campaign_id: str,
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
                round_eval_data,
                on_phase,
                obs=_obs,
                trace_id=_tid,
            )
            if _l2_stop:
                return _l2_stop
            state.stall_count = 0  # L2/L3 changed prompt — fresh window
        else:
            return StopReason.PATIENCE
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_optimization(
    instruction: str,
    eval_data: list[dict[str, Any]],
    config: CycleConfig,
    *,
    baseline_prompt_fields: dict | None = None,
    baseline_accuracy: float = 0.0,
    baseline_results: list | None = None,
    callbacks: CycleCallbacks | None = None,
    langfuse_session_id: str | None = None,
    scan_context: dict | None = None,
    cycle_id: str | None = None,
    experiment_id: str = "",
    backend_client: "BackendClient | None" = None,
) -> CycleResult:
    """Run iterative optimization with feedback cycling.

    Executes: init → round loop (l1_generate → l1_evaluate) → finalize.
    Stops on: patience exhaustion, max_rounds, perfect score, or interrupt.
    """
    if scan_context is not None:
        config = config.model_copy(update={"scan_context": scan_context})
    started_at = datetime.now(UTC).isoformat()

    cb = callbacks or CycleCallbacks()

    # -- Init --
    init = await _init_cycle_state(
        instruction,
        eval_data,
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
    round_eval_data = init.round_eval_data
    escalation_checks = init.escalation_checks
    resumed_from_round = init.resumed_from_round

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
                    eval_data,
                    round_num,
                )
            else:
                _round_data = round_eval_data
                _round_checks = escalation_checks

            logger.info(
                "Optimization round %d (clean=%d/%d, acc=%.3f, stall=%d/%d%s)",
                round_num,
                clean_rounds,
                max_rounds,
                state.current_accuracy,
                state.stall_count,
                config.l1_patience,
                ", PROBE" if is_probe else "",
            )

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
            )
            update_round_state(state, round_result, round_num)

            # --- After probe round: reset flag + force L2 ---
            if is_probe:
                probe_stop = await _handle_post_probe(
                    state,
                    config,
                    round_num,
                    _round_data,
                    cb.on_phase,
                    obs_campaign_id,
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
                    round_eval_data,
                    cb.on_phase,
                    obs_campaign_id,
                    campaign_store,
                    cycle_id,
                )
                if esc_stop:
                    stop_reason = esc_stop
                    break
                # Degradation rounds don't count toward max_rounds
                round_num += 1
                continue

            # --- Normal path (no escalation) ---
            state.stall_count = 0 if round_result.improved else state.stall_count + 1
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

            # Stopping conditions
            _stop = await _check_stopping_conditions(
                state,
                config,
                round_result,
                round_num,
                round_eval_data,
                cb.on_phase,
                obs_campaign_id,
            )
            if _stop:
                stop_reason = _stop
                break

            round_num += 1
            clean_rounds += 1

        # Loop completed without break
        if stop_reason is None:
            stop_reason = StopReason.HARD_CAP if round_num >= hard_cap else StopReason.MAX_ROUNDS

    except (KeyboardInterrupt, asyncio.CancelledError):
        stop_reason = StopReason.INTERRUPTED
        logger.warning(
            "Optimization interrupted at round %d. Completed rounds are checkpointed.",
            len(state.rounds),
        )

    # -- Finalize --
    finished_at = datetime.now(UTC).isoformat()
    obs = state.eval_ctx.obs if state.eval_ctx else None
    campaign_status = "interrupted" if stop_reason == StopReason.INTERRUPTED else "completed"
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
    cloud_trace_id = (
        None
        if stop_reason == StopReason.INTERRUPTED
        else (obs.get_cloud_trace_id(obs_campaign_id) if obs else None)
    )

    return CycleResult(
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
