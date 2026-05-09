"""Scoring — single gateway over per-sample measurement and composite fitness.

Per CLAUDE.md, :func:`score_search_point` is **the** scoring gateway every
caller reaches for; ``measure_sample`` is its implementation detail. Each
load rescores under the active scorer (traces are facts, scores are policy).

The gateway is re-exported here. Specialised utilities live in their own
modules and are imported from there:

* ``formula`` — ``compile_round_scorer``, ``rescore_results``,
  ``extract_item_label``, ``extract_display_answer``,
  ``split_scoring_block``.
* ``metrics`` — ``compute_composite_fitness``, ``count_degraded_samples``,
  ``find_rank``, ``extract_sample_diagnostics``.
* ``sample_measurement`` — ``measure_sample`` (used by the gateway).
* ``evaluators`` — registry of per-node evaluators.
"""

from promptpotter.application.scoring.search_point_scorer import score_search_point

__all__ = ["score_search_point"]
