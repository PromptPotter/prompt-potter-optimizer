"""Verdict strategies — each lives beside the math it asks, selected at the API edge. The FOLD is what is
shared, which is why ``replay`` lives in ``resume_and_fork/ab_replay.py`` with the replayers."""

from __future__ import annotations

from typing import NamedTuple

from promptpotter.application.mask.divergence import Verdict, VerdictOutcome
from promptpotter.application.mask.record import MaskReading, MaskRound
from promptpotter.domain.results import ScoreboardRankKey, scoreboard_rank_key


class MaskedElection(NamedTuple):
    """What the record's scorer would have made of ONE round, against a stated parent floor.

    ``decidable`` is False where the parent itself has no reading: there is then no floor to
    reproduce the "parent held" case against, and every caller must say nothing rather than guess.
    ``winner_id`` is ``None`` for "the parent held" — a real outcome, not an absence.
    """

    decidable: bool
    winner_id: str | None


def _key(reading: MaskReading) -> ScoreboardRankKey:
    return scoreboard_rank_key(reading.composite_fitness, reading.accuracy)


def masked_election(rnd: MaskRound, parent: MaskReading | None) -> MaskedElection:
    """The one-round ranking every mask consumer shares — the divergence verdict against the
    RECORDED parent, the scenario spine against the counterfactual one it threaded forward. Both
    must order candidates identically or a divergence marker and the chain it explains would
    disagree about the same round.

    The eligible filter is the realized one (``is_electable``); the ordering is
    ``scoreboard_rank_key`` over each arm's reading. An arm with none is skipped, never scored 0.
    """
    if parent is None:
        return MaskedElection(decidable=False, winner_id=None)
    best_key = _key(parent)
    leader_id: str | None = None  # the parent holds until a challenger beats it
    for c in rnd.candidates:
        if not c.is_eligible or c.reading is None:
            continue
        k = _key(c.reading)
        if k > best_key:
            best_key = k
            leader_id = c.candidate_id
    return MaskedElection(decidable=True, winner_id=leader_id)


def make_scoring_verdict() -> Verdict:
    """The scoring verdict over a record read under a swapped criterion: **re-ranks the RECORD, it
    does not re-run the election.** The ordering is :func:`masked_election`'s, where the election
    ranks Rasch θ-lift over the parent behind a coverage floor.

    That gap is not closable here: θ under another formula must be re-fit from per-sample grades
    against a re-calibrated δ ruler — ``ab_replay``'s substrate (``with_replay=True`` plus an
    archive read), not a cheaper version of it. So a divergence means "under this formula the
    crowned candidate is no longer the best-scoring one", where ``ab`` answers if the RUN moved."""

    def verdict(rnd: MaskRound) -> VerdictOutcome:
        # Round 0 holds no election, so there is nothing it could have decided differently.
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
    """The abort verdict. Suppressing a contributor that DID fire is record-computable; ADDING one the run lacked
    is not — that needs the per-step ``p_best`` stream, and belongs on the real-run sibling-cycle path."""

    def verdict(rnd: MaskRound) -> VerdictOutcome:
        if rnd.round == 0:
            return VerdictOutcome(diverged=False)
        fired = any(c.abort in suppress for c in rnd.candidates)
        return VerdictOutcome(diverged=fired)

    return verdict


__all__ = ["MaskedElection", "make_abort_verdict", "make_scoring_verdict", "masked_election"]
