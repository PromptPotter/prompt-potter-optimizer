"""Intelligence — shared materialized views over historical search data.

Derived views over the ``MeasurementArchive`` (the DB core), consumed by
both the optimization loop and the optional sensitivity scan. Pure
derivation off measurements. **Layer rule: MUST NOT import from
``optimization``** (fails loud at import).

CONCEPT MAP (by module):
* **indexes** (:mod:`.indexes` package) — the two incremental-refresh
  views sharing a ``_seen_runs`` cursor: :class:`SampleIndex` (per-sample
  state, hits/flips/failure modes) and :class:`AxisIndex` (axis-keyed
  ``digest()`` of param impact / patterns). Both are re-exported here.
* **adaptive_queue_mechanism** — 1PL Rasch CAT primitives;
  ``pick_value = decision_information_gain + delta_learning_gain``.
* **exploration** — Rasch IRT fit + per-round scoring-subset selection
  (``build_observations`` / ``select_round_subset`` / ``Observation``).
* **measurement_series** — per-sample chronological series (cycle + campaign).
* **hard_sample_sorter** / **hard_sample_archive** — θ_c × δ_s hard-sample
  matrix (in-cycle) + its cross-cycle archive-sourced peer.
* **sibling_wounds** — surfaces a sibling fork's ``RuntimeFailure``s onto a
  fresh fork (cross-cycle wound inheritance).
"""

from promptpotter.application.intelligence.indexes import AxisIndex, SampleIndex

__all__ = ["AxisIndex", "SampleIndex"]
