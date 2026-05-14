"""Scoring formula compiler + match/display primitives.

The compiler turns a ``campaign.json::scoring`` formula string into a
callable that scores one ``QueryMeasurement`` dict; ``rescore_results`` is
the sole writer of top-level ``hit``/``fitness`` and the ``scored`` audit
map.

Per-sample formula namespace:

    hit                 — bool (1/0), exact match at rank 1
    ground_truth_rank   — int or None, 1-based position in ranking
    n_candidates        — int, total candidates returned
    predicted           — str, model output
    ground_truth        — str, expected answer
    error               — str or None
    <node_name>         — SimpleNamespace of that node's pipeline_data
    evaluators          — SimpleNamespace of per-sample Evaluator values

Per-round formula namespace is built from ``application/scoring/evaluators`` —
every registered per-round evaluator whose ``applies(schema)`` is True
contributes one named value. Undefined names raise ``NameError`` (fail loud).
"""

from __future__ import annotations

from promptpotter.application.scoring.formula.compiler import (
    auto_scorer_id,
    compile_scorer,
    split_scoring_block,
)
from promptpotter.application.scoring.formula.matchers import (
    DISPLAY_EXTRACTORS,
    SCORING_FUNCTIONS,
    extract_display_answer,
)
from promptpotter.application.scoring.formula.primitives import extract_item_label
from promptpotter.application.scoring.formula.rescore import rescore_results
from promptpotter.application.scoring.formula.round_scorer import compile_round_scorer

__all__ = [
    "DISPLAY_EXTRACTORS",
    "SCORING_FUNCTIONS",
    "auto_scorer_id",
    "compile_round_scorer",
    "compile_scorer",
    "extract_display_answer",
    "extract_item_label",
    "rescore_results",
    "split_scoring_block",
]
