"""Posterior-of-Being-Best (PoBB) candidate budget allocation.

**How the round's query budget is spent across N candidates** — kill a candidate
mid-round once it can no longer be the round's best. The umbrella term: never call this
"query ranking" (that is ``llm_ranking``, a backend node) and never confuse it with the
Rasch *sort*, which orders samples. Elimination and the round-winner election rank on the
SAME metric, difficulty-adjusted θ, so they cannot disagree about what "better" means. A
new elimination strategy implements the ``StopRule`` protocol and gains a branch in
:func:`build_elimination_check` — never a second call site. Nothing is re-exported here.
"""
