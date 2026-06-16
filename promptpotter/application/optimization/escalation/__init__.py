"""Self-healing control law — the concept HOME (escalation + wounds).

Grep "self-healing", "escalation", or "wounds" → land here. This is the
one orienting entry for how the loop heals a failure: the control law
*decides* (escalate L1→L2→L3) and *grounds the decision in evidence*
(wounds — the validation + runtime failures the next prompt must see).

HERE (this package owns the decision machinery):
- :mod:`.state`  — :class:`EscalationFSM` (cause-driven L1/L2/L3 stall
  counters), :class:`EscalationEvent`, :class:`NextAction`. Counters
  mutate only via observation methods; "signals from measurement, not the
  calendar" is structural (no setter to assign a ``round_num >= N`` literal).
- :mod:`.decide` — :func:`decide_escalation` is the single post-round
  routing entry; :class:`EscalationInputs` is the frozen snapshot it reads.
- :mod:`.rules`  — :class:`EscalationRule` rows + :data:`DEFAULT_ESCALATION_RULES`.
  Adding a rule = adding a row.
- :mod:`.firing` — :func:`escalate_l2` (L2/L3 firing driver) +
  :func:`apply_fork_payload_to_osp`.

MAPPED (the concept spans these; canonical homes, not re-exported here):
- wound + signal TYPES → ``domain/escalation_signals.py`` (``EscalationSignal``,
  ``EscalationTarget``, ``NurseOwner``, ``ValidationFailure``, ``RuntimeFailure``).
- wound RENDERING into the optimizer prompt → ``dispatch/hub/injections/
  wounds.py`` (registered by role in the ``INJECTIONS`` registry).
- the FSM LIVES ON ``optimization/cycle.py::Cycle.escalation`` (rebuilt via
  ``EscalationFSM.from_ledger`` on resume — see ``resume_and_fork/resume.py``).
- signals are CONSTRUCTED at the failure sites: ``l1/generate.py``,
  ``l1/score/signal_effect.py``, ``validators/l1_strict.py``,
  ``scoring/search_point_scorer.py``, ``pobb/elimination/checks.py``,
  ``intelligence/sibling_wounds.py``.
- the post-round ROUTING call (``decide_escalation`` → ``escalate_l2``)
  fires in ``runner/round.py``.

External callers import from this surface. The ONE exception: ``cycle.py`` and
``resume_and_fork/resume.py`` import the foundational :class:`EscalationFSM`
from ``.state`` directly — going through this surface eagerly loads the firing
driver, which depends back on ``Cycle`` (import cycle). Foundational state
types sit below the driver; everything else uses the package surface.
"""

from promptpotter.application.optimization.escalation.decide import (
    EscalationInputs,
    decide_escalation,
)
from promptpotter.application.optimization.escalation.firing import (
    apply_fork_payload_to_osp,
    escalate_l2,
)
from promptpotter.application.optimization.escalation.rules import (
    DEFAULT_ESCALATION_RULES,
    EscalationRule,
)
from promptpotter.application.optimization.escalation.state import (
    EscalationEvent,
    EscalationFSM,
    NextAction,
)

__all__ = [
    "DEFAULT_ESCALATION_RULES",
    "EscalationEvent",
    "EscalationFSM",
    "EscalationInputs",
    "EscalationRule",
    "NextAction",
    "apply_fork_payload_to_osp",
    "decide_escalation",
    "escalate_l2",
]
