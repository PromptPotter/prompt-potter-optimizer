"""L1 scoring — per-candidate three-path lifecycle + per-population loop + winner selection.

* :mod:`signal_effect` — :func:`decode_signal_effect` folds the four
  overlapping reads of an ``EscalationSignal`` into one :class:`SignalEffect`;
  :class:`CandidateOutcome` tags how a candidate exited.
* :mod:`candidate` — :func:`score_one_candidate` runs one candidate through
  the validation-skip / cache-replay / scored paths.
* :mod:`loop` — :func:`score_population` is the outer loop dispatching to
  ``score_one_candidate`` and handling LEADER_LOCKED / ESCALATED breaks.
* :mod:`winner` — :func:`l1_score` picks the round winner (comparing
  fitness, not composite_fitness) and produces the round's
  :class:`RoundResult`.
"""
