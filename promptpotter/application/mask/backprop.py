"""MCTS backpropagation + UCB selection over the lineage forest.

The third and final phase of AlphaZero-shaped MCTS over the lineage. The other two
were already here: *expansion* is a round (L1 proposes, the population is scored),
and *simulation* is the deterministic forward pass on the eval set — the same swap
AlphaZero makes when it replaces random rollouts with a learned value. What was
missing is what this module supplies: **backpropagation** (a round's outcome updates
its ancestors' statistics) and **selection** (a rule, not a guess, picks which node to
re-expand when the current subtree is exhausted).

**The tree.** A node is one round on a cycle's spine. A fork physically copies its
parent's rounds ``0..fork_from_round-1`` forward, so those rounds exist twice on
disk under two ``cycle_id``s — the *same* logical node. :func:`_canonical_rounds`
keeps only a cycle's own new rounds (``round >= fork_from_round``) and re-attaches
its spine to the parent's branch-point, so each logical node is counted exactly once.
Counting the copies would inflate every ancestor's visit count by its own descendants'
inherited prefixes — the deeper the lineage, the more the bias compounds.

**The value.** ``cumulative_theta`` (Rasch ability). Averaging accuracy up a lineage is
not sound: rounds score different sample subsets, so accuracy is subset-relative and a
deep branch that drifted onto easier samples would out-rank a shallow honest one. θ is
subset-invariant by construction — it is why the frontier is persisted at all.

**The rule.** UCB1 over an ancestor's children. θ is unbounded (logits), while UCB1's
exploration term assumes rewards in [0, 1] — so Q is min-max normalized across the
forest before the bonus is added, which is what keeps ``UCB_EXPLORATION_C`` meaningful
rather than arbitrary. Rollouts here cost a full round of LLM calls, so exploration is
deliberately cheap-side: this is closer to AlphaZero's sample-efficient PUCT in spirit
than to vanilla UCT over free rollouts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from promptpotter.application.mask.record import MaskCycle, MaskRecord, MaskRound
from promptpotter.config.settings import UCB_EXPLORATION_C

NodeKey = tuple[str, int]
"""A logical tree node: ``(cycle_id, round)`` — the cycle that FIRST produced it."""


@dataclass(frozen=True)
class NodeStats:
    """One lineage node's backpropagated statistics — MCTS's N, W, Q.

    ``visits`` is the size of the subtree rooted here (this round plus every round
    descended from it, across every fork). ``value_sum`` is that subtree's summed θ.
    A node with a high ``q`` but few ``visits`` is exactly what the exploration term
    exists to surface: promising, and under-explored.
    """

    cycle_id: str
    round: int
    visits: int
    value_sum: float

    @property
    def node_key(self) -> str:
        """Matches ``MaskRound.node_key`` + the frontend tree node id."""
        return f"{self.cycle_id}::r{self.round}"

    @property
    def q(self) -> float:
        """Mean subtree value. ``visits`` is >= 1 for every node the fold emits."""
        return self.value_sum / self.visits


def _canonical_rounds(cycle: MaskCycle) -> list[MaskRound]:
    """A cycle's OWN rounds — the inherited prefix belongs to the parent, not here."""
    cut = cycle.fork_from_round
    if cycle.parent_cycle_id is None or cut is None:
        return list(cycle.rounds)
    return [r for r in cycle.rounds if r.round >= cut]


def _parent_of(cycle: MaskCycle, rnd: MaskRound, first_round: int) -> NodeKey | None:
    """The tree edge above *rnd*: the prior round on this spine, else the branch-point."""
    if rnd.round > first_round:
        return (cycle.cycle_id, rnd.round - 1)
    cut = cycle.fork_from_round
    if cycle.parent_cycle_id is None or cut is None or cut <= 0:
        return None  # a root, or a fork-at-offset-0 sibling: nothing above it
    return (cycle.parent_cycle_id, cut - 1)


