"""Scoring formula compiler + match/display primitives.

- `compiler.py` — compiles a `campaign.json::scoring` expression into a scorer.
- `round_scorer.py` — `compile_round_scorer`, the per-round formula over the
  evaluator namespace.
- `matchers.py` — `SCORING_FUNCTIONS` (the per-sample matchers) + `EXTRACTION_NOTES`,
  the `answer_format` contract each extract-then-compare matcher imposes on the
  committed prompt.
- `rescore.py` — re-derives stored measurements under a changed formula.

**Answer extraction is a double seam; this is only one arm.** Here a matcher pulls
the LABEL out of the model's prose (last bold span, last `\\boxed{N}`) and decides
HIT/MISS. The other arm runs earlier and elsewhere: `connectors/llm_only.py::
_extract_answer` destructures the structured-output SLOT named by `answer_field`,
before any scoring. Slot first, label second — a task that returns JSON is shaped
by the connector arm, and no matcher here will ever see the raw envelope.
Display-side extraction is a third thing again (`domain/rendering.py`).
"""

from __future__ import annotations

from typing import Any

from promptpotter.application.scoring.formula.compiler import (
    ScoringFormulaError,
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
    "auto_scorer_id",
    "compile_round_scorer",
    "compile_scorer",
    "extract_item_label",
    "rescore_results",
    "split_scoring_block",
]
