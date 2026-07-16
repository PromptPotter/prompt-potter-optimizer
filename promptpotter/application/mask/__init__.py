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
