"""Per-round formula compiler.

Returns a ``RoundScorer`` callable over the per-round evaluator-name map.
Undefined names raise ``NameError`` (fail loud — every per-round
evaluator should be registered).
"""

from __future__ import annotations

import ast

from promptpotter.application.scoring.formula.compiler import _SAFE_BUILTINS, validate_ast
from promptpotter.domain.scoring import RoundScorer


def compile_round_scorer(formula: str | None) -> RoundScorer:
    """Pre-compile a per-round scoring formula. Undefined names raise ``NameError``."""
    if not formula:
        return _default_round_scorer

    tree = ast.parse(formula, "<round_scoring>", "eval")
    validate_ast(tree, source="per_round scoring formula")
    code = compile(tree, "<round_scoring>", "eval")

    def _scorer(values: dict[str, float]) -> float:
        raw = eval(code, _SAFE_BUILTINS, dict(values))
        return max(0.0, min(1.0, float(raw)))

    return _scorer


def _default_round_scorer(values: dict[str, float]) -> float:
    return max(0.0, min(1.0, float(values.get("accuracy", 0.0))))


__all__ = ["compile_round_scorer"]
