"""Per-round formula compiler. A name the map lacks is an UNMEASURED term, not a zero — the registry omits an evaluator's
key when it had nothing to measure, so a formula naming it halts rather than scoring on a default nobody measured."""

from __future__ import annotations

import ast

from promptpotter.application.scoring.formula.compiler import (
    _SAFE_BUILTINS,
    ScoringFormulaError,
    ScoringTermMissingError,
    clamp_unit_score,
    validate_ast,
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

    tree = ast.parse(formula, "<round_scoring>", "eval")
    validate_ast(tree, source="per_round scoring formula")
    code = compile(tree, "<round_scoring>", "eval")

    def _scorer(values: dict[str, float]) -> float:
        try:
            raw = eval(code, _SAFE_BUILTINS, dict(values))
        except NameError as exc:
            raise ScoringTermMissingError(
                f"Per-round scoring formula {formula!r}: {exc}. The round measured "
                f"{sorted(values)}. A term the round could not measure is absent from that map "
                "on purpose — score the round without it, or exclude the round."
            ) from exc
        except Exception as exc:
            raise ScoringFormulaError(
                f"Per-round scoring formula {formula!r} raised: {type(exc).__name__}: {exc}."
            ) from exc
        return clamp_unit_score(raw, formula=formula, subject="the round")

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
