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

from promptpotter.models.eval_context import EvalContext
from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.models.phase_event import PhaseEvent
from promptpotter.services.backend_client import BackendClient
from promptpotter.services.campaign._campaign_utils import emit_phase, get_obs_trace, graceful
from promptpotter.services.campaign.adaptive_eval import adapt_eval_set
from promptpotter.services.campaign.callbacks import CycleCallbacks
from promptpotter.services.campaign.campaign_lifecycle import (
    finalize_campaign,
    init_campaign,
)
from promptpotter.services.campaign.config import CycleConfig
from promptpotter.services.campaign.critique import sample_thinking_styles
from promptpotter.services.campaign.escalation import escalate_l2
from promptpotter.services.campaign.results import CycleResult, CycleRoundResult, StopReason
from promptpotter.services.campaign.round_execution import (
    PauseForReviewError,
    execute_round,
    update_round_state,
)
from promptpotter.services.campaign.state import CycleInitResult, LoopState
from promptpotter.services.metrics import compile_query_difficulty, compute_composite_score
from promptpotter.services.prompt_eval import subsample_eval_data
from promptpotter.services.search.scan_results import ScanContext
from promptpotter.services.search.search_memory import SearchMemory
from promptpotter.shared.hashing import HASH_TRUNCATE

if TYPE_CHECKING:
    from promptpotter.services.stores.campaign_store import CampaignStore

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
    logger.debug(
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
    on_checkpoint: Callable[[str], str | None] | None = None,
) -> StopReason | None:
    """After a probe round: reset flag and force L2 if enabled."""
    state.probe_next_round = False
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
    config: CycleConfig,
    round_result: CycleRoundResult,
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
    logger.debug("Using provided baseline (acc=%.3f)", baseline_accuracy)

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
        active_steps=config.active_steps,
        prompt_node=config.prompt_node,
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
            known = {k: v for k, v in _osp.items() if k in OptSearchPoint.model_fields}
            missing = set(OptSearchPoint.model_fields) - set(_osp)
            if missing:
                logger.debug(
                    "Checkpoint missing %d OptSearchPoint field(s): %s "
                    "(using defaults — checkpoint predates schema change)",
                    len(missing), ", ".join(sorted(missing)),
                )
            state.opt_sp = OptSearchPoint(**known)
        state.escalation.l2_round = _latest_trial.get("l2_round", 0)
        state.escalation.l3_round = _latest_trial.get("l3_round", 0)
        state.escalation.l2_stall_count = _latest_trial.get("l2_stall_count", 0)
        state.escalation.l3_stall_count = _latest_trial.get("l3_stall_count", 0)
        state.stall_count = _latest_trial.get("stall_count", 0)
        logger.debug(
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
        from promptpotter.services.project_store import ProjectStore

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
        stale_data_load_protocol=config.stale_data_load_protocol,
        stale_data_observations=state.opt_sp.stale_data_observations,
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

    from promptpotter.services.campaign.escalation import build_escalation_checks

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

    # 4. SearchMemory — load + refresh from historical data
    search_memory: SearchMemory | None = None
    _store = state.eval_ctx.store if state.eval_ctx else None
    if _store and config.backend_id:
        from pathlib import Path

        _sm_path = Path(_store.base_dir) / config.backend_id / "search_memory.json"
        search_memory = SearchMemory.load(_sm_path)
        if search_memory.refresh(_store, config.backend_id):
            search_memory.save(_sm_path)

    # Build restored state summary for display
    _restored = {}
    if resumed_from_round:
        _restored = {
            "critique_chars": len(state.opt_sp.critique_text),
            "task_context_keys": len(state.opt_sp.task_context),
            "escalation_journal_entries": len(state.opt_sp.escalation_journal),
            "l2_round": state.escalation.l2_round,
        }

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
        restored_state=_restored,
    )

    # 5. Persistence emitter — auto-created for all entry points
    persistence_emitter = None
    if config.project_root and config.backend_id and config.session_id:
        from pathlib import Path

        from promptpotter.services.campaign.persistence_emitter import CampaignPersistenceEmitter

        _session_dir = Path(config.project_root) / config.backend_id / "sessions" / config.session_id
        _session_store = None
        if _store:
            from promptpotter.services.stores.session_store import SessionStore
            _session_store = SessionStore(Path(config.project_root))

        resume_from = CampaignPersistenceEmitter.load_resume_state(
            _session_dir, baseline=baseline_accuracy,
        )
        persistence_emitter = CampaignPersistenceEmitter(
            _session_dir,
            session_store=_session_store,
            backend_id=config.backend_id,
            session_id=config.session_id,
            max_rounds=config.max_rounds or 999,
            l1_patience=config.l1_patience,
            active_nodes=list(config.active_steps),
            config={},  # raw campaign config not available here; emitter uses defaults
            resume_from=resume_from,
        )

    return CycleInitResult(
        state=state,
        campaign_store=campaign_store,
        cycle_id=cycle_id,
        obs_campaign_id=obs_campaign_id,
        round_eval_data=round_eval_data,
        escalation_checks=escalation_checks,
        resumed_from_round=resumed_from_round,
        search_memory=search_memory,
        persistence_emitter=persistence_emitter,
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
    scan_context: ScanContext | None = None,
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
    search_memory = init.search_memory
    _emitter = init.persistence_emitter
    state.search_memory = search_memory
    if state.eval_ctx and search_memory:
        state.eval_ctx.search_memory = search_memory

    # Chain persistence callbacks (fires first) with caller display callbacks
    if _emitter:
        from promptpotter.services.campaign.callbacks import chain_callbacks

        persistence_cb = CycleCallbacks(
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
                    eval_data,
                    round_num,
                )
            else:
                _round_data = round_eval_data
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
                    round_eval_data, _adapt = adapt_eval_set(
                        round_eval_data, _qd, eval_data,
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
