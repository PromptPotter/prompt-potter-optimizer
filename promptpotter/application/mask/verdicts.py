"""``replay`` lives in ``resume_and_fork/ab_replay.py`` with the replayers: the FOLD is what is shared."""

from __future__ import annotations

from typing import NamedTuple

from promptpotter.application.mask.divergence import Verdict, VerdictOutcome
from promptpotter.application.mask.record import MaskReading, MaskRound
from promptpotter.domain.results import ScoreboardRankKey, scoreboard_rank_key


class MaskedElection(NamedTuple):
    """``winner_id`` ``None`` is "the parent held", a real outcome; no parent reading is ``decidable`` False."""

    decidable: bool
    winner_id: str | None


def _key(reading: MaskReading) -> ScoreboardRankKey:
    return scoreboard_rank_key(reading.composite_fitness, reading.accuracy)


def masked_election(rnd: MaskRound, parent: MaskReading | None) -> MaskedElection:
    """Shared by the divergence verdict and the scenario spine, which must order one round identically."""
    if parent is None:
        return MaskedElection(decidable=False, winner_id=None)
    best_key = _key(parent)
    leader_id: str | None = None
    for c in rnd.candidates:
        if not c.is_eligible or c.reading is None:
            continue
        k = _key(c.reading)
        if k > best_key:
            best_key = k
            leader_id = c.candidate_id
    return MaskedElection(decidable=True, winner_id=leader_id)


def make_scoring_verdict() -> Verdict:
    """Re-ranks the RECORD, never re-runs the election: whether the RUN moves under the formula is ``ab``'s."""

    def verdict(rnd: MaskRound) -> VerdictOutcome:
        if rnd.round == 0:
            return VerdictOutcome(diverged=False)
        recorded_winner = next((c.candidate_id for c in rnd.candidates if c.is_selected), None)
        election = masked_election(rnd, rnd.parent)
        if not election.decidable:
            return VerdictOutcome(diverged=False)
        diverged = election.winner_id != recorded_winner
        alternative = election.winner_id if (diverged and election.winner_id) else None
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


__all__ = ["MaskedElection", "make_abort_verdict", "make_scoring_verdict", "masked_election"]
