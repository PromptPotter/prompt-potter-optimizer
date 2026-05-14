"""Posterior-of-Being-Best (PoBB) candidate budget allocation.

* ``elimination`` — :class:`PoBBCheck`, :class:`PoBBConfig`,
  :class:`PoBBSnapshot`, :class:`DegradationCheck`,
  :func:`classify_result`, :func:`build_degradation_checks`. The
  abort-and-continue mechanism §0 errors-heal-tolerantly depends on.
  Load-bearing per §0.5.
* ``elevation`` — :func:`elevate_to_decisive` + :func:`discover_compare_arms`.
  Adaptive top-up for the ``compare`` CLI verb (cycle-winner
  arbitration across the family).
"""
