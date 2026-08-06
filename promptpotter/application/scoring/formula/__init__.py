from __future__ import annotations

from typing import Any

from promptpotter.application.scoring.formula.compiler import (
    ScoringFormulaError,
    ScoringTermMissingError,
    auto_scorer_id,
    compile_scorer,
    split_scoring_block,
)
from promptpotter.application.scoring.formula.matchers import SCORING_FUNCTIONS
from promptpotter.application.scoring.formula.rescore import rescore_results
from promptpotter.application.scoring.formula.round_scorer import compile_round_scorer


def extract_item_label(c: Any) -> str:
    """Display label of a ranked item (dict ``{candidate: ...}``, list/tuple, or string)."""
    if isinstance(c, dict):
        return str(c.get("candidate", c))
    return c[0] if isinstance(c, (list, tuple)) else str(c)


__all__ = [
    "SCORING_FUNCTIONS",
    "ScoringFormulaError",
    "ScoringTermMissingError",
    "auto_scorer_id",
    "compile_round_scorer",
    "compile_scorer",
    "extract_item_label",
    "rescore_results",
    "split_scoring_block",
]
