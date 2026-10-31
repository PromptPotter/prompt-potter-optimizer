from __future__ import annotations

import math
from dataclasses import dataclass

from promptpotter.application.mask.record import SpineCycle

# UCB1's regret-optimal constant for rewards in [0, 1]; NOT a knob: one rewind costs a cycle.
UCB_EXPLORATION_C: float = math.sqrt(2)

NodeKey = tuple[str, int]
"""A logical tree node: ``(cycle_id, round)`` — the cycle that FIRST produced it."""


@dataclass(frozen=True)
class NodeStats:
    """``visits`` is the size of the subtree rooted here, across every fork; ``valued`` the nodes
    in it whose θ differences."""

    cycle_id: str
    round: int
    visits: int
    valued: int
    value_sum: float

    @property
    def q(self) -> float | None:
        return self.value_sum / self.valued if self.valued else None


def _canonical_rounds(cycle: SpineCycle) -> list[int]:
    cut = cycle.cut_round
    rounds = sorted(cycle.theta_by_round)
    if cycle.parent_cycle_id is None or cut is None:
        return rounds
    return [r for r in rounds if r >= cut]


def _parent_of(cycle: SpineCycle, rnd: int, first_round: int) -> NodeKey | None:
    if rnd > first_round:
        return (cycle.cycle_id, rnd - 1)
    cut = cycle.cut_round
    if cycle.parent_cycle_id is None or cut is None or cut <= 0:
        return None  # a root, or a fork-at-offset-0 sibling: nothing above it
    return (cycle.parent_cycle_id, cut - 1)


def accumulate_node_stats(spine: list[SpineCycle]) -> dict[NodeKey, NodeStats]:
    parents: dict[NodeKey, NodeKey | None] = {}
    own: dict[NodeKey, float | None] = {}
    for cycle in spine:
        rounds = _canonical_rounds(cycle)
        if not rounds:
            continue
        first = rounds[0]
        for rnd in rounds:
            key = (cycle.cycle_id, rnd)
            own[key] = cycle.theta_by_round[rnd]
            parents[key] = _parent_of(cycle, rnd, first)

    stats = {
        k: NodeStats(k[0], k[1], 1, int(v is not None), 0.0 if v is None else v)
        for k, v in own.items()
    }
    # Depth is a lineage's length (tens), so the walk beats materializing a child adjacency map.
    for key, value in own.items():
        seen: set[NodeKey] = {key}
        cur = parents.get(key)
        while cur is not None and cur not in seen and cur in stats:
            seen.add(cur)
            prev = stats[cur]
            stats[cur] = NodeStats(
                prev.cycle_id,
                prev.round,
                prev.visits + 1,
                prev.valued + int(value is not None),
                prev.value_sum + (0.0 if value is None else value),
            )
            cur = parents.get(cur)
    return stats


def _ancestors(
    stats: dict[NodeKey, NodeStats], spine: list[SpineCycle], node: NodeKey
) -> list[NodeKey]:
    by_cycle = {c.cycle_id: c for c in spine}
    out: list[NodeKey] = []
    cur: NodeKey | None = node
    seen: set[NodeKey] = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        cycle = by_cycle.get(cur[0])
        if cycle is None:
            break
        rounds = _canonical_rounds(cycle)
        if not rounds or cur[1] not in rounds:
            break
        cur = _parent_of(cycle, cur[1], rounds[0])
        if cur is not None and cur in stats:
            out.append(cur)
    return out


def select_branch_round(
    spine: list[SpineCycle],
    *,
    cycle_id: str,
    current_round: int,
    origin_id: str,
    changing: bool,
) -> int | None:
    """The node a fork stands on, the current one or an ancestor, in the current cycle's
    coordinates; ``None`` ⇒ the caller must not fork. A fork carrying no change stands only on a
    promoted individual the line does not stand on now: anything else replays what it left."""
    stats = accumulate_node_stats(spine)
    qs = {key: q for key, s in stats.items() if (q := s.q) is not None}
    node: NodeKey = (cycle_id, current_round)
    picks = {(c.cycle_id, rnd): pick for c in spine for rnd, pick in c.pick_by_round.items()}
    if node not in picks:
        return None
    stale = {origin_id, picks[node]}
    candidates = [
        a
        for a in (node, *_ancestors(stats, spine, node))
        if a in qs and a in picks and (changing or picks[a] not in stale)
    ]
    if not candidates:
        return None

    # θ is in logits and UCB1 assumes [0, 1]; a one-θ forest collapses to pure exploration.
    lo, hi = min(qs.values()), max(qs.values())
    span = hi - lo

    def _ucb(key: NodeKey) -> float:
        child = stats[key]
        q = (qs[key] - lo) / span if span > 0 else 0.0
        parent_visits = max(stats[key].visits, 1)
        for other in _ancestors(stats, spine, key)[:1]:
            parent_visits = stats[other].visits
        return q + UCB_EXPLORATION_C * math.sqrt(math.log(max(parent_visits, 2)) / child.visits)

    best = max(candidates, key=_ucb)
    return best[1]
