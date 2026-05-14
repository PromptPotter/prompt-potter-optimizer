"""Round-helpers — observers, transitions, decomposition, diagnostics.

Per-round support modules called from the L1 loop and the escalation
firing path but not part of either's core surface:

* ``decomposition`` — fresh-init ``restructure`` LLM call that decomposes
  ``task_description.md`` into ``task_context``.
* ``observers`` — :class:`RunObservers` + :func:`build_run_observers` —
  typed event constructor over ``CycleEventLog.append``.
* ``transitions`` — ``TransitionResult`` + ``run_layer_transition`` —
  L2/L3 transition strategy entry.
* ``round_analysis`` — :func:`compute_round_diagnostics` — deterministic
  post-scoring stats; rendered through the ``diagnostics`` injection.
* ``l1_population`` — population-shape helpers (``parse_population``,
  ``build_score_report``, ``pobb_decision_data``, ``INVALID_SCORES``).
* ``l1_stats`` — comprehensive L1 panel stats (``L1Stats``).
* ``l1_critique`` — L1-critique LLM call orchestration.
"""
