"""Scoring — the single gateway over per-sample measurement + composite fitness.

:func:`search_point_scorer.score_search_point` is **the** scoring ingress (§0.5) every
caller reaches for; traces are facts and scores are policy, so each load rescores under
the active scorer. Nothing is re-exported here, deliberately: a re-export would be a
second name for the gateway, and a second name is exactly what a single-ingress rule
cannot afford.
"""
