"""Per-round formula compiler. A name the map lacks is an UNMEASURED term, not a zero — the registry omits an evaluator's
key when it had nothing to measure, so a formula naming it halts rather than scoring on a default nobody measured."""

from __future__ import annotations

from promptpotter.application.scoring.formula.compiler import (
    ScoringTermMissingError,
    clamp_unit_score,
    compile_expression,
)
from promptpotter.domain.scoring import RoundScorer

# The term a caller that supplies no formula is scored on. Not a second owner of the default
# formula (``evaluators.default_per_round_formula`` is that, and every loop caller resolves it
# before compiling) — this is the floor for the callers that pass ``None`` outright.
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
