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

MAPPED (the concept spans these; canonical homes):
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

Nothing is re-exported here — every consumer imports the leaf directly, e.g.
``from promptpotter.application.optimization.escalation.decide import
decide_escalation``. That is also what keeps the package import-safe: a
re-exporting surface here would eagerly load the firing driver, which depends
back on ``Cycle``, so ``cycle.py`` and ``resume_and_fork/resume.py`` had to
reach past it to ``.state`` for the foundational :class:`EscalationFSM`. With
no surface to hop through, that exception is gone — foundational state types
simply sit below the driver.
"""
