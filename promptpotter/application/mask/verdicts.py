"""Verdict strategies — each lives where its math already is, selected at the API
edge. The fold (:func:`find_divergences`) folds one of these over the record.

**scoring** (M1) is here because its math is scoring math: it re-scores candidates
under an alternative formula through the single re-evaluation seam
(``value_with_mask_applied`` — a formula over the stored evaluator namespace, no
schema, no re-run) and re-runs the *exact* realized election to ask "would this
round have elected someone else?". The abort verdict (a log-read over
``elimination_context``, no value face) lands here too when it migrates.
"""

from __future__ import annotations

from collections.abc import Mapping

from promptpotter.application.mask.divergence import Verdict, VerdictOutcome
from promptpotter.application.mask.record import MaskRound
from promptpotter.application.optimization.l1.score.winner import round_winner_key
from promptpotter.application.scoring.metrics import value_with_mask_applied
from promptpotter.domain.scoring import RoundScorer


def make_scoring_verdict(criterion: RoundScorer | str | None) -> Verdict:
    """Build the **scoring** verdict for a swapped ``criterion`` (a formula string
    like ``"accuracy"``, a compiled ``RoundScorer``, or ``None`` for accuracy-only).

    Per round it re-runs the realized election under the swap: ``argmax
    round_winner_key`` over ``{origin-anchor} ∪ eligible candidates``, each re-scored
    from its *own* stored evaluator namespace via ``value_with_mask_applied``. The
    eligible filter and the election key are the realized ones verbatim — only the
    formula changes — so feeding the *realizing* criterion reproduces ``is_winner``
    exactly (the self-consistency gate). A flip from the recorded winner is a
    divergence; the flipped-to candidate is the one-step alternative (it was measured
    = nameable).
    """

    def _key(evaluators: Mapping[str, float], accuracy: float) -> tuple[float, float]:
        return round_winner_key(value_with_mask_applied(evaluators, criterion), accuracy)

    def verdict(rnd: MaskRound) -> VerdictOutcome:
        # Origin round 0 holds no election; and without the anchor we cannot
        # reproduce the "origin held" case → honest: no divergence claimed.
        if rnd.round == 0 or not rnd.anchor_evaluators:
            return VerdictOutcome(diverged=False)

        recorded_winner = next((c.candidate_id for c in rnd.candidates if c.is_winner), None)

        best_key = _key(rnd.anchor_evaluators, rnd.anchor_accuracy)
        leader_id: str | None = None  # origin/anchor holds until a challenger beats it
        for c in rnd.candidates:
            if not c.is_eligible or not c.evaluators:
                continue
            k = _key(c.evaluators, c.accuracy)
            if k > best_key:
                best_key = k
                leader_id = c.candidate_id

        diverged = leader_id != recorded_winner
        alternative = leader_id if (diverged and leader_id is not None) else None
        return VerdictOutcome(diverged=diverged, alternative_candidate_id=alternative)

    return verdict


__all__ = ["make_scoring_verdict"]
