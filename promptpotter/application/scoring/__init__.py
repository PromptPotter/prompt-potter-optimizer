"""Scoring — the single gateway over per-sample measurement + composite fitness.

:func:`score_search_point` (``search_point_scorer.py``) is **the** scoring
ingress (§0.5) every caller reaches for — and the only symbol re-exported
here. It resolves the prior-cache split, drives the per-sample loop, persists
each fresh measurement to the archive, and emits the ``DatasetRun`` trace.
Each load rescores under the active scorer (traces are facts, scores are policy).

CONCEPT MAP (the gateway's implementation, by module — import from each):
* ``query_loop`` — :func:`run_query_loop`, the inner per-sample walk the
  gateway drives (adaptive-queue or insertion order, cache reuse, stale-data
  recovery, abort classification) → ``QueryLoopResult``.
* ``sample_measurement`` — ``measure_sample``, one sample's backend call +
  per-step token-usage emit (the loop's leaf).
* ``evaluators`` — registry + materializers of per-node evaluators
  (compute fns returning float in [0, 1]).
* ``formula`` — ``compile_round_scorer``, ``rescore_results``,
  ``extract_item_label``, ``split_scoring_block``.
* ``metrics`` — ``compute_composite_fitness``, ``count_degraded_samples``,
  ``find_rank``, ``extract_sample_diagnostics`` (composite-fitness rollups).

Nothing is re-exported here. Every one of ``score_search_point``'s six callers
already imports it from ``search_point_scorer``, so the re-export was a second
name for the gateway that no one used — and a second name is exactly what a
single-ingress rule cannot afford.
"""
