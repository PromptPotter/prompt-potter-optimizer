"""Post-round escalation router — single entry point for "what next?".

:func:`decide_escalation` is the sort-by-priority, first-match-wins
router over :data:`DEFAULT_ESCALATION_RULES`. Synchronous, no side
effects, no LLM calls. Reads a frozen :class:`EscalationInputs`
snapshot and returns the :class:`EscalationEvent` the round loop
already understands.

The runner dispatches the result: ``FIRE_L2`` lands on
:func:`.firing.escalate_l2` (which performs the LLM call + L2/L3
cascade), ``STOP_*`` lands on :class:`StopLoop`, ``CONTINUE``
proceeds to the next L1 round.

State mutation (bumping the L1 stall counter) lives at the per-round
fold seam in :meth:`EscalationState.observe_round`; this function
takes the post-fold snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.optimization.escalation.state import (
    EscalationEvent,
    NextAction,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.escalation.rules import EscalationRule

__all__ = ["EscalationEvent", "EscalationInputs", "NextAction", "decide_escalation"]


@dataclass(frozen=True)
class EscalationInputs:
    """All escalation-rule predicate inputs, in one frozen snapshot.

    Built by :class:`EscalationState` at observation time. Predicates
    read fields directly. Optional fields (``axes_with_positive_yield``,
    etc.) are populated only when the cycle has the corresponding
    derived state available (e.g. AxisIndex initialised); rules
    consulting them must handle the ``None`` case explicitly.
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
    fire_l2_every_round: bool = False


def decide_escalation(
    inputs: EscalationInputs,
    rules: list[EscalationRule] | None = None,
) -> EscalationEvent:
    """Resolve the post-round routing decision over *inputs*.

    Sort rules by priority (higher first), return the first whose
    predicate matches. Default rule set reproduces
    :meth:`EscalationState.observe_round` exactly.
    """
    from promptpotter.application.optimization.escalation.rules import (
        DEFAULT_ESCALATION_RULES,
    )

    active = rules if rules is not None else DEFAULT_ESCALATION_RULES
    for rule in sorted(active, key=lambda r: -r.priority):
        if rule.when(inputs):
            return EscalationEvent(
                next_action=rule.fire,
                stall_depth=inputs.l1_stall_count,
                reason=rule.format_reason(inputs),
                rule_name=rule.name,
                rule_priority=rule.priority,
            )
    raise RuntimeError(
        f"No escalation rule matched observe_round inputs (rules={[r.name for r in active]}); "
        "the rule set must include a fall-through with priority < all conditional rules."
    )
