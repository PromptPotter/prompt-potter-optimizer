"""Posterior-of-Being-Best (PoBB) candidate budget allocation.

**How the round's query budget is spent across N candidates** — kill a candidate
mid-round once it can no longer be the round's best. The umbrella term; never call
this "query ranking" (that is `llm_ranking`, a backend node) and never confuse it
with the Rasch *sort* (`intelligence/hard_sample_sorter.py`, which orders samples).

Elimination and the round-winner election rank on the SAME quality metric —
difficulty-adjusted ability θ — so they can never disagree on what "better" means.

* ``elimination`` — :class:`PoBBCheck`, :class:`PoBBConfig`,
  :class:`PoBBSnapshot`, :class:`DegradationCheck`,
  :func:`classify_result`, :func:`build_degradation_checks`. The
  abort-and-continue mechanism §0 errors-heal-tolerantly depends on.
  Load-bearing per §0.5. The paired-margin futility gate is
  ``elimination/checks.py::_margin_stats``; the operator knob is
  ``campaign.json::optimization.mechanisms.elimination.margin_elimination``.
"""
