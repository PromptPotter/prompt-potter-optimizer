"""``scenario_spine`` — how far a swapped criterion agrees with the record, and the round it stops
agreeing. Where ``find_divergences`` answers *which rounds rank differently* one round at a time,
this walks the chain: each round is decided against the winner the scenario is standing on, which
is only meaningful while that is still the winner the run carried.

**It ends at the round the two part.** Past that the run would have stood on a parent it never had
— nothing was measured against it, and L1 would have generated a different population from it — so
there is no record to read and the honest chain is the one that stops. That round is also where a
fork applying the criterion is minted: ``resume_and_fork/resume.py`` branches at exactly this round
on a scoring divergence, carrying the rounds before it.

Pure over the record, no I/O. Every candidate on the chain was MEASURED — the fold picks among the
arms the run actually ran and never invents one.

**The lens cannot be ranked BY the election, at any price worth paying — don't file it as a
one-line consistency fix.** θ under another formula must be re-fit from per-sample grades against a
re-calibrated δ ruler, which is ``resume_and_fork/ab_replay.py``'s substrate: the lens and ``ab``
are ONE mechanism at two prices, and the cheap one is what the tree route polls. Adopting the exact
one puts a campaign-wide refit behind a 5 s poll.
"""

from __future__ import annotations

from typing import NamedTuple

from promptpotter.application.mask.record import MaskCycle
from promptpotter.application.mask.verdicts import masked_election
from promptpotter.domain.scoring import RoundScorer


class ScenarioStep(NamedTuple):
    """One round of the chain: who the scenario stands on, and who the record stood on there. The
    two differ only on the LAST step, since the walk ends on the round that parts — so the pair is
    readable as one round's disagreement rather than a flag a reader has to date."""

    round: int
    candidate_id: str
    recorded_id: str


def scenario_spine(cycle: MaskCycle, criterion: RoundScorer | str | None) -> list[ScenarioStep]:
    """The branch as *criterion* would have run it, origin first, up to and including the round the
    two readings part.

    A round that cannot be decided under the mask (its standing parent is unscorable there) carries
    the parent forward rather than guessing — the honest reading, and the same one the divergence
    verdict takes. So does a round that HELD: the record crowns nobody, the scenario is still
    standing on whoever it carried in, and agreeing on the parent is not a parting.
    """
    rounds = sorted(cycle.rounds, key=lambda r: r.round)
    first = rounds[0] if rounds else None
    # The branch's starting point — the first round's single arm, which no criterion elects and
    # every criterion inherits. A branch whose first round scored nothing has no chain to walk,
    # and taking the NEXT round's arm instead would claim it began somewhere it did not.
    origin = next((c for c in first.candidates if c.evaluators), None) if first else None
    if origin is None or first is None:
        return []
    # One variable for both readings' standing winner: they are equal at the top of every round the
    # walk reaches, since the round that separates them is the round it returns on.
    standing = origin
    steps = [ScenarioStep(first.round, origin.candidate_id, origin.candidate_id)]
    for rnd in rounds[1:]:
        by_id = {c.candidate_id: c for c in rnd.candidates}
        election = masked_election(rnd, criterion, standing.evaluators, standing.accuracy)
        elected = by_id.get(election.winner_id) if election.winner_id else None
        scenario_winner = elected if elected is not None else standing
        crowned = next((c for c in rnd.candidates if c.is_winner), None)
        recorded = crowned if crowned is not None else standing
        steps.append(ScenarioStep(rnd.round, scenario_winner.candidate_id, recorded.candidate_id))
        if scenario_winner.candidate_id != recorded.candidate_id:
            break
        standing = scenario_winner
    return steps


__all__ = ["ScenarioStep", "scenario_spine"]
