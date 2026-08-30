"""The MASK's formula compiler, over a round's stored per-round evaluator map. A name the map lacks is an UNMEASURED
term, not a zero — the registry omits an evaluator's key when it had nothing to measure, so a formula naming it halts
rather than scoring on a default nobody measured.

Not the campaign's composite: that is per-CELL (``domain/scoring.py::CellScorer``) and is what θ is fit on. This one
answers a read-side counterfactual — *what would this round have scored under formula X* — off a record whose rows may
be gone, which is what keeps it at round scope. Where a formula is nonlinear in the per-cell terms the two are the
projection and the thing projected, and only the linear case makes them agree."""

from __future__ import annotations

from promptpotter.application.scoring.formula.compiler import (
    ScoringTermMissingError,
    clamp_unit_score,
    compile_expression,
)
from promptpotter.domain.scoring import RoundScorer

# What a mask with no formula is read on — the round's plain accuracy.
_DEFAULT_TERM = "accuracy"


def compile_round_scorer(formula: str | None) -> RoundScorer:
    """Pre-compile a per-round scoring formula. An unmeasured term raises ``ScoringFormulaError``."""
    if not formula:
        return _default_round_scorer

    compiled = compile_expression(formula, source="per_round scoring formula")

    def _scorer(values: dict[str, float]) -> float:
        return clamp_unit_score(
            compiled.evaluate(dict(values), "the round"), formula=formula, subject="the round"
        )

    return _scorer


def _default_round_scorer(values: dict[str, float]) -> float:
    if _DEFAULT_TERM not in values:
        raise ScoringTermMissingError(
            f"The round measured {sorted(values)} — no {_DEFAULT_TERM!r}, so the default "
            "composite has nothing to score. A round whose samples were all unscoreable has no "
            "fitness; it is not a fitness of zero."
        )
    return clamp_unit_score(values[_DEFAULT_TERM], formula=_DEFAULT_TERM, subject="the round")


__all__ = ["compile_round_scorer"]
