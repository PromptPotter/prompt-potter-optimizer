"""Default escalation rule set — reproduces :meth:`EscalationState.observe_round`.

Each rule is a frozen :class:`EscalationRule`: predicate over
:class:`EscalationInputs`, action to fire, integer priority, and a
reason template the evaluator formats into the returned
:class:`EscalationEvent.reason`. Higher priority wins; ties resolved by
list order.

The yield-driven rule (``l2_axis_yield_drought``) is a permanent rule —
it fires whenever the cycle has AxisIndex evidence and that evidence
shows zero productive axes. When the signal is unavailable (early
cycles, no AxisIndex) the rule's predicate is False and other rules
take over.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from promptpotter.application.optimization.escalation.decide import EscalationInputs
from promptpotter.application.optimization.escalation.state import NextAction

PredicateFn = Callable[[EscalationInputs], bool]
ReasonFn = Callable[[EscalationInputs], str]


@dataclass(frozen=True)
class EscalationRule:
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

    def format_reason(self, inputs: EscalationInputs) -> str:
        if callable(self.reason):
            return self.reason(inputs)
        return self.reason or self.name


# perfect_accuracy preempts so a perfect-fit round terminates instead of
# firing one more L2. l2_axis_yield_drought (priority 60) preempts the
# patience wait when the AxisIndex shows no productive axes. Setting
# l1_patience=0 unifies the "fire L2 every round" cadence under this
# primitive: l1_continue never matches, so we fall through to l1_to_l2.
DEFAULT_ESCALATION_RULES: list[EscalationRule] = [
    EscalationRule(
        name="perfect_accuracy",
        when=lambda s: s.current_accuracy >= 1.0,
        fire=NextAction.STOP_PERFECT,
        priority=100,
        reason="composite_fitness >= 1.0",
    ),
    EscalationRule(
        name="l2_axis_yield_drought",
        when=lambda s: (
            s.l1_stall_count >= 1
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
    EscalationRule(
        name="l1_continue",
        when=lambda s: s.l1_stall_count < s.l1_patience,
        fire=NextAction.CONTINUE,
        priority=50,
        reason=lambda s: f"L1 stall {s.l1_stall_count}/{s.l1_patience}",
    ),
    EscalationRule(
        name="l1_to_l2",
        when=lambda s: True,
        fire=NextAction.FIRE_L2,
        priority=10,
        reason="L1 patience exhausted -> L2",
    ),
]


__all__ = [
    "DEFAULT_ESCALATION_RULES",
    "EscalationRule",
    "PredicateFn",
    "ReasonFn",
]