def accumulate_node_stats(record: MaskRecord) -> dict[NodeKey, NodeStats]:
    """Backpropagate every round's θ up to its ancestors — the fold.

    Each round is one simulation. A node's ``visits`` / ``value_sum`` accumulate its
    own outcome plus every outcome below it, so an ancestor's statistics answer "what
    did re-expanding from here actually yield, everywhere it was tried?" — the question
    a rewind is asking, and the one no per-cycle counter can answer.
    """
    parents: dict[NodeKey, NodeKey | None] = {}
    own: dict[NodeKey, float] = {}
    for cycle in record.cycles:
        rounds = _canonical_rounds(cycle)
        if not rounds:
            continue
        first = min(r.round for r in rounds)
        for rnd in rounds:
            key = (cycle.cycle_id, rnd.round)
            own[key] = rnd.cumulative_theta
            parents[key] = _parent_of(cycle, rnd, first)

    stats = {k: NodeStats(k[0], k[1], visits=1, value_sum=v) for k, v in own.items()}
    # Walk each node's ancestor chain once. Depth is a lineage's length (tens), so the
    # O(nodes x depth) walk is cheaper than materializing a child adjacency map.
    for key, value in own.items():
        seen: set[NodeKey] = {key}
        cur = parents.get(key)
        while cur is not None and cur not in seen and cur in stats:
            seen.add(cur)
            prev = stats[cur]
            stats[cur] = NodeStats(
                prev.cycle_id, prev.round, prev.visits + 1, prev.value_sum + value
            )
            cur = parents.get(cur)
    return stats


def _ancestors(stats: dict[NodeKey, NodeStats], record: MaskRecord, node: NodeKey) -> list[NodeKey]:
    """*node*'s strict ancestors, nearest first — the rewind targets on offer."""
    by_cycle = {c.cycle_id: c for c in record.cycles}
    out: list[NodeKey] = []
    cur: NodeKey | None = node
    seen: set[NodeKey] = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        cycle = by_cycle.get(cur[0])
        if cycle is None:
            break
        rounds = _canonical_rounds(cycle)
        if not rounds:
            break
        rnd = next((r for r in rounds if r.round == cur[1]), None)
        if rnd is None:
            break
        cur = _parent_of(cycle, rnd, min(r.round for r in rounds))
        if cur is not None and cur in stats:
            out.append(cur)
    return out


def select_rewind_round(
    record: MaskRecord,
    *,
    cycle_id: str,
    current_round: int,
) -> int | None:
    """The UCB1 pick: which ancestor round to re-expand from. ``None`` ⇒ no rewind.

    Returns an ABSOLUTE round number valid in the current cycle's coordinates. That is
    sound without a cross-cycle address because a fork carries its parent's rounds
    forward under their original numbers — so an ancestor living in a parent cycle is
    addressable as that same round here, and ``RebaseRequest`` needs no cycle field.

    ``None`` when there is nothing above the current node (a root's round 0) — the
    caller must not fork, since a rewind to nowhere would mint a duplicate cycle.
    """
    stats = accumulate_node_stats(record)
    node: NodeKey = (cycle_id, current_round)
    candidates = [a for a in _ancestors(stats, record, node) if a[1] < current_round]
    if not candidates:
        return None

    # θ is in logits; UCB1's bonus assumes [0, 1]. Normalize across the forest so the
    # exploration constant means what it says. A degenerate forest (every node the same
    # θ) collapses to pure exploration — correct: nothing distinguishes the arms.
    values = [s.q for s in stats.values()]
    lo, hi = min(values), max(values)
    span = hi - lo

    def _ucb(key: NodeKey) -> float:
        child = stats[key]
        q = (child.q - lo) / span if span > 0 else 0.0
        parent_visits = max(stats[key].visits, 1)
        for other in _ancestors(stats, record, key)[:1]:
            parent_visits = stats[other].visits
        return q + UCB_EXPLORATION_C * math.sqrt(math.log(max(parent_visits, 2)) / child.visits)

    best = max(candidates, key=_ucb)
    return best[1]


__all__ = ["NodeStats", "accumulate_node_stats", "select_rewind_round"]
