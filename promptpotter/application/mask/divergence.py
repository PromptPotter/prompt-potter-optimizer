from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from pydantic import ConfigDict, Field

from promptpotter.application.mask.record import MaskCycle, MaskRecord, MaskRound
from promptpotter.domain.strict_model import StrictModel


class VerdictOutcome(StrictModel):
    """``alternative_candidate_id`` names the one-step counterfactual only where it was MEASURED, else ``None``."""

    model_config = ConfigDict(frozen=True)

    diverged: bool
    alternative_candidate_id: str | None = None


Verdict = Callable[[MaskRound], VerdictOutcome]


class Divergence(StrictModel):
    model_config = ConfigDict(frozen=True)

    cycle_id: str
    round: int
    alternative_candidate_id: str | None = None


class DivergenceResult(StrictModel):
    """``divergences`` are the markers; ``divergent`` the dimmed ``(cycle_id, round)`` coordinates STRICTLY after each."""

    model_config = ConfigDict(frozen=True)

    divergences: list[Divergence] = Field(default_factory=list)
    divergent: list[tuple[str, int]] = Field(default_factory=list)


def find_divergences(record: MaskRecord, verdict: Verdict) -> DivergenceResult:
    children: dict[str, list[MaskCycle]] = defaultdict(list)
    ids = {c.cycle_id for c in record.cycles}
    for c in record.cycles:
        if c.parent_cycle_id and c.parent_cycle_id in ids:
            children[c.parent_cycle_id].append(c)
    roots = [c for c in record.cycles if not c.parent_cycle_id or c.parent_cycle_id not in ids]

    divergences: list[Divergence] = []
    divergent: list[tuple[str, int]] = []
    for root in sorted(roots, key=lambda c: c.cycle_id):
        _walk(root, children, verdict, divergences, divergent)
    return DivergenceResult(divergences=divergences, divergent=divergent)


def _walk(
    cycle: MaskCycle,
    children: dict[str, list[MaskCycle]],
    verdict: Verdict,
    divergences: list[Divergence],
    divergent: list[tuple[str, int]],
) -> None:
    div_round: int | None = None
    for rnd in sorted(cycle.rounds, key=lambda r: r.round):
        if div_round is None:
            outcome = verdict(rnd)
            if outcome.diverged:
                div_round = rnd.round
                divergences.append(
                    Divergence(
                        cycle_id=cycle.cycle_id,
                        round=rnd.round,
                        alternative_candidate_id=outcome.alternative_candidate_id,
                    )
                )
        else:
            divergent.append((cycle.cycle_id, rnd.round))

    for child in sorted(children.get(cycle.cycle_id, []), key=lambda c: c.cycle_id):
        rooted_after = (
            div_round is not None
            and child.fork_from_round is not None
            and child.fork_from_round >= div_round
        )
        if rooted_after:
            _mark_subtree_divergent(child, children, divergent)
        else:
            # An unknown root round lands here too: it cannot be proven counterfactual.
            _walk(child, children, verdict, divergences, divergent)


def _mark_subtree_divergent(
    cycle: MaskCycle,
    children: dict[str, list[MaskCycle]],
    divergent: list[tuple[str, int]],
) -> None:
    for rnd in cycle.rounds:
        divergent.append((cycle.cycle_id, rnd.round))
    for child in children.get(cycle.cycle_id, []):
        _mark_subtree_divergent(child, children, divergent)


__all__ = ["Divergence", "Verdict", "VerdictOutcome", "find_divergences"]
