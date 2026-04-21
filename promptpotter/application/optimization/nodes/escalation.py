"""Degradation check + L1→L2→L3 escalation orchestration."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.nodes.critique_payload import extract_warning_types
from promptpotter.application.optimization.nodes.layer_transitions import (
    L2RefineStrategy,
    L3ModifyPlan,
    LayerTransition,
)
from promptpotter.application.optimization.phases import CampaignPhase, PhaseEvent, emit_phase
from promptpotter.application.scoring.metrics import count_degraded_queries
from promptpotter.domain.analysis import EscalationSignal, EscalationTarget
from promptpotter.infrastructure.tracing.events import LayerApplied
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
    "build_escalation_entry",
    "escalate_l2",
]


def build_escalation_entry(
    round_num: int,
    check_result: dict[str, Any],
    current_pipeline_params: dict | None,
) -> dict[str, Any]:
    """Shape a journal entry from a DegradationCheck result + live pipeline params."""
    dominant = check_result.get("dominant_warning", "unknown:unknown")
    problem_step = dominant.split(":")[0] if ":" in dominant else "unknown"
    step_cfg = (current_pipeline_params or {}).get(problem_step, {})
    return {
        "round": round_num,
        "degraded_rate": check_result.get("degraded_rate", 0),
        "problem_step": problem_step,
        "step_config": dict(step_cfg) if isinstance(step_cfg, dict) else {},
        "warning_types": check_result.get("warning_types", {}),
        "outcome_degraded_rate": None,
    }


# Warnings that are deterministic — one occurrence ends the candidate (no retry).
FATAL_WARNINGS: frozenset[str] = frozenset(
    {
        "llm_only:empty_content_reasoning_fallback",
    }
)


class DegradationCheck:
    """Eliminates a candidate when warnings look terminal (fatal fast-path + rate-based)."""

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
        # Fast-path: a single fatal warning on the newest query ends the candidate.
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
    """Eliminates candidates whose LLM consistently returns empty predictions."""

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


def _maybe_emit_backend_warning(
    state: LoopState,
    config: CampaignConfig,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
) -> None:
    """One-shot backend advisory when degradation resets exceed threshold."""
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


async def _do_layer_transition(
    transition: LayerTransition,
    state: LoopState,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    obs: ObservabilityBridge | None,
    obs_campaign_id: str,
    run_kwargs: dict[str, Any],
    enter_payload: dict[str, Any],
    exit_payload_fn: Callable[[Any], dict[str, Any]],
    temperature: float,
) -> Any:
    """Unified L2/L3 orchestrator: emit enter → call → adopt → emit LayerApplied → side-effects → emit exit.

    Layer-specific prep (run_kwargs, enter/exit payloads, temperature) is
    passed in by the thin L2/L3 callers below. Layer-specific state tail
    lives on ``transition.apply_side_effects`` (called after adopt).
    """
    from promptpotter.infrastructure.llm import client as _llm_client
    from promptpotter.infrastructure.tracing import observed_node

    assert state.current_sp is not None
    client = _llm_client.get_llm_client()
    current_pp = state.current_sp.pipeline_params
    observed_name = f"{transition.template_name}_r{round_num}"

    emit_phase(on_phase, transition.phase, "enter", round=round_num, **enter_payload)
    async with observed_node(
        observed_name,
        "llm/meta",
        obs=obs,
        campaign_id=obs_campaign_id,
        round_num=round_num,
    ):
        result = await transition.run(
            state.opt_sp,
            client,
            model=config.optimizer_llm.model,
            temperature=temperature,
            pipeline_params=current_pp,
            pipeline_schema=pipeline_schema,
            search_memory=state.search_memory,
            **run_kwargs,
        )
    state.adopt_transition(
        result.opt_search_point,
        result.pipeline_params,
        schema=pipeline_schema,
    )
    if obs is not None:
        with graceful(f"LayerApplied({transition.layer}) emit failed"):
            obs.emit_write_point(
                LayerApplied,
                layer=transition.layer,
                campaign_id=obs_campaign_id,
                round_num=round_num,
                changes_description=result.opt_search_point.changes_description or "",
            )
    transition.apply_side_effects(state, result, round_num)
    emit_phase(on_phase, transition.phase, "exit", round=round_num, **exit_payload_fn(result))
    return result


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
    """L2 refine_strategy thin wrapper — builds args, delegates to ``_do_layer_transition``."""
    from promptpotter.application.optimization.nodes.formatting import warning_summary

    last_candidates = state.rounds[-1].candidate_scores if state.rounds else []

    enter_payload = {
        "l2_round": state.escalation.l2.round,
        "l1_stall_count": state.escalation.l1_stall_count,
        "current_params": state.opt_sp.optimizer_params,
        "current_accuracy": state.current_accuracy,
        "best_accuracy": state.best_accuracy,
    }

    def _exit(result: Any) -> dict[str, Any]:
        warned_count, top_warning = warning_summary(state.opt_sp.memory.warning_inventory)
        return {
            "l2_round": state.escalation.l2.round,
            "param_changes_count": len(result.opt_search_point.optimizer_params),
            "task_context_changed": result.task_context is not None,
            "changes_description": result.opt_search_point.changes_description or "",
            "pipeline_params_changed": result.pipeline_params is not None,
            "pipeline_params": result.pipeline_params,
            "action": result.action,
            "warned_queries": warned_count,
            "top_warning": top_warning,
            "l2_prompt": result.debug_prompt,
            "l2_response": result.debug_response,
        }

    return await _do_layer_transition(
        L2RefineStrategy(),
        state,
        config,
        pipeline_schema,
        round_num,
        on_phase,
        obs=obs,
        obs_campaign_id=obs_campaign_id,
        run_kwargs={
            "rounds": state.rounds,
            "candidate_scores": last_candidates,
            "escalation_check_result": escalation_check_result,
        },
        enter_payload=enter_payload,
        exit_payload_fn=_exit,
        temperature=config.optimization.l2_temperature,
    )


async def _do_l3_transition(
    state: LoopState,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: ObservabilityBridge | None = None,
    obs_campaign_id: str = "",
) -> Any:
    """L3 modify_plan thin wrapper — builds args, delegates to ``_do_layer_transition``."""
    l2_history = [
        {
            "l2_round": state.escalation.l2.round,
            "optimizer_params": state.opt_sp.optimizer_params,
            "accuracy_change": state.best_composite - state.escalation.l3.best_composite_at_entry,
        }
    ]

    enter_payload = {
        "l3_round": state.escalation.l3.round,
        "l2_stall_count": state.escalation.l2.stall_count,
        "current_plan_preview": str(state.opt_sp.plan)[:120],
    }

    def _exit(result: Any) -> dict[str, Any]:
        return {
            "l3_round": state.escalation.l3.round,
            "new_plan_preview": str(result.opt_search_point.plan)[:120],
            "changes_description": result.opt_search_point.changes_description or "",
            "pipeline_params_changed": result.pipeline_params is not None,
        }

    return await _do_layer_transition(
        L3ModifyPlan(),
        state,
        config,
        pipeline_schema,
        round_num,
        on_phase,
        obs=obs,
        obs_campaign_id=obs_campaign_id,
        run_kwargs={"l2_history": l2_history},
        enter_payload=enter_payload,
        exit_payload_fn=_exit,
        temperature=config.optimization.l3_temperature,
    )


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
    """Exhaust patience (return stop) OR reset counters + run L2."""
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
    """L1→L2 (and optional L2→L3) escalation; from_degradation resets counters instead of stopping."""
    from promptpotter.application.campaign.decisions import record_decision
    from promptpotter.application.optimization.nodes.round_execution import PauseForReviewError
    from promptpotter.application.optimization.phases import StopReason

    opt = config.optimization
    esc = state.escalation
    esc.l2.record_outcome(state.best_composite)

    l2_stalled = opt.l2_patience is not None and esc.l2.stall_count >= opt.l2_patience
    # entry_round = round whose rescored best_composite is the stall baseline (-1 = never fired).
    entry_round_l2 = esc.l2.round if esc.l2.round > 0 else -1
    record_decision(
        state.pending_decisions,
        "l2_escalation_trigger",
        {
            "round_num": round_num,
            "l2_patience": opt.l2_patience,
            "from_degradation": from_degradation,
            "entry_round": entry_round_l2,
        },
        not l2_stalled,
        data={
            "l2_round": esc.l2.round,
            "stall_count": esc.l2.stall_count,
            "best_composite_at_entry": esc.l2.best_composite_at_entry,
            "best_composite_this_round": state.best_composite,
            "best_accuracy": state.best_accuracy,
        },
    )
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

    esc.l3.record_outcome(state.best_composite)
    l3_exhausted = opt.l3_patience is not None and esc.l3.stall_count >= opt.l3_patience
    entry_round_l3 = esc.l3.round if esc.l3.round > 0 else -1
    record_decision(
        state.pending_decisions,
        "l3_escalation_trigger",
        {
            "round_num": round_num,
            "l3_patience": opt.l3_patience,
            "entry_round": entry_round_l3,
        },
        not l3_exhausted,
        data={
            "l3_round": esc.l3.round,
            "stall_count": esc.l3.stall_count,
            "best_composite_at_entry": esc.l3.best_composite_at_entry,
            "best_composite_this_round": state.best_composite,
        },
    )
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
