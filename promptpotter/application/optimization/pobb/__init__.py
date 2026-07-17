"""Posterior-of-Being-Best (PoBB) candidate budget allocation.

**How the round's query budget is spent across N candidates** — kill a candidate
mid-round once it can no longer be the round's best. The umbrella term; never call
this "query ranking" (that is `llm_ranking`, a backend node) and never confuse it
with the Rasch *sort* (`intelligence/hard_sample_sorter.py`, which orders samples).

Elimination and the round-winner election rank on the SAME quality metric —
difficulty-adjusted ability θ — so they can never disagree on what "better" means.

Two leaves, and nothing is re-exported here — import each directly:

* :mod:`.checks` — the mid-round stop rules: :class:`DegradationCheck` (fatal
  fast-path + rate-based) and :class:`PoBBCheck` (Bayesian Posterior-of-Being-Best,
  Russo 2016) + :class:`PoBBConfig` / :class:`PoBBSnapshot` /
  :func:`build_degradation_checks`. The abort-and-continue mechanism §0
  errors-heal-tolerantly depends on — load-bearing per §0.5. The paired-margin
  futility gate is ``checks.py::_margin_stats``; the operator knob is
  ``campaign.json::optimization.mechanisms.elimination.margin_elimination``.
* :mod:`.classification` — :func:`classify_result`'s three-bucket
  (advisory / infra / fatal) verdict + the result-shape helpers
  (:func:`is_deprecated`, :func:`get_ranked_items`, :func:`extract_warning_types`,
  :func:`ranked_item_keys_from_schema`). Read well beyond elimination — scoring and
  metrics classify results too — which is why it stays its own leaf rather than
  folding into ``checks``.

**Adding an elimination strategy.** The strategy contract is the
:class:`~promptpotter.domain.validators.StopRule` Protocol; the swap point is
:func:`build_elimination_check` (today returns PoBBCheck, the sole strategy). A new
strategy gains a branch in that builder.
"""
