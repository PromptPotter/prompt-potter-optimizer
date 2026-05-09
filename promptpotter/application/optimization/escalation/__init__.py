"""Escalation FSM + L2/L3 firing driver + post-round routing.

- :mod:`.state` — :class:`EscalationState` (cause-driven L1/L2/L3 stall
  counters), :class:`EscalationEvent`, :class:`NextAction`. Counters
  mutate only via observation methods; ``signals from measurement, not
  the calendar`` is structural (no public setter to assign a
  ``round_num >= N`` literal to).
- :mod:`.decide` — :func:`decide_escalation` is the single
  post-round routing entry point;
  :class:`EscalationInputs` is the frozen snapshot it reads.
- :mod:`.rules` — :class:`EscalationRule` rows + the
  :data:`DEFAULT_ESCALATION_RULES` list. Adding a rule means adding a
  row.
- :mod:`.firing` — :func:`escalate_l2` (the L2/L3 firing driver) +
  :class:`LayerStrategy` + :func:`apply_sweep_payload_to_osp`.
"""

from promptpotter.application.optimization.escalation.decide import (
    EscalationInputs,
    decide_escalation,
)
from promptpotter.application.optimization.escalation.firing import (
    apply_sweep_payload_to_osp,
    escalate_l2,
)
from promptpotter.application.optimization.escalation.rules import (
    DEFAULT_ESCALATION_RULES,
    EscalationRule,
)
from promptpotter.application.optimization.escalation.state import (
    EscalationEvent,
    EscalationState,
    NextAction,
)

__all__ = [
    "DEFAULT_ESCALATION_RULES",
    "EscalationEvent",
    "EscalationInputs",
    "EscalationRule",
    "EscalationState",
    "NextAction",
    "apply_sweep_payload_to_osp",
    "decide_escalation",
    "escalate_l2",
]
