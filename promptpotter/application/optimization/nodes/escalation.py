"""Degradation check + L1→L2→L3 escalation orchestration.

Two concerns live here:

1. ``DegradationCheck`` — per-query check that can fire mid-evaluation
   and abort remaining queries/candidates.  Duck-types with
   ``EliminationCheck`` in ``search/failure_groups.py``.
2. ``escalate_l2`` — state-machine that decides whether a stall triggers
   L2 refine_strategy, L3 modify_plan, counter reset, or stop.

Pure data types (``EscalationSignal``, ``EscalationTarget``) live in
``promptpotter.domain.analysis``.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.nodes.critique_payload import extract_warning_types
from promptpotter.application.optimization.phases import CampaignPhase, PhaseEvent, emit_phase
from promptpotter.application.scoring.metrics import count_degraded_queries
from promptpotter.domain.analysis import EscalationSignal, EscalationTarget
from promptpotter.infrastructure.tracing.events import L2Applied, L3Applied
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.optimization.loop_state import LoopState
    from promptpotter.application.optimization.phases import StopReason
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = [
    "FATAL_WARNINGS",
    "DegradationCheck",
    "EmptyOutputCheck",
    "build_degradation_checks",
    "escalate_l2",
]


# Warnings whose first occurrence is already conclusive evidence that the
# candidate's config is deterministically broken for this dataset — no point
# spending more queries to confirm. Currently: reasoning models (e.g.
# gpt-oss-120b) whose hidden reasoning trace eats the entire max_tokens budget
# and leaves empty visible content. Hardcoded invariant, not a tunable.
FATAL_WARNINGS: frozenset[str] = frozenset(
    {
        "llm_only:empty_content_reasoning_fallback",
    }
)


# ---------------------------------------------------------------------------
# Degradation check (mid-eval abort)
# ---------------------------------------------------------------------------


class DegradationCheck:
    """Eliminates a candidate when its warnings look terminal.

    Two paths, in order:

    1. **Fatal fast-path.** If the newest query carries any warning in
       ``FATAL_WARNINGS`` (e.g. ``llm_only:empty_content_reasoning_fallback``),
       fire immediately — bypass ``min_queries`` and ``threshold``. These
       codes are deterministic for the whole config, so one occurrence is
       conclusive and the remaining queries would just waste backend calls.
    2. **Rate-based.** Otherwise, once at least ``min_queries`` results
       are in, fire when the degraded fraction meets ``threshold``.

    Target is ``ELIMINATE_CANDIDATE`` — the check attributes the failure
    to the specific candidate that produced it, and the per-candidate
    absorption path in ``score._score_candidates`` synthesises a
    ``RuntimeFailure`` from ``check_result`` and attaches it to that
    candidate's ``OptSearchPoint.memory.runtime_failures``. The winner
    is never penalised for a losing candidate's runtime issues; L2 sees
    the evidence via the candidate_scores self-healing rail next round.
    """

    name = "degradation"

    def __init__(self, threshold: float = 0.4, min_queries: int = 3):
        self.enabled = True
        self.threshold = threshold
        self.min_queries = min_queries

    def evaluate(
        self,
        results_so_far: list[dict],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None:
        # Fast-path: a single fatal warning on the newest query ends the
        # candidate immediately. Scanning only results_so_far[-1] is enough
        # because this method runs after every query — an earlier fatal
        # would already have fired.
        if results_so_far:
            latest_warnings = extract_warning_types(results_so_far[-1])
            fatal_hit = next((w for w in latest_warnings if w in FATAL_WARNINGS), None)
            if fatal_hit is not None:
                n = len(results_so_far)
                return EscalationSignal(
                    check_name=self.name,
                    target=EscalationTarget.ELIMINATE_CANDIDATE,
                    check_result={
                        "degraded_rate": 1.0,
                        "degraded_count": n,
                        "total_evaluated": n,
                        "warning_types": {fatal_hit: 1},
                        "dominant_warning": fatal_hit,
                        "fatal": True,
                    },
                    candidate_idx=candidate_idx,
                    candidates_scored=candidate_idx + 1,
                    candidates_skipped=n_total_candidates - candidate_idx - 1,
                )

        if len(results_so_far) < self.min_queries:
            return None

        degraded = count_degraded_queries(results_so_far)
        rate = degraded / len(results_so_far)
        if rate < self.threshold:
            return None

        warning_types: Counter[str] = Counter()
        for r in results_so_far:
            warning_types.update(extract_warning_types(r))
        dominant = max(warning_types, key=warning_types.get) if warning_types else "unknown"  # type: ignore[arg-type]

        return EscalationSignal(
            check_name=self.name,
            target=EscalationTarget.ELIMINATE_CANDIDATE,
            check_result={
                "degraded_rate": rate,
                "degraded_count": degraded,
                "total_evaluated": len(results_so_far),
                "warning_types": dict(warning_types),
                "dominant_warning": dominant,
            },
            candidate_idx=candidate_idx,
            candidates_scored=candidate_idx + 1,
            candidates_skipped=n_total_candidates - candidate_idx - 1,
        )


class EmptyOutputCheck:
    """Eliminates a candidate whose LLM consistently returns empty predictions.

    Catches candidates whose prompt blew the max_tokens budget mid-reasoning
    and returned 0 chars — visible to the scorer as ``predicted == ""`` and
    ``score == 0``, but indistinguishable from a legitimate wrong answer
    without this check. Conforms to the same protocol as ``DegradationCheck``
    and ``EliminationCheck``; uses ``ELIMINATE_CANDIDATE`` so the per-candidate
    absorption logic in ``score._score_candidates`` skips it without aborting
    the round.
    """

    name = "empty_output"

    def __init__(self, threshold: float = 0.5, min_queries: int = 3) -> None:
        self.enabled = threshold > 0
        self.threshold = threshold
        self.min_queries = min_queries

    def evaluate(
        self,
        results_so_far: list[dict],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None:
        if len(results_so_far) < self.min_queries:
            return None
        empty = sum(1 for r in results_so_far if not str(r.get("predicted") or "").strip())
        rate = empty / len(results_so_far)
        if rate < self.threshold:
            return None
        return EscalationSignal(
            check_name=self.name,
            target=EscalationTarget.ELIMINATE_CANDIDATE,
            check_result={
                "empty_count": empty,
                "empty_rate": rate,
                "total_evaluated": len(results_so_far),
            },
            candidate_idx=candidate_idx,
            candidates_scored=candidate_idx + 1,
            candidates_skipped=n_total_candidates - candidate_idx - 1,
        )


def build_degradation_checks(config: CampaignConfig) -> list[Any]:
    """Return the list of enabled per-query checks (degradation + empty-output)."""
    checks: list[Any] = []
    opt = config.optimization
    if opt.degradation_threshold > 0:
        checks.append(DegradationCheck(threshold=opt.degradation_threshold))
    if opt.empty_output_threshold > 0:
        checks.append(EmptyOutputCheck(threshold=opt.empty_output_threshold))
    return checks


# ---------------------------------------------------------------------------
# Backend-warning one-shot (after repeated degradation resets)
# ---------------------------------------------------------------------------


def _maybe_emit_backend_warning(
    state: LoopState,
    config: CampaignConfig,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
) -> None:
    """Emit a one-shot backend advisory once degradation resets pile up."""
    mem = state.opt_sp.memory
    threshold = config.optimization.backend_warning_threshold
    if mem.backend_warning_emitted or threshold <= 0:
        return
    if mem.degradation_reset_count < threshold:
        return

    mem.backend_warning_emitted = True
    count = mem.degradation_reset_count
    steps = sorted({e["problem_step"] for e in mem.escalation_journal if e.get("problem_step")})
    wtypes: Counter[str] = Counter()
    for e in mem.escalation_journal:
        wtypes.update(e.get("warning_types") or {})

    emit_phase(
        on_phase,
        CampaignPhase.BACKEND_WARNING,
        "notify",
        round=round_num,
        message=(
            f"Repeated pipeline degradation \u2014 {count} investigation "
            "cycles exhausted. Likely a backend server issue."
        ),
        advice="Paste warnings + connector code into Claude Code \u2192 docs/connectors",
        degradation_reset_count=count,
        problem_steps=steps,
        persistent_warning_types=dict(wtypes),
    )
    logger.warning("Backend warning at round %d (%d resets, steps: %s)", round_num, count, steps)


# ---------------------------------------------------------------------------
# L2/L3 transition execution (shared scaffolding)
# ---------------------------------------------------------------------------


async def _run_layer_transition(
    phase: CampaignPhase,
    state: LoopState,
    schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    obs: ObservabilityBridge | None,
    obs_campaign_id: str,
    observed_name: str,
    enter_payload: dict[str, Any],
    call: Callable[[], Awaitable[Any]],
    exit_payload: Callable[[Any], dict[str, Any]],
) -> Any:
    """Run an L2/L3 transition: emit enter → observed call → apply → emit exit."""
    from promptpotter.infrastructure.tracing import observed_node

    emit_phase(on_phase, phase, "enter", round=round_num, **enter_payload)
    async with observed_node(
        observed_name,
        "llm/meta",
        obs=obs,
        campaign_id=obs_campaign_id,
        round_num=round_num,
    ):
        transition = await call()
    state.adopt_transition(
        transition.opt_search_point,
        transition.pipeline_params,
        schema=schema,
    )
    if obs is not None:
        event_cls: type = L2Applied if phase == CampaignPhase.REFINE_STRATEGY else L3Applied
        with graceful(f"{event_cls.__name__} emit failed"):
            obs.emit_write_point(
                event_cls,
                campaign_id=obs_campaign_id,
                round_num=round_num,
                changes_description=transition.opt_search_point.changes_description or "",
            )
    emit_phase(on_phase, phase, "exit", round=round_num, **exit_payload(transition))
    return transition


async def _do_l2_transition(
    state: LoopState,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: ObservabilityBridge | None = None,
    obs_campaign_id: str = "",
    escalation_check_result: dict | None = None,
) -> Any:
    """Perform L2 refine_strategy transition. Updates state in-place."""
    from promptpotter.application.optimization.nodes import layer_transitions
    from promptpotter.application.optimization.nodes.formatting import warning_summary
    from promptpotter.application.optimization.nodes.layer_transitions import TransitionAction
    from promptpotter.infrastructure.llm import client as _llm_client

    assert state.current_sp is not None
    current_pp = state.current_sp.pipeline_params
    client = _llm_client.get_llm_client()
    last_candidates = state.rounds[-1].candidate_scores if state.rounds else []

    enter = {
        "l2_round": state.escalation.l2.round,
        "l1_stall_count": state.escalation.l1_stall_count,
        "current_params": state.opt_sp.optimizer_params,
        "current_accuracy": state.current_accuracy,
        "best_accuracy": state.best_accuracy,
    }

    async def _call() -> Any:
        return await layer_transitions.refine_strategy(
            state.opt_sp,
            client,
            model=config.optimizer_llm.model,
            temperature=config.optimization.l2_temperature,
            pipeline_params=current_pp,
            pipeline_schema=pipeline_schema,
            escalation_check_result=escalation_check_result,
            search_memory=state.search_memory,
            rounds=state.rounds,
            candidate_scores=last_candidates,
        )

    def _exit(transition: Any) -> dict[str, Any]:
        warned_count, top_warning = warning_summary(state.opt_sp.memory.warning_inventory)
        return {
            "l2_round": state.escalation.l2.round,
            "param_changes_count": len(transition.opt_search_point.optimizer_params),
            "task_context_changed": transition.task_context is not None,
            "changes_description": transition.opt_search_point.changes_description or "",
            "pipeline_params_changed": transition.pipeline_params is not None,
            "pipeline_params": transition.pipeline_params,
            "action": transition.action,
            "warned_queries": warned_count,
            "top_warning": top_warning,
            "l2_prompt": transition.debug_prompt,
            "l2_response": transition.debug_response,
        }

    transition = await _run_layer_transition(
        CampaignPhase.REFINE_STRATEGY,
        state,
        pipeline_schema,
        round_num,
        on_phase,
        obs=obs,
        obs_campaign_id=obs_campaign_id,
        observed_name=f"l2_refine_r{round_num}",
        enter_payload=enter,
        call=_call,
        exit_payload=_exit,
    )

    if transition.task_context:
        state.opt_sp.task_context = transition.task_context
    state.opt_sp.memory.l2_directive = transition.l2_directive
    state.escalation.l2.record_entry(state.best_accuracy, state.best_composite)
    if transition.action == TransitionAction.PROBE:
        state.probe_next_round = True
        logger.debug("L2 requested probe — next round uses warned queries")
    logger.debug(
        "L2 refine_strategy at round %d (l2_round=%d)", round_num, state.escalation.l2.round
    )
    return transition


async def _do_l3_transition(
    state: LoopState,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: ObservabilityBridge | None = None,
    obs_campaign_id: str = "",
) -> Any:
    """Perform L3 modify_plan transition. Updates state in-place."""
    from promptpotter.application.optimization.nodes import layer_transitions
    from promptpotter.infrastructure.llm import client as _llm_client

    assert state.current_sp is not None
    current_pp = state.current_sp.pipeline_params
    client = _llm_client.get_llm_client()

    l2_history = [
        {
            "l2_round": state.escalation.l2.round,
            "optimizer_params": state.opt_sp.optimizer_params,
            "accuracy_change": state.best_composite - state.escalation.l3.best_composite_at_entry,
        }
    ]

    enter = {
        "l3_round": state.escalation.l3.round,
        "l2_stall_count": state.escalation.l2.stall_count,
        "current_plan_preview": str(state.opt_sp.plan)[:120],
    }

    async def _call() -> Any:
        return await layer_transitions.modify_plan(
            state.opt_sp,
            l2_history,
            client,
            model=config.optimizer_llm.model,
            temperature=config.optimization.l3_temperature,
            pipeline_params=current_pp,
            pipeline_schema=pipeline_schema,
            search_memory=state.search_memory,
        )

    def _exit(transition: Any) -> dict[str, Any]:
        return {
            "l3_round": state.escalation.l3.round,
            "new_plan_preview": str(transition.opt_search_point.plan)[:120],
            "changes_description": transition.opt_search_point.changes_description or "",
            "pipeline_params_changed": transition.pipeline_params is not None,
        }

    transition = await _run_layer_transition(
        CampaignPhase.MODIFY_PLAN,
        state,
        pipeline_schema,
        round_num,
        on_phase,
        obs=obs,
        obs_campaign_id=obs_campaign_id,
        observed_name=f"l3_modify_plan_r{round_num}",
        enter_payload=enter,
        call=_call,
        exit_payload=_exit,
    )

    state.escalation.l3.record_entry(state.best_accuracy, state.best_composite)
    state.escalation.reset_for_l3(state.best_accuracy, state.best_composite)
    logger.debug("L3 modify_plan at round %d (l3_round=%d)", round_num, state.escalation.l3.round)
    return transition


# ---------------------------------------------------------------------------
# Escalation state machine
# ---------------------------------------------------------------------------


async def _exhaust_or_reset(
    state: LoopState,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    layer: str,
    stall_count: int,
    from_degradation: bool,
    stop_reason: StopReason,
    reset_l3: bool,
    obs: ObservabilityBridge | None,
    obs_campaign_id: str,
    escalation_check_result: dict | None,
) -> StopReason | None:
    """Either exhaust patience (return stop) or reset counters + run L2."""
    if not from_degradation:
        logger.debug("%s patience exhausted (%d stalls) at round %d", layer, stall_count, round_num)
        return stop_reason

    logger.debug(
        "%s patience exhausted during degradation — resetting at round %d", layer, round_num
    )
    state.escalation.l2.stall_count = 0
    state.escalation.l2.round = 0
    if reset_l3:
        state.escalation.l3.stall_count = 0
        state.escalation.l3.round = 0
    state.opt_sp.memory.degradation_reset_count += 1
    _maybe_emit_backend_warning(state, config, round_num, on_phase)
    await _do_l2_transition(
        state,
        config,
        pipeline_schema,
        round_num,
        on_phase,
        obs=obs,
        obs_campaign_id=obs_campaign_id,
        escalation_check_result=escalation_check_result,
    )
    return None


async def escalate_l2(
    state: LoopState,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    on_checkpoint: Callable[[str], str | None] | None = None,
    obs: ObservabilityBridge | None = None,
    obs_campaign_id: str = "",
    from_degradation: bool = False,
    escalation_check_result: dict | None = None,
) -> StopReason | None:
    """Handle L1→L2 escalation and optionally L2→L3.

    Returns a ``StopReason`` if the cycle should stop, or ``None`` to
    continue.  When ``from_degradation`` is True, L2/L3 patience
    exhaustion resets counters instead of stopping — the degradation
    investigation loop continues.
    """
    from promptpotter.application.optimization.nodes.round_execution import PauseForReviewError
    from promptpotter.application.optimization.phases import StopReason

    opt = config.optimization
    esc = state.escalation
    esc.l2.record_outcome(state.best_composite)

    l2_stalled = opt.l2_patience is not None and esc.l2.stall_count >= opt.l2_patience
    if not l2_stalled:
        await _do_l2_transition(
            state,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            obs_campaign_id=obs_campaign_id,
            escalation_check_result=escalation_check_result,
        )
        if on_checkpoint:
            ctrl = on_checkpoint("before_l2_scoring")
            if ctrl == "pause":
                raise PauseForReviewError([], round_num, pause_point="before_l2_scoring")
            if ctrl == "stop":
                return StopReason.USER_STOPPED
        return None

    # L2 stalled, L3 disabled → exhaust or reset
    if not opt.enable_l3:
        return await _exhaust_or_reset(
            state,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            layer="L2",
            stall_count=esc.l2.stall_count,
            from_degradation=from_degradation,
            stop_reason=StopReason.L2_PATIENCE,
            reset_l3=False,
            obs=obs,
            obs_campaign_id=obs_campaign_id,
            escalation_check_result=escalation_check_result,
        )

    # L2 stalled, L3 enabled — track L3 stall
    esc.l3.record_outcome(state.best_composite)
    l3_exhausted = opt.l3_patience is not None and esc.l3.stall_count >= opt.l3_patience
    if not l3_exhausted:
        await _do_l3_transition(
            state,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            obs_campaign_id=obs_campaign_id,
        )
        return None

    # L3 exhausted → exhaust or reset
    return await _exhaust_or_reset(
        state,
        config,
        pipeline_schema,
        round_num,
        on_phase,
        layer="L3",
        stall_count=esc.l3.stall_count,
        from_degradation=from_degradation,
        stop_reason=StopReason.L3_PATIENCE,
        reset_l3=True,
        obs=obs,
        obs_campaign_id=obs_campaign_id,
        escalation_check_result=escalation_check_result,
    )
