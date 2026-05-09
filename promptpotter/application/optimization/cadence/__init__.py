"""Cadence engine — declarative rules over EscalationState signals.

The L1→L2 escalation decision (flaw 3 in the Routed Dispatch plan) used to
live as an FSM in :meth:`EscalationState.observe_round`. Phase 2 lifts the
decision out of the state class into a typed rule evaluator: each rule is a
predicate over :class:`SignalInputs` plus a :class:`NextAction` to fire,
ranked by priority, first-match wins.

Default rules reproduce the prior FSM exactly. New rules (yield-driven L2
escalation, directive-repeat L3 escalation) layer on top — opt-in per
campaign via ``campaign.json::cadence_rules`` overrides. The L2-escalation
cascade (L2→L3) is NOT routed through cadence yet — it stays imperative on
:meth:`EscalationState.observe_l2_escalation`.
"""

from __future__ import annotations

from promptpotter.application.optimization.cadence.evaluator import (
    SignalInputs,
    evaluate_round,
)
from promptpotter.application.optimization.cadence.rules import (
    DEFAULT_ROUND_RULES,
    CadenceRule,
)

__all__ = [
    "DEFAULT_ROUND_RULES",
    "CadenceRule",
    "SignalInputs",
    "evaluate_round",
]
