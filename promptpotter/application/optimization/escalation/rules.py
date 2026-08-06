"""Default escalation rules — (predicate, action, priority); higher wins, ties by list order. A predicate is False
when its signal is unavailable, so early cycles fall through to the other rules rather than firing blind."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from promptpotter.application.optimization.escalation.decide import EscalationInputs
from promptpotter.application.optimization.escalation.state import NextAction

PredicateFn = Callable[[EscalationInputs], bool]


@dataclass(frozen=True)
class EscalationRule:
    """Declarative rule; evaluated highest-priority first, first match wins.
    Fall-through = low-priority + ``when=lambda s: True``."""

    name: str
    when: PredicateFn
    fire: NextAction
    priority: int = 0


# perfect_accuracy preempts so a perfect-fit round terminates instead of firing L2;
# l1_generate_unusable preempts patience — l1_generate's output is structurally unusable this
#   round (a dropped mandatory placeholder OR zero parseable candidates), a fault no amount of
#   patience fixes because the identical optimizer prompt reproduces it; heal L2 now;
# l1_evidence_starved preempts patience — a node starved across ~all samples is accumulated
#   evidence of a systemic fault no L1 param move can fix; bring L2 in to diagnose (it never stops);
# l2_axis_yield_drought preempts patience when AxisIndex shows no productive axes;
# l1_patience=0 collapses "fire L2 every round" via the l1_to_l2 fall-through.
DEFAULT_ESCALATION_RULES: list[EscalationRule] = [
    EscalationRule(
        name="perfect_accuracy",
        when=lambda s: s.current_accuracy >= 1.0,
        fire=NextAction.STOP_PERFECT,
        priority=100,
    ),
    EscalationRule(
        name="l1_generate_unusable",
        when=lambda s: s.l1_mandatory_breach or s.l1_zero_candidates,
        fire=NextAction.FIRE_L2,
        priority=70,
    ),
    EscalationRule(
        name="l1_evidence_starved",
        when=lambda s: s.evidence_starved,
        fire=NextAction.FIRE_L2,
        priority=65,
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
    ),
    EscalationRule(
        name="l1_continue",
        when=lambda s: s.l1_stall_count < s.l1_patience,
        fire=NextAction.CONTINUE,
        priority=50,
    ),
    EscalationRule(
        name="l1_to_l2",
        when=lambda s: True,
        fire=NextAction.FIRE_L2,
        priority=10,
    ),
]


__all__ = [
    "DEFAULT_ESCALATION_RULES",
    "EscalationRule",
]
