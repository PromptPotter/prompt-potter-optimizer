"""Verdict strategies — each lives beside the math it asks, selected at the API edge. The FOLD is what is
shared, which is why ``replay`` lives in ``resume_and_fork/ab_replay.py`` with the replayers."""

from __future__ import annotations

from collections.abc import Mapping

from promptpotter.application.mask.divergence import Verdict, VerdictOutcome
from promptpotter.application.mask.record import MaskRound
from promptpotter.application.scoring.metrics import value_with_mask_applied
from promptpotter.domain.rendering import round_winner_key
from promptpotter.domain.scoring import RoundScorer


def make_scoring_verdict(criterion: RoundScorer | str | None) -> Verdict:
    """The scoring verdict for a swapped criterion. The eligible filter and the election key are the realized ones verbatim
    — only the formula changes — so feeding the REALIZING criterion reproduces ``is_winner`` exactly."""

    def _key(evaluators: Mapping[str, float], accuracy: float) -> tuple[float, float] | None:
        # A candidate/anchor whose stored namespace can't satisfy this mask's formula —
        # it references a schema-bound evaluator absent from those values — is
        # *unscorable under the mask*, not a crash. ``value_with_mask_applied`` owns
        # that single resolution (returns None); same class of incompleteness as the
        # `not c.evaluators` skip below, so we thread None to the caller as a missing
        # candidate. Row-derivable evaluators are recomputed into every record upstream,
        # and the realized formula only names evaluators that WERE stored, so feeding it
        # never trips this — self-consistency is untouched.
        value = value_with_mask_applied(evaluators, criterion)
        return None if value is None else round_winner_key(value, accuracy)

    def verdict(rnd: MaskRound) -> VerdictOutcome:
        # Origin round 0 holds no election; and without the anchor we cannot
        # reproduce the "origin held" case → honest: no divergence claimed.
        if rnd.round == 0 or not rnd.anchor_evaluators:
            return VerdictOutcome(diverged=False)

        recorded_winner = next((c.candidate_id for c in rnd.candidates if c.is_winner), None)

        best_key = _key(rnd.anchor_evaluators, rnd.anchor_accuracy)
        # Anchor unscorable under this mask ⇒ no origin floor to reproduce the
        # "origin held" case against → honest: claim no divergence this round.
        if best_key is None:
            return VerdictOutcome(diverged=False)
        leader_id: str | None = None  # origin/anchor holds until a challenger beats it
        for c in rnd.candidates:
            if not c.is_eligible or not c.evaluators:
                continue
            k = _key(c.evaluators, c.accuracy)
            if k is None:  # unscorable under this mask — skip, like missing evaluators
                continue
            if k > best_key:
                best_key = k
                leader_id = c.candidate_id

        diverged = leader_id != recorded_winner
        alternative = leader_id if (diverged and leader_id is not None) else None
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


__all__ = ["make_abort_verdict", "make_scoring_verdict"]
