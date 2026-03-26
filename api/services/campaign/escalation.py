"""EscalationCheck framework and L2/L3 escalation execution.

Two layers:
1. **EscalationCheck framework** — per-query checks run during
   ``evaluate_prompt_batch()`` that can abort evaluation early.
2. **Escalation execution** — L1→L2→L3 layer transition orchestration
   triggered when the feedback cycle stalls.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from api.models.phase_event import PhaseEvent
from api.services.campaign.campaign_lifecycle import emit_phase

if TYPE_CHECKING:
    from api.services.campaign.models import CycleConfig, StopReason, _LoopState
    from api.services.obs.observability_logger import ObsLogger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------


@dataclass
class EscalationSignal:
    """Signal emitted when an EscalationCheck triggers mid-evaluation."""

    check_name: str
    target: str  # "retry" | "l2" | "l3" | "abort"
    context: dict[str, Any]
    candidate_idx: int
    candidates_evaluated: int
    candidates_skipped: int

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


@dataclass
class EscalationStrategy:
    """Configurable response to a specific warning type."""

    target: str = "l2"


DEFAULT_STRATEGIES: dict[str, EscalationStrategy] = {
    "web_search:partial_scrape": EscalationStrategy(target="l2"),
}



# ---------------------------------------------------------------------------
# Exception for mid-eval abort
# ---------------------------------------------------------------------------


class EscalationError(Exception):
    """Raised inside evaluate_prompt_batch when a check fires."""

    def __init__(self, signal: EscalationSignal, partial_results: list[dict]):
        self.signal = signal
        self.partial_results = partial_results
        super().__init__(f"EscalationCheck '{signal.check_name}' triggered")


# ---------------------------------------------------------------------------
# EscalationCheck ABC + DegradationCheck
# ---------------------------------------------------------------------------


class EscalationCheck(ABC):
    """Base class for mid-evaluation escalation checks."""

    name: str = ""
    enabled: bool = True
    strategies: dict[str, EscalationStrategy] = field(default_factory=dict)

    @abstractmethod
    def evaluate(
        self,
        results_so_far: list[dict],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None:
        """Check results accumulated so far. Return signal to abort, or None."""
        ...


class DegradationCheck(EscalationCheck):
    """Triggers when degraded query fraction exceeds threshold."""

    def __init__(
        self,
        threshold: float = 0.4,
        strategies: dict[str, EscalationStrategy] | None = None,
    ):
        self.name = "degradation"
        self.enabled = True
        self.threshold = threshold
        self.strategies = strategies or dict(DEFAULT_STRATEGIES)

    def evaluate(
        self,
        results_so_far: list[dict],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None:
        if not results_so_far:
            return None

        degraded = _count_degraded(results_so_far)
        rate = degraded / len(results_so_far)

        if rate < self.threshold:
            return None

        warning_types = collect_warning_types(results_so_far)
        dominant = max(warning_types, key=warning_types.get) if warning_types else "unknown"
        strategy = self.strategies.get(dominant, EscalationStrategy())

        return EscalationSignal(
            check_name=self.name,
            target=strategy.target,
            context={
                "degraded_rate": rate,
                "degraded_count": degraded,
                "total_evaluated": len(results_so_far),
                "warning_types": warning_types,
                "dominant_warning": dominant,
            },
            candidate_idx=candidate_idx,
            candidates_evaluated=candidate_idx + 1,
            candidates_skipped=n_total_candidates - candidate_idx - 1,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_degraded(results: list[dict]) -> int:
    return sum(
        1 for r in results
        if (r.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings")
    )


def collect_warning_types(results: list[dict]) -> dict[str, int]:
    """Count occurrences of each warning type across results."""
    counts: Counter[str] = Counter()
    for r in results:
        for w in (
            (r.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings") or []
        ):
            if isinstance(w, dict):
                wtype = f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}"
            else:
                wtype = "unknown"
            counts[wtype] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_escalation_checks(config: "CycleConfig") -> list[EscalationCheck]:
    """Build enabled escalation checks from CycleConfig."""
    checks: list[EscalationCheck] = []
    threshold = getattr(config, "degradation_threshold", 0.0)
    if threshold > 0:
        checks.append(DegradationCheck(threshold=threshold))
    return checks


# ---------------------------------------------------------------------------
# L2/L3 escalation execution
# ---------------------------------------------------------------------------


def _maybe_emit_backend_warning(
    state: "_LoopState",
    config: "CycleConfig",
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
        on_phase, "backend_warning", "notify", round=round_num,
        message=(
            f"Repeated pipeline degradation \u2014 {count} investigation "
            "cycles exhausted. Likely a backend server issue."
        ),
        advice=(
            "Paste warnings + connector code into Claude Code "
            "\u2192 docs/connectors"
        ),
        degradation_reset_count=count,
        problem_steps=sorted(steps),
        persistent_warning_types=wtypes,
    )
    logger.warning(
        "Backend warning at round %d (%d resets, steps: %s)",
        round_num, count, sorted(steps),
    )


def _degradation_reset(
    state: "_LoopState",
    config: "CycleConfig",
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    reset_l3: bool = False,
) -> None:
    """Reset L2 (and optionally L3) counters during degradation investigation."""
    state.l2_stall_count = 0
    state.l2_round = 0
    if reset_l3:
        state.l3_stall_count = 0
        state.l3_round = 0
    state.opt_sp.degradation_reset_count += 1
    _maybe_emit_backend_warning(state, config, round_num, on_phase)


async def _do_l2_transition(
    state: "_LoopState",
    config: "CycleConfig",
    round_num: int,
    eval_data: list[dict],
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: "ObsLogger | None" = None,
    trace_id: str | None = None,
    escalation_context: dict | None = None,
) -> Any:
    """Perform L2 refine_context transition. Updates state in-place."""
    from api.services.campaign import layer_transitions
    from api.services import llm_client as _llm_client
    from api.services.obs.node_tracer import observed_step
    from api.services.campaign.critique_stats import warning_summary

    current_pp = state.current_sp.pipeline_params

    stalled_rounds = [
        {
            "round": r.round,
            "accuracy": r.accuracy,
            "results": r.results,
        }
        for r in state.rounds[-config.patience:]
    ]
    emit_phase(on_phase, "refine_context", "enter", round=round_num,
                l2_round=state.l2_round,
                stall_count=state.stall_count,
                current_params=state.opt_sp.optimizer_params,
                current_accuracy=state.current_accuracy,
                best_accuracy=state.best_accuracy)

    client = _llm_client.get_llm_client(config.provider)
    async with observed_step(f"l2_refine_r{round_num}", "llm/meta",
                             obs=obs, trace_id=trace_id):
        tr = await layer_transitions.refine_context(
            state.opt_sp, stalled_rounds, eval_data, client,
            model=config.model,
            temperature=config.l2_temperature,
            pipeline_params=current_pp,
            pipeline_schema=config.pipeline_schema,
            escalation_context=escalation_context,
        )
    # Update task_context if L2 refined it
    if tr.task_context:
        state.opt_sp.task_context = tr.task_context
    # Store L2 directive for next L1 round (sliding window=1)
    state.opt_sp.l2_directive = tr.l2_directive
    # L2 does NOT set pipeline_params — only L1 Generate does that
    # Update opt_sp from L2 result, then rebuild JobSearchPoint
    state.opt_sp = tr.opt_search_point
    state.current_sp = state.opt_sp.to_job_search_point(
        model=state.current_sp.model,
        temperature=state.current_sp.temperature,
        base_pipeline_params=state.current_sp.pipeline_params,
    )
    state.l2_round += 1
    state.best_accuracy_at_l2_entry = state.best_accuracy
    state.best_composite_at_l2_entry = state.best_composite
    # Build warning inventory one-liner for display
    _warned_count, _top_warning = warning_summary(state.opt_sp.warning_inventory)

    emit_phase(on_phase, "refine_context", "exit", round=round_num,
                l2_round=state.l2_round,
                param_changes_count=len(tr.opt_search_point.optimizer_params),
                task_context_changed=tr.task_context is not None,
                changes_description=tr.opt_search_point.changes_description or "",
                pipeline_params_changed=tr.pipeline_params is not None,
                pipeline_params=tr.pipeline_params,
                action=tr.action,
                warned_queries=_warned_count,
                top_warning=_top_warning,
                l2_prompt=tr.debug_prompt,
                l2_response=tr.debug_response)
    # Flag next round as probe if L2 requested it
    if tr.action == "probe":
        state.probe_next_round = True
        logger.info("L2 requested probe — next round uses warned queries")

    logger.info(
        "L2 refine_context at round %d (l2_round=%d)",
        round_num, state.l2_round,
    )
    return tr


async def _do_l3_transition(
    state: "_LoopState",
    config: "CycleConfig",
    round_num: int,
    eval_data: list[dict],
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: "ObsLogger | None" = None,
    trace_id: str | None = None,
) -> Any:
    """Perform L3 modify_plan transition. Updates state in-place."""
    from api.services.campaign import layer_transitions
    from api.services import llm_client as _llm_client
    from api.services.obs.node_tracer import observed_step

    current_pp = state.current_sp.pipeline_params

    l2_history = [{
        "l2_round": state.l2_round,
        "optimizer_params": state.opt_sp.optimizer_params,
        "accuracy_change": state.best_composite - state.best_composite_at_l3_entry,
    }]
    emit_phase(on_phase, "modify_plan", "enter", round=round_num,
                l3_round=state.l3_round,
                l2_stall_count=state.l2_stall_count,
                current_plan_preview=str(state.opt_sp.plan)[:120])

    client = _llm_client.get_llm_client(config.provider)
    async with observed_step(f"l3_modify_plan_r{round_num}", "llm/meta",
                             obs=obs, trace_id=trace_id):
        tr = await layer_transitions.modify_plan(
            state.opt_sp, l2_history, eval_data, client,
            model=config.model,
            temperature=config.l3_temperature,
            pipeline_params=current_pp,
            pipeline_schema=config.pipeline_schema,
        )
    # Update opt_sp from L3 result, then rebuild JobSearchPoint
    state.opt_sp = tr.opt_search_point
    _pp = tr.pipeline_params or state.current_sp.pipeline_params
    state.current_sp = state.opt_sp.to_job_search_point(
        model=state.current_sp.model,
        temperature=state.current_sp.temperature,
        base_pipeline_params=_pp,
    )
    state.l3_round += 1
    state.best_accuracy_at_l3_entry = state.best_accuracy
    state.best_composite_at_l3_entry = state.best_composite
    state.l2_stall_count = 0
    state.l2_round = 0
    state.best_accuracy_at_l2_entry = state.best_accuracy
    state.best_composite_at_l2_entry = state.best_composite
    emit_phase(on_phase, "modify_plan", "exit", round=round_num,
                l3_round=state.l3_round,
                new_plan_preview=str(tr.opt_search_point.plan)[:120],
                changes_description=tr.opt_search_point.changes_description or "",
                pipeline_params_changed=tr.pipeline_params is not None)
    logger.info(
        "L3 modify_plan at round %d (l3_round=%d)",
        round_num, state.l3_round,
    )
    return tr


async def _escalate_l2(
    state: "_LoopState",
    config: "CycleConfig",
    round_num: int,
    eval_data: list[dict],
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: "ObsLogger | None" = None,
    trace_id: str | None = None,
    from_degradation: bool = False,
    escalation_context: dict | None = None,
) -> "StopReason | None":
    """Handle L1→L2 escalation and optionally L2→L3.

    Returns a StopReason if the cycle should stop, or None to continue.
    When *from_degradation* is True, L2/L3 patience exhaustion resets counters
    instead of stopping — the degradation investigation loop continues.
    """
    from api.services.campaign.models import StopReason

    _l2_kwargs = dict(obs=obs, trace_id=trace_id, escalation_context=escalation_context)

    # Track L2 stall
    l2_improved = state.best_composite > state.best_composite_at_l2_entry
    state.l2_stall_count = 0 if l2_improved or state.l2_round == 0 else state.l2_stall_count + 1

    # Not stalled → plain L2 transition
    l2_stalled = config.l2_patience is not None and state.l2_stall_count >= config.l2_patience
    if not l2_stalled:
        await _do_l2_transition(state, config, round_num, eval_data, on_phase, **_l2_kwargs)
        return None

    # L2 stalled, L3 disabled → exhaust or reset
    if not config.enable_l3:
        if from_degradation:
            logger.info(
                "L2 patience exhausted during degradation — resetting at round %d", round_num,
            )
            _degradation_reset(state, config, round_num, on_phase)
            await _do_l2_transition(state, config, round_num, eval_data, on_phase, **_l2_kwargs)
            return None
        logger.info(
            "L2 patience exhausted (%d stalls) at round %d", state.l2_stall_count, round_num,
        )
        return StopReason.L2_PATIENCE

    # L2 stalled, L3 enabled — track L3 stall
    l3_improved = state.best_composite > state.best_composite_at_l3_entry
    state.l3_stall_count = 0 if l3_improved or state.l3_round == 0 else state.l3_stall_count + 1

    l3_exhausted = config.l3_patience is not None and state.l3_stall_count >= config.l3_patience
    if not l3_exhausted:
        await _do_l3_transition(
            state, config, round_num, eval_data, on_phase, obs=obs, trace_id=trace_id,
        )
        return None

    # L3 exhausted → exhaust or reset
    if from_degradation:
        logger.info(
            "L3 patience exhausted during degradation — resetting L2/L3 at round %d", round_num,
        )
        _degradation_reset(state, config, round_num, on_phase, reset_l3=True)
        await _do_l2_transition(state, config, round_num, eval_data, on_phase, **_l2_kwargs)
        return None
    logger.info("L3 patience exhausted (%d stalls) at round %d", state.l3_stall_count, round_num)
    return StopReason.L3_PATIENCE
