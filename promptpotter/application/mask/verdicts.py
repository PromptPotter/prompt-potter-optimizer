"""Verdict strategies — each lives beside the math it asks, selected at the API edge. The FOLD is what is
shared, which is why ``replay`` lives in ``resume_and_fork/ab_replay.py`` with the replayers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

from promptpotter.application.mask.divergence import Verdict, VerdictOutcome
from promptpotter.application.mask.record import MaskRound
from promptpotter.application.scoring.metrics import value_with_mask_applied
from promptpotter.domain.rendering import DisplayRankKey, display_rank_key
from promptpotter.domain.scoring import RoundScorer


class MaskedElection(NamedTuple):
    """What a swapped criterion would have made of ONE round, against a stated parent floor.

    ``decidable`` is False where the parent itself is unscorable under the mask: there is then no
    floor to reproduce the "parent held" case against, and every caller must say nothing rather
    than guess. ``winner_id`` is ``None`` for "the parent held" — a real outcome, not an absence.
    """

    decidable: bool
    winner_id: str | None


def masked_election(
    rnd: MaskRound,
    criterion: RoundScorer | str | None,
    parent_evaluators: Mapping[str, float],
    parent_accuracy: float,
) -> MaskedElection:
    """The one-round ranking every mask consumer shares — the divergence verdict against the
    RECORDED parent, the scenario spine against the counterfactual one it threaded forward. Both
    must order candidates identically or a divergence marker and the chain it explains would
    disagree about the same round.

    The eligible filter is the realized one (``is_electable``); the ordering is
    ``display_rank_key`` over the masked aggregate.
    """

    def _key(evaluators: Mapping[str, float], accuracy: float) -> DisplayRankKey | None:
        # A candidate/parent whose stored namespace can't satisfy this mask's formula —
        # it references a schema-bound evaluator absent from those values — is
        # *unscorable under the mask*, not a crash. ``value_with_mask_applied`` owns
        # that single resolution (returns None); same class of incompleteness as the
        # `not c.evaluators` skip below, so we thread None to the caller as a missing
        # candidate. Row-derivable evaluators are recomputed into every record upstream,
        # and the realized formula only names evaluators that WERE stored, so feeding it
        # never trips this — self-consistency is untouched.
        value = value_with_mask_applied(evaluators, criterion)
        return None if value is None else display_rank_key(value, accuracy)

    best_key = _key(parent_evaluators, parent_accuracy)
    if not parent_evaluators or best_key is None:
        return MaskedElection(decidable=False, winner_id=None)
    leader_id: str | None = None  # the parent holds until a challenger beats it
    for c in rnd.candidates:
        if not c.is_eligible or not c.evaluators:
            continue
        k = _key(c.evaluators, c.accuracy)
        if k is None:  # unscorable under this mask — skip, like missing evaluators
            continue
        if k > best_key:
            best_key = k
            leader_id = c.candidate_id
    return MaskedElection(decidable=True, winner_id=leader_id)


def make_scoring_verdict(criterion: RoundScorer | str | None) -> Verdict:
    """The scoring verdict for a swapped criterion: **re-ranks the RECORD, it does not re-run the
    election.** The ordering is :func:`masked_election`'s, where the election ranks Rasch θ-lift
    over the parent behind a coverage floor.

    That gap is not closable here: θ under another formula must be re-fit from per-sample grades
    against a re-calibrated δ ruler — ``ab_replay``'s substrate (``with_replay=True`` plus an
    archive read), not a cheaper version of it. So a divergence means "under this formula the
    crowned candidate is no longer the best-scoring one", where ``ab`` answers if the RUN moved."""

    def verdict(rnd: MaskRound) -> VerdictOutcome:
        # Round 0 holds no election, so there is nothing it could have decided differently.
        if rnd.round == 0:
            return VerdictOutcome(diverged=False)
        recorded_winner = next((c.candidate_id for c in rnd.candidates if c.is_winner), None)
        election = masked_election(rnd, criterion, rnd.parent_evaluators, rnd.parent_accuracy)
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
