"""``find_divergences`` — the one shared tree-recursive fold.

The single shared piece of mask machinery. Given the realized :class:`MaskRecord`
and a **verdict** (a strategy callable that, per round, says "would an alternative
criterion have gone differently here?"), it walks the lineage forest and finds the
*first* node per branch where the verdict flips — the **divergence point** — and
marks that node's descendant subtree **divergent** (counterfactual). It does **not**
build a second, full alternative tree: past the first divergence the data was never
measured, so any deeper tail would be fiction. It finds where the record *departs*.

What varies between masks is **only the verdict** (scoring, abort, …) — never this
fold. The verdict is a per-round predicate; this is proven for the two per-round
consumers (scoring + abort). The fold is indifferent to what the verdict reads.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.application.mask.record import MaskCycle, MaskRecord, MaskRound


class VerdictOutcome(BaseModel):
    """A verdict's answer for one round. ``alternative_candidate_id`` names the
    one-step counterfactual when the masked criterion would have elected a
    *different* candidate (it was measured, so it is invariant — nameable); ``None``
    when the round simply would not have diverged, or would have held on origin, or
    when the verdict measures no nameable one-step alternative (the abort verdict)."""

    model_config = ConfigDict(frozen=True)

    diverged: bool
    alternative_candidate_id: str | None = None


# A verdict is a strategy callable bound (via a factory) with whatever its math
# needs — a pipeline schema + the swapped criterion for the scoring verdict, a
# variant config for the abort verdict. The fold knows none of that.
Verdict = Callable[[MaskRound], VerdictOutcome]


class Divergence(BaseModel):
    """A divergence point — the first round on a branch the criterion would have
    forked. Rendered as a marker on that node (not dimmed); its descendant subtree
    is dimmed (the ``divergent`` set)."""

    model_config = ConfigDict(frozen=True)

    node_key: str
    cycle_id: str
    round: int
    alternative_candidate_id: str | None = None


class DivergenceResult(BaseModel):
    """The fold's output. ``divergences`` are the markers; ``divergent`` are the
    node keys of the dimmed counterfactual subtree (strictly *after* each
    divergence — the divergence node itself stays a real, marked node)."""

    model_config = ConfigDict(frozen=True)

    divergences: list[Divergence] = Field(default_factory=list)
    divergent: list[str] = Field(default_factory=list)


def find_divergences(record: MaskRecord, verdict: Verdict) -> DivergenceResult:
    """Walk the forest; return the divergence markers + the divergent subtree.

    Per-branch and tree-recursive: within a cycle the *first* diverging round is the
    divergence point and every later round on that spine is divergent; a fork rooted
    *before* the divergence stays in the invariant prefix and is analyzed for its
    own divergence, while a fork rooted *at or after* it is wholly counterfactual.
    """
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


__all__ = ["Divergence", "DivergenceResult", "Verdict", "VerdictOutcome", "find_divergences"]
