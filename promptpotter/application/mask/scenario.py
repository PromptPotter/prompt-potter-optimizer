from __future__ import annotations

from typing import NamedTuple

from promptpotter.application.mask.record import MaskCycle
from promptpotter.application.mask.verdicts import masked_election


class ScenarioStep(NamedTuple):
    round: int
    candidate_id: str
    recorded_id: str


def scenario_spine(cycle: MaskCycle) -> list[ScenarioStep]:
    """ENDS on the round the two readings part: past it the run stands on a parent nothing measured."""
    rounds = sorted(cycle.rounds, key=lambda r: r.round)
    first = rounds[0] if rounds else None
    origin = next((c for c in first.candidates if c.reading), None) if first else None
    if origin is None or first is None:
        return []
    standing = origin
    steps = [ScenarioStep(first.round, origin.candidate_id, origin.candidate_id)]
    for rnd in rounds[1:]:
        by_id = {c.candidate_id: c for c in rnd.candidates}
        election = masked_election(rnd, standing.reading)
        elected = by_id.get(election.winner_id) if election.winner_id else None
        # An undecidable round and one that HELD both carry the parent forward: neither is a parting.
        scenario_winner = elected if elected is not None else standing
        crowned = next((c for c in rnd.candidates if c.is_selected), None)
        recorded = crowned if crowned is not None else standing
        steps.append(ScenarioStep(rnd.round, scenario_winner.candidate_id, recorded.candidate_id))
        if scenario_winner.candidate_id != recorded.candidate_id:
            break
        standing = scenario_winner
    return steps


__all__ = ["ScenarioStep", "scenario_spine"]
