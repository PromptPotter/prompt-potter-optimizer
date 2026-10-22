from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

from pydantic import Field

from promptpotter.application.mask.record import MaskCycle, MaskReading, MaskRecord, MaskRound
from promptpotter.domain.results import ScoreboardRankKey, scoreboard_rank_key
from promptpotter.domain.strict_model import FrozenModel

if TYPE_CHECKING:
    from promptpotter.domain.paired_reading import ArmKey


class VerdictOutcome(FrozenModel):
    """``alternative_candidate_id`` names the one-step counterfactual only where it was MEASURED, else ``None``."""

    diverged: bool
    alternative_candidate_id: str | None = None


Verdict = Callable[[MaskRound], VerdictOutcome]


class Divergence(FrozenModel):
    cycle_id: str
    round: int
    alternative_candidate_id: str | None = None


class DivergenceResult(FrozenModel):
    """``divergences`` are the markers; ``divergent`` the dimmed ``(cycle_id, round)`` coordinates STRICTLY after each."""

    divergences: list[Divergence] = Field(default_factory=list)
    divergent: list[tuple[str, int]] = Field(default_factory=list)


class MaskedElection(NamedTuple):
    """``winner_key`` ``None`` is "the parent held", a real outcome; no parent reading is ``decidable`` False."""

    decidable: bool
    winner_key: ArmKey | None


class ScenarioStep(NamedTuple):
    round: int
    scenario_key: ArmKey
    recorded_key: ArmKey


def _key(reading: MaskReading) -> ScoreboardRankKey:
    return scoreboard_rank_key(reading.composite_fitness, reading.accuracy)


def masked_election(rnd: MaskRound, parent: MaskReading | None) -> MaskedElection:
    if parent is None:
        return MaskedElection(decidable=False, winner_key=None)
    best_key = _key(parent)
    leader: ArmKey | None = None
    for c in rnd.candidates:
        if not c.is_eligible or c.reading is None:
            continue
        k = _key(c.reading)
        if k > best_key:
            best_key = k
            leader = c.key
    return MaskedElection(decidable=True, winner_key=leader)


def make_scoring_verdict() -> Verdict:
    """Re-ranks the RECORD, never re-runs the election: whether the RUN moves under the formula is ``ab``'s."""

    def verdict(rnd: MaskRound) -> VerdictOutcome:
        if rnd.round == 0:
            return VerdictOutcome(diverged=False)
        recorded_winner = next((c.key for c in rnd.candidates if c.is_selected), None)
        election = masked_election(rnd, rnd.parent)
        if not election.decidable:
            return VerdictOutcome(diverged=False)
        diverged = election.winner_key != recorded_winner
        alternative = election.winner_key if (diverged and election.winner_key) else None
        return VerdictOutcome(diverged=diverged, alternative_candidate_id=alternative)

    return verdict


def make_abort_verdict(suppress: frozenset[str]) -> Verdict:
    """Suppressing a contributor that DID fire is record-computable; ADDING one needs the ``p_best`` stream."""

    def verdict(rnd: MaskRound) -> VerdictOutcome:
        if rnd.round == 0:
            return VerdictOutcome(diverged=False)
        fired = any(c.abort in suppress for c in rnd.candidates)
        return VerdictOutcome(diverged=fired)

    return verdict


def scenario_spine(cycle: MaskCycle) -> list[ScenarioStep]:
    """ENDS on the round the two readings part: past it the run stands on a parent nothing measured."""
    rounds = sorted(cycle.rounds, key=lambda r: r.round)
    first = rounds[0] if rounds else None
    origin = next((c for c in first.candidates if c.reading), None) if first else None
    if origin is None or first is None:
        return []
    standing = origin
    steps = [ScenarioStep(first.round, origin.key, origin.key)]
    for rnd in rounds[1:]:
        by_key = {c.key: c for c in rnd.candidates}
        election = masked_election(rnd, standing.reading)
        elected = by_key.get(election.winner_key) if election.winner_key else None
        # An undecidable round and one that HELD both carry the parent forward: neither is a parting.
        scenario_winner = elected if elected is not None else standing
        crowned = next((c for c in rnd.candidates if c.is_selected), None)
        recorded = crowned if crowned is not None else standing
        steps.append(ScenarioStep(rnd.round, scenario_winner.key, recorded.key))
        if scenario_winner.key != recorded.key:
            break
        standing = scenario_winner
    return steps


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
