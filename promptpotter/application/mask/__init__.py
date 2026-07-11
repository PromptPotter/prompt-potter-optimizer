"""mask — the realized lineage as a tree, and the folds that read it (backend, read-only).

Two folds over one **record**, both tree-recursive, neither building a second tree:

* ``find_divergences`` — projects an alternative *criterion* over the realized lineage
  and marks where it would have forked the record. "mask" is internal naming; the
  operator sees only a served divergence overlay.
* ``accumulate_node_stats`` / ``select_rewind_round`` — MCTS **backpropagation** (roll
  each round's Rasch ability up to its ancestors) and **UCB selection** (which ancestor
  a stalled L2/L3 should re-expand from). The lineage forest is the search tree; this is
  the phase that makes it one.

Design + invariants: ``docs/specs/mask-projection.md``.
"""

from __future__ import annotations

from promptpotter.application.mask.backprop import (
    NodeStats,
    accumulate_node_stats,
    select_rewind_round,
)
from promptpotter.application.mask.divergence import (
    Divergence,
    DivergenceResult,
    Verdict,
    VerdictOutcome,
    find_divergences,
)
from promptpotter.application.mask.record import (
    MaskCandidate,
    MaskCycle,
    MaskRecord,
    MaskRound,
)
from promptpotter.application.mask.verdicts import make_abort_verdict, make_scoring_verdict

__all__ = [
    "Divergence",
    "DivergenceResult",
    "MaskCandidate",
    "MaskCycle",
    "MaskRecord",
    "MaskRound",
    "NodeStats",
    "Verdict",
    "VerdictOutcome",
    "accumulate_node_stats",
    "find_divergences",
    "make_abort_verdict",
    "make_scoring_verdict",
    "select_rewind_round",
]
