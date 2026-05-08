"""Escalation FSM + L2/L3 firing driver.

- :mod:`.state` — :class:`EscalationState` (cause-driven L1/L2/L3 stall
  counters), :class:`EscalationEvent`, :class:`NextAction`,
  :func:`build_escalation_entry`. Counters mutate only via observation
  methods; ``signals from measurement, not the calendar`` is structural
  (no public setter to assign a ``round_num >= N`` literal to).
- :mod:`.firing` — :func:`escalate_l2` (the L2/L3 firing driver) +
  :class:`LayerStrategy` + :func:`apply_sweep_payload_to_osp`.
"""

from promptpotter.application.optimization.escalation.firing import (
    apply_sweep_payload_to_osp,
    escalate_l2,
)
from promptpotter.application.optimization.escalation.state import (
    EscalationEvent,
    EscalationState,
    NextAction,
    build_escalation_entry,
)

__all__ = [
    "EscalationEvent",
    "EscalationState",
    "NextAction",
    "apply_sweep_payload_to_osp",
    "build_escalation_entry",
    "escalate_l2",
]
