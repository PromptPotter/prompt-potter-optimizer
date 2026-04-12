"""EscalationCheck framework and L2/L3 escalation execution.

Two layers:
1. **EscalationCheck framework** — per-query checks run during
   ``_run_eval_batch()`` that can abort evaluation early.
2. **Escalation execution** — L1→L2→L3 layer transition orchestration
   triggered when the feedback cycle stalls.

Pure data types (``EscalationTarget``, ``EscalationSignal``,
``EscalationStrategy``) live in ``promptpotter.domain.analysis``.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.nodes.critique import extract_warning_types
from promptpotter.application.scoring.metrics import count_degraded_queries
from promptpotter.domain.analysis import (
    DEFAULT_STRATEGIES,
    EscalationSignal,
    EscalationStrategy,
    EscalationTarget,
)
from promptpotter.infrastructure.persistence.state import CampaignPhase, PhaseEvent, emit_phase

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import LoopConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.persistence.state import LoopState, StopReason
    from promptpotter.infrastructure.tracing.observability_logger import ObsLogger

logger = logging.getLogger(__name__)

__all__ = [
    "DegradationCheck",
    "EscalationSignal",
    "EscalationStrategy",
    "EscalationTarget",
    "build_degradation_checks",
    "collect_warning_types",
]


class DegradationCheck:
    """Triggers when degraded query fraction exceeds threshold."""

    def __init__(
        self,
        threshold: float = 0.4,
        strategies: dict[str, EscalationStrategy] | None = None,
        min_queries: int = 3,
    ):
        self.name = "degradation"
        self.enabled = True
        self.threshold = threshold
        self.min_queries = min_queries
        self.strategies = strategies or dict(DEFAULT_STRATEGIES)

    def evaluate(
        self,
        results_so_far: list[dict],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None:
        if len(results_so_far) < self.min_queries:
            return None

        degraded = count_degraded_queries(results_so_far)
        rate = degraded / len(results_so_far)

        if rate < self.threshold:
            return None

        warning_types = collect_warning_types(results_so_far)
        dominant = max(warning_types, key=warning_types.get) if warning_types else "unknown"  # type: ignore[arg-type]
        strategy = self.strategies.get(dominant, EscalationStrategy())

        return EscalationSignal(
            check_name=self.name,
            target=strategy.target,
            check_result={
                "degraded_rate": rate,
                "degraded_count": degraded,
                "total_evaluated": len(results_so_far),
                "warning_types": warning_types,
                "dominant_warning": dominant,
            },
            candidate_idx=candidate_idx,
            candidates_scored=candidate_idx + 1,
            candidates_skipped=n_total_candidates - candidate_idx - 1,
        )


def collect_warning_types(results: list[dict]) -> dict[str, int]:
    """Count occurrences of each warning type across results."""
    counts: Counter[str] = Counter()
    for r in results:
        counts.update(extract_warning_types(r))
    return dict(counts)


def build_degradation_checks(config: LoopConfig) -> list[DegradationCheck]:
    """Build enabled degradation checks from LoopConfig."""
    checks: list[DegradationCheck] = []
    threshold = getattr(config, "degradation_threshold", 0.0)
    if threshold > 0:
        checks.append(DegradationCheck(threshold=threshold))
    return checks


def _maybe_emit_backend_warning(
    state: LoopState,
    config: LoopConfig,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
) -> None:
    """Emit a one-shot backend warning after repeated degradation resets."""
    opt = state.opt_sp
    if opt.backend_warning_emitted or config.backend_warning_threshold <= 0:
        return
    if opt.degradation_reset_count < config.backend_warning_threshold:
        return

    opt.backend_warning_emitted = True
    count = opt.degradation_reset_count

    steps: set[str] = set()
    wtypes: dict[str, int] = {}
    for e in opt.escalation_journal:
        if e.get("problem_step"):
            steps.add(e["problem_step"])
        for wt, n in e.get("warning_types", {}).items():
            wtypes[wt] = wtypes.get(wt, 0) + n

    emit_phase(
        on_phase,
        CampaignPhase.BACKEND_WARNING,
        "notify",
        round=round_num,
        message=(
            f"Repeated pipeline degradation \u2014 {count} investigation "
            "cycles exhausted. Likely a backend server issue."
        ),
        advice=("Paste warnings + connector code into Claude Code \u2192 docs/connectors"),
        degradation_reset_count=count,
        problem_steps=sorted(steps),
        persistent_warning_types=wtypes,
    )
    logger.warning(
        "Backend warning at round %d (%d resets, steps: %s)",
        round_num,
        count,
        sorted(steps),
    )


def _rebuild_current_sp(
    state: LoopState,
    transition: Any,
    *,
    schema: PipelineSchema | None = None,
) -> None:
    """Update opt_sp from a transition result and rebuild current_sp."""
    new_opt = transition.opt_search_point
    new_opt.inherit_memory(state.opt_sp)
    state.opt_sp = new_opt
    cur = state.current_sp
    assert cur is not None
    base_params = getattr(transition, "pipeline_params", None) or cur.pipeline_params
    state.current_sp = state.opt_sp.to_job_search_point(
        base_pipeline_params=base_params,
        schema=schema,
    )


def _degradation_reset(
    state: LoopState,
    config: LoopConfig,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    reset_l3: bool = False,
) -> None:
    """Reset L2 (and optionally L3) counters during degradation investigation."""
    state.escalation.l2_stall_count = 0
    state.escalation.l2_round = 0
    if reset_l3:
        state.escalation.l3_stall_count = 0
        state.escalation.l3_round = 0
    state.opt_sp.degradation_reset_count += 1
    _maybe_emit_backend_warning(state, config, round_num, on_phase)


async def _degradation_reset_and_l2(
    state: LoopState,
    config: LoopConfig,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    obs: ObsLogger | None = None,
    trace_id: str | None = None,
    escalation_check_result: dict | None = None,
    reset_l3: bool = False,
) -> None:
    """Reset degradation counters and trigger a fresh L2 transition."""
    layer = "L2/L3" if reset_l3 else "L2"
    logger.debug(
        "%s patience exhausted during degradation — resetting at round %d",
        layer,
        round_num,
    )
    _degradation_reset(state, config, round_num, on_phase, reset_l3=reset_l3)
    await _do_l2_transition(
        state,
        config,
        round_num,
        on_phase,
        obs=obs,
        trace_id=trace_id,
        escalation_check_result=escalation_check_result,
    )


async def _do_l2_transition(
    state: LoopState,
    config: LoopConfig,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: ObsLogger | None = None,
    trace_id: str | None = None,
    escalation_check_result: dict | None = None,
) -> Any:
    """Perform L2 refine_strategy transition. Updates state in-place."""
    from promptpotter.application.optimization.nodes import layer_transitions
    from promptpotter.application.optimization.nodes.formatting import warning_summary
    from promptpotter.infrastructure.llm import client as _llm_client
    from promptpotter.infrastructure.tracing.observability_logger import observed_node

    assert state.current_sp is not None
    current_pp = state.current_sp.pipeline_params

    emit_phase(
        on_phase,
        CampaignPhase.REFINE_STRATEGY,
        "enter",
        round=round_num,
        l2_round=state.escalation.l2_round,
        stall_count=state.stall_count,
        current_params=state.opt_sp.optimizer_params,
        current_accuracy=state.current_accuracy,
        best_accuracy=state.best_accuracy,
    )

    client = _llm_client.get_llm_client()
    async with observed_node(f"l2_refine_r{round_num}", "llm/meta", obs=obs, trace_id=trace_id):
        # Pass round trajectory and last round's candidate scores for L2 intelligence
        _last_candidates = state.rounds[-1].candidate_scores if state.rounds else []
        transition = await layer_transitions.refine_strategy(
            state.opt_sp,
            client,
            model=config.model,
            temperature=config.l2_temperature,
            pipeline_params=current_pp,
            pipeline_schema=config.pipeline_schema,
            escalation_check_result=escalation_check_result,
            search_memory=state.search_memory,
            rounds=state.rounds,
            candidate_scores=_last_candidates,
        )
    # Rebuild first — inherit_memory preserves accumulated state,
    # then apply L2's new values to the rebuilt opt_sp.
    _rebuild_current_sp(state, transition, schema=config.pipeline_schema)
    if transition.task_context:
        state.opt_sp.task_context = transition.task_context
    state.opt_sp.l2_directive = transition.l2_directive
    state.escalation.record_l2_entry(state.best_accuracy, state.best_composite)
    # Build warning inventory one-liner for display
    _warned_count, _top_warning = warning_summary(state.opt_sp.warning_inventory)

    emit_phase(
        on_phase,
        CampaignPhase.REFINE_STRATEGY,
        "exit",
        round=round_num,
        l2_round=state.escalation.l2_round,
        param_changes_count=len(transition.opt_search_point.optimizer_params),
        task_context_changed=transition.task_context is not None,
        changes_description=transition.opt_search_point.changes_description or "",
        pipeline_params_changed=transition.pipeline_params is not None,
        pipeline_params=transition.pipeline_params,
        action=transition.action,
        warned_queries=_warned_count,
        top_warning=_top_warning,
        l2_prompt=transition.debug_prompt,
        l2_response=transition.debug_response,
    )
    # Flag next round as probe if L2 requested it
    from promptpotter.application.optimization.nodes.layer_transitions import TransitionAction

    if transition.action == TransitionAction.PROBE:
        state.enter_probe_mode()
        logger.debug("L2 requested probe — next round uses warned queries")

    logger.debug(
        "L2 refine_strategy at round %d (l2_round=%d)",
        round_num,
        state.escalation.l2_round,
    )
    return transition


async def _do_l3_transition(
    state: LoopState,
    config: LoopConfig,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: ObsLogger | None = None,
    trace_id: str | None = None,
) -> Any:
    """Perform L3 modify_plan transition. Updates state in-place."""
    from promptpotter.application.optimization.nodes import layer_transitions
    from promptpotter.infrastructure.llm import client as _llm_client
    from promptpotter.infrastructure.tracing.observability_logger import observed_node

    assert state.current_sp is not None
    current_pp = state.current_sp.pipeline_params

    l2_history = [
        {
            "l2_round": state.escalation.l2_round,
            "optimizer_params": state.opt_sp.optimizer_params,
            "accuracy_change": state.best_composite - state.escalation.best_composite_at_l3_entry,
        }
    ]
    emit_phase(
        on_phase,
        CampaignPhase.MODIFY_PLAN,
        "enter",
        round=round_num,
        l3_round=state.escalation.l3_round,
        l2_stall_count=state.escalation.l2_stall_count,
        current_plan_preview=str(state.opt_sp.plan)[:120],
    )

    client = _llm_client.get_llm_client()
    async with observed_node(
        f"l3_modify_plan_r{round_num}", "llm/meta", obs=obs, trace_id=trace_id
    ):
        transition = await layer_transitions.modify_plan(
            state.opt_sp,
            l2_history,
            client,
            model=config.model,
            temperature=config.l3_temperature,
            pipeline_params=current_pp,
            pipeline_schema=config.pipeline_schema,
            search_memory=state.search_memory,
        )
    _rebuild_current_sp(state, transition, schema=config.pipeline_schema)
    state.escalation.record_l3_entry(state.best_accuracy, state.best_composite)
    state.escalation.reset_for_l3(state.best_accuracy, state.best_composite)
    emit_phase(
        on_phase,
        CampaignPhase.MODIFY_PLAN,
        "exit",
        round=round_num,
        l3_round=state.escalation.l3_round,
        new_plan_preview=str(transition.opt_search_point.plan)[:120],
        changes_description=transition.opt_search_point.changes_description or "",
        pipeline_params_changed=transition.pipeline_params is not None,
    )
    logger.debug(
        "L3 modify_plan at round %d (l3_round=%d)",
        round_num,
        state.escalation.l3_round,
    )
    return transition


async def escalate_l2(
    state: LoopState,
    config: LoopConfig,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    on_checkpoint: Callable[[str], str | None] | None = None,
    obs: ObsLogger | None = None,
    trace_id: str | None = None,
    from_degradation: bool = False,
    escalation_check_result: dict | None = None,
) -> StopReason | None:
    """Handle L1→L2 escalation and optionally L2→L3.

    Returns a StopReason if the cycle should stop, or None to continue.
    When *from_degradation* is True, L2/L3 patience exhaustion resets counters
    instead of stopping — the degradation investigation loop continues.
    """
    from promptpotter.application.optimization.nodes.round_execution import PauseForReviewError
    from promptpotter.infrastructure.persistence.state import StopReason

    esc = state.escalation
    esc.record_l2_outcome(state.best_composite)

    l2_stalled = config.l2_patience is not None and esc.l2_stall_count >= config.l2_patience
    if not l2_stalled:
        await _do_l2_transition(
            state,
            config,
            round_num,
            on_phase,
            obs=obs,
            trace_id=trace_id,
            escalation_check_result=escalation_check_result,
        )
        # HITL checkpoint: pause after L2 generates new context, before L1 resumes
        if on_checkpoint:
            _ctrl = on_checkpoint("before_l2_scoring")
            if _ctrl == "pause":
                raise PauseForReviewError([], round_num, pause_point="before_l2_scoring")
            if _ctrl == "stop":
                return StopReason.USER_STOPPED
        return None

    # L2 stalled, L3 disabled → exhaust or reset
    if not config.enable_l3:
        if from_degradation:
            await _degradation_reset_and_l2(
                state,
                config,
                round_num,
                on_phase,
                obs=obs,
                trace_id=trace_id,
                escalation_check_result=escalation_check_result,
            )
            return None
        logger.debug(
            "L2 patience exhausted (%d stalls) at round %d",
            esc.l2_stall_count,
            round_num,
        )
        return StopReason.L2_PATIENCE

    # L2 stalled, L3 enabled — track L3 stall
    esc.record_l3_outcome(state.best_composite)
    l3_exhausted = config.l3_patience is not None and esc.l3_stall_count >= config.l3_patience
    if not l3_exhausted:
        await _do_l3_transition(
            state,
            config,
            round_num,
            on_phase,
            obs=obs,
            trace_id=trace_id,
        )
        return None

    # L3 exhausted → exhaust or reset
    if from_degradation:
        await _degradation_reset_and_l2(
            state,
            config,
            round_num,
            on_phase,
            obs=obs,
            trace_id=trace_id,
            escalation_check_result=escalation_check_result,
            reset_l3=True,
        )
        return None
    logger.debug(
        "L3 patience exhausted (%d stalls) at round %d",
        esc.l3_stall_count,
        round_num,
    )
    return StopReason.L3_PATIENCE
