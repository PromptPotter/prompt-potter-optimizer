"""Rule evaluator — sort-by-priority, first-match-wins.

:class:`SignalInputs` is the frozen snapshot of every field a cadence
predicate may inspect. Adding a new signal means adding a field here +
adding a rule that consults it. Evaluators take a list of
:class:`CadenceRule` and a ``SignalInputs`` and return the
:class:`EscalationEvent` the cycle loop already understands — same wire
shape as the prior FSM so call sites don't change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.optimization.escalation.state import (
    EscalationEvent,
    NextAction,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.cadence.rules import CadenceRule


@dataclass(frozen=True)
class SignalInputs:
    """All cadence-rule predicate inputs, in one frozen snapshot.

    Built by :class:`EscalationState` at observation time. Predicates read
    fields directly. Optional fields (``axes_with_positive_yield``, etc.)
    are populated only when the cycle has the corresponding derived state
    available (e.g. AxisIndex initialised); rules consulting them must
    handle the ``None`` case explicitly.
    """

    # Round outcome
    improved: bool
    current_accuracy: float

    # Stall counters (mirror EscalationState properties post-mutation)
    l1_stall_count: int

    # Patience config + enabled gates
    l1_patience: int
    enable_l2: bool

    # Optional derived-memory inputs (None when AxisIndex not initialised
    # or the corresponding feature flag is off — predicates must handle).
    axes_with_positive_yield: int | None = None
    escalate_on_yield_drought: bool = False


def evaluate_round(
    inputs: SignalInputs,
    rules: list[CadenceRule] | None = None,
) -> EscalationEvent:
    """Post-L1-round transition: returns CONTINUE / FIRE_L2 / STOP_*.

    Sort rules by priority (higher first), return the first whose
    predicate matches. Default rule set reproduces
    :meth:`EscalationState.observe_round` exactly.
    """
    from promptpotter.application.optimization.cadence.rules import DEFAULT_ROUND_RULES

    active = rules if rules is not None else DEFAULT_ROUND_RULES
    for rule in sorted(active, key=lambda r: -r.priority):
        if rule.when(inputs):
            return EscalationEvent(
                next_action=rule.fire,
                stall_depth=inputs.l1_stall_count,
                reason=rule.format_reason(inputs),
            )
    raise RuntimeError(
        f"No cadence rule matched observe_round inputs (rules={[r.name for r in active]}); "
        "the rule set must include a fall-through with priority < all conditional rules."
    )


# Re-export for ergonomics — callers import NextAction from cadence as well.
__all__ = ["NextAction", "SignalInputs", "evaluate_round"]
