"""Post-round escalation router. `decide_escalation` is priority-sort, first-match over
`DEFAULT_ESCALATION_RULES`. Pure: no LLM, no side effects. Returns the EscalationEvent the
round loop dispatches (`FIRE_L2` → `.firing.escalate_l2`, `STOP_*` → StopLoop, `CONTINUE` → next round).
State mutation (stall counter bump) lives in `EscalationFSM.observe_round`; this is post-fold.
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
    """Frozen snapshot of all escalation-rule predicate inputs. Optional fields are populated only
    when the corresponding derived state exists (e.g. AxisIndex initialised); rules must handle None.
    """

    current_accuracy: float
    l1_stall_count: int
    l1_patience: int
    # None until AxisIndex is initialised; runner populates from `cycle.axes.with_positive_yield()`.
    axes_with_positive_yield: int | None = None


def decide_escalation(
    inputs: EscalationInputs,
    rules: list[EscalationRule] | None = None,
) -> EscalationEvent:
    """Sort by priority (desc), return the first matching rule's event."""
    from promptpotter.application.optimization.escalation.rules import (
        DEFAULT_ESCALATION_RULES,
    )

    active = rules if rules is not None else DEFAULT_ESCALATION_RULES
    for rule in sorted(active, key=lambda r: -r.priority):
        if rule.when(inputs):
            return EscalationEvent(
                next_action=rule.fire,
                reason=rule.format_reason(inputs),
            )
    raise RuntimeError(
        f"No escalation rule matched observe_round inputs (rules={[r.name for r in active]}); "
        "the rule set must include a fall-through with priority < all conditional rules."
    )
