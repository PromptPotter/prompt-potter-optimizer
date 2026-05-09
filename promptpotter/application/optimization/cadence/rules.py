"""Default cadence rule set — reproduces :meth:`EscalationState.observe_round`.

Each rule is a frozen :class:`CadenceRule`: predicate over
:class:`SignalInputs`, action to fire, integer priority, and a reason
template the evaluator formats into the returned
:class:`EscalationEvent.reason`. Higher priority wins; ties resolved by
list order.

Phase 2a ships the existing FSM as four rules — no behaviour change.
Phase 2b layers a yield-driven rule (axis_yield_drought) opt-in via
``campaign.json::optimization.escalate_on_yield_drought``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from promptpotter.application.optimization.cadence.evaluator import SignalInputs
from promptpotter.application.optimization.escalation.state import NextAction

PredicateFn = Callable[[SignalInputs], bool]
ReasonFn = Callable[[SignalInputs], str]


@dataclass(frozen=True)
class CadenceRule:
    """One declarative escalation rule.

    Rules are evaluated in priority order (higher first); first matching
    rule wins. The fall-through pattern is a low-priority rule with
    ``when=lambda s: True``.
    """

    name: str
    when: PredicateFn
    fire: NextAction
    priority: int = 0
    reason: ReasonFn | str = ""

    def format_reason(self, inputs: SignalInputs) -> str:
        if callable(self.reason):
            return self.reason(inputs)
        return self.reason or self.name


# Default rules reproduce :meth:`EscalationState.observe_round` exactly,
# plus the opt-in yield-drought rule (Phase 2b).
# Branch order in the original FSM (perfect → continue → stop_no_l2 → fire)
# maps to descending priority below — first match wins. The yield-drought
# rule sits at priority 60, ABOVE l1_continue (50): when AxisIndex shows no
# axes with positive yield AND L1 has stalled, escalate to L2 early instead
# of grinding out the patience window.
DEFAULT_ROUND_RULES: list[CadenceRule] = [
    CadenceRule(
        name="perfect_accuracy",
        when=lambda s: s.current_accuracy >= 1.0,
        fire=NextAction.STOP_PERFECT,
        priority=100,
        reason="composite_fitness >= 1.0",
    ),
    CadenceRule(
        name="l2_axis_yield_drought",
        when=lambda s: (
            s.escalate_on_yield_drought
            and s.l1_stall_count >= 1
            and s.axes_with_positive_yield is not None
            and s.axes_with_positive_yield == 0
        ),
        fire=NextAction.FIRE_L2,
        priority=60,
        reason=lambda s: (
            f"axis yield drought: 0 axes with effect > noise at L1 stall "
            f"{s.l1_stall_count}/{s.l1_patience}"
        ),
    ),
    CadenceRule(
        name="l1_continue",
        when=lambda s: s.l1_stall_count < s.l1_patience,
        fire=NextAction.CONTINUE,
        priority=50,
        reason=lambda s: f"L1 stall {s.l1_stall_count}/{s.l1_patience}",
    ),
    CadenceRule(
        name="l1_stop_no_l2",
        when=lambda s: not s.enable_l2,
        fire=NextAction.STOP_L1_PATIENCE,
        priority=30,
        reason="L1 patience exhausted; L2 disabled",
    ),
    CadenceRule(
        name="l1_to_l2",
        when=lambda s: True,
        fire=NextAction.FIRE_L2,
        priority=10,
        reason="L1 patience exhausted -> L2",
    ),
]


__all__ = ["DEFAULT_ROUND_RULES", "CadenceRule", "PredicateFn", "ReasonFn"]
