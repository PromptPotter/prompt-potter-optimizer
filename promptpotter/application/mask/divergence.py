"""``find_divergences`` — the one shared tree fold: the FIRST node per branch where the verdict flips, its subtree
marked counterfactual. It builds no second tree, because past the divergence nothing was ever measured."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from pydantic import ConfigDict, Field

from promptpotter.application.mask.record import MaskCycle, MaskRecord, MaskRound
from promptpotter.domain.strict_model import StrictModel


class VerdictOutcome(StrictModel):
    """A verdict's answer for one round. ``alternative_candidate_id`` names the one-step counterfactual only when it was
    MEASURED and so is nameable; ``None`` when the round would not have diverged, or held on origin."""

    model_config = ConfigDict(frozen=True)

    diverged: bool
    alternative_candidate_id: str | None = None


# A verdict is a strategy callable bound (via a factory) with whatever its math
# needs — a pipeline schema + the swapped criterion for the scoring verdict, a
# variant config for the abort verdict. The fold knows none of that.
Verdict = Callable[[MaskRound], VerdictOutcome]


class Divergence(StrictModel):
    """The first round on a branch the criterion would have forked. Rendered as a MARKER on that node, not dimmed; its
    descendant subtree is what gets dimmed."""

    model_config = ConfigDict(frozen=True)

    node_key: str
    cycle_id: str
    round: int
    alternative_candidate_id: str | None = None


class DivergenceResult(StrictModel):
    """The fold's output. ``divergences`` are the markers; ``divergent`` are the dimmed counterfactual keys STRICTLY after
    each one — the divergence node itself stays a real, marked node."""

    model_config = ConfigDict(frozen=True)

    divergences: list[Divergence] = Field(default_factory=list)
    divergent: list[str] = Field(default_factory=list)


def find_divergences(record: MaskRecord, verdict: Verdict) -> DivergenceResult:
    """Per-branch and tree-recursive. A fork rooted BEFORE the divergence stays in the invariant prefix and is analyzed for
    its own; a fork rooted at or after it is wholly counterfactual."""
    children: dict[str, list[MaskCycle]] = defaultdict(list)
    ids = {c.cycle_id for c in record.cycles}
    for c in record.cycles:
        if c.parent_cycle_id and c.parent_cycle_id in ids:
            children[c.parent_cycle_id].append(c)
    roots = [c for c in record.cycles if not c.parent_cycle_id or c.parent_cycle_id not in ids]

    divergences: list[Divergence] = []
    divergent: list[str] = []
    for root in sorted(roots, key=lambda c: c.cycle_id):
        _walk(root, children, verdict, divergences, divergent)
    return DivergenceResult(divergences=divergences, divergent=divergent)


def _walk(
    cycle: MaskCycle,
    children: dict[str, list[MaskCycle]],
    verdict: Verdict,
    divergences: list[Divergence],
    divergent: list[str],
) -> None:
    div_round: int | None = None
    for rnd in sorted(cycle.rounds, key=lambda r: r.round):
        if div_round is None:
            outcome = verdict(rnd)
            if outcome.diverged:
                div_round = rnd.round
                divergences.append(
                    Divergence(
                        node_key=rnd.node_key,
                        cycle_id=cycle.cycle_id,
                        round=rnd.round,
                        alternative_candidate_id=outcome.alternative_candidate_id,
                    )
                )
        else:
            # Strictly after the divergence point — counterfactual, dimmed.
            divergent.append(rnd.node_key)

    for child in sorted(children.get(cycle.cycle_id, []), key=lambda c: c.cycle_id):
        rooted_after = (
            div_round is not None
            and child.fork_from_round is not None
            and child.fork_from_round >= div_round
        )
        if rooted_after:
            _mark_subtree_divergent(child, children, divergent)
        else:
            # Rooted before the divergence (or no divergence here, or unknown
            # root round → honest: can't prove counterfactual) → analyze it.
            _walk(child, children, verdict, divergences, divergent)


def _mark_subtree_divergent(
    cycle: MaskCycle,
    children: dict[str, list[MaskCycle]],
    divergent: list[str],
) -> None:
    for rnd in cycle.rounds:
        divergent.append(rnd.node_key)
    for child in children.get(cycle.cycle_id, []):
        _mark_subtree_divergent(child, children, divergent)


__all__ = ["Divergence", "Verdict", "VerdictOutcome", "find_divergences"]
