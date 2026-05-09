"""Post-round escalation router — single entry point for "what next?".

Synchronous, no side effects, no LLM calls. Reads a frozen
:class:`SignalInputs` snapshot and returns the
:class:`EscalationEvent` the round loop already understands. The
runner dispatches the result: ``FIRE_L2`` lands on
:func:`.firing.escalate_l2` (which performs the LLM call + L2/L3
cascade), ``STOP_*`` lands on :class:`StopLoop`, ``CONTINUE`` proceeds
to the next L1 round.

State mutation (bumping the L1 stall counter) lives at the per-round
fold seam in :meth:`EscalationState.observe_round`; this function
takes the post-fold snapshot. The rule engine logic lives in
:mod:`promptpotter.application.optimization.cadence.evaluator` —
that package collapses into this directory in M10 commit 2.
"""

from __future__ import annotations

from promptpotter.application.optimization.cadence.evaluator import (
    SignalInputs,
    evaluate_round,
)
from promptpotter.application.optimization.escalation.state import EscalationEvent

__all__ = ["EscalationEvent", "SignalInputs", "decide_escalation"]


def decide_escalation(inputs: SignalInputs) -> EscalationEvent:
    """Resolve the post-round routing decision over *inputs*.

    Sort-by-priority, first-match-wins over
    :data:`promptpotter.application.optimization.cadence.rules.DEFAULT_ROUND_RULES`.
    Returns the matching :class:`EscalationEvent`; ``rule_name`` /
    ``rule_priority`` are populated by the rule that matched, or
    ``None`` for the imperative L2/L3 cascade path that lives in
    :meth:`EscalationState.observe_l2_escalation`.
    """
    return evaluate_round(inputs)
