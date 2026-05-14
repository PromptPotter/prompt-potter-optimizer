"""Scoring domain types — query-result shapes, runner protocol, scorer aliases.

Pure data layer: no behavior, no I/O. The formula compiler, match/display
primitives, and ``rescore_results`` mutator live in
``promptpotter.application.scoring.formula``.

Each dataset declares a scoring formula in ``campaign.json`` under ``"scoring"``.
Accepted shapes:

- **String shorthand** — interpreted as ``per_sample``; ``per_round`` uses the evaluator-registry default.
- **Twin form** — ``{"per_sample": "...", "per_round": "..."}``; omitted keys fall back to defaults.

A missing ``scoring`` key raises in ``compile_scorer`` — there is no implicit default.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple, NotRequired, TypedDict

# ---------------------------------------------------------------------------
# Per-sample result types
# ---------------------------------------------------------------------------


class PipelineData(TypedDict, total=False):
    """Nested pipeline execution details within a QueryMeasurement."""

    final_ranking: list[dict[str, Any]]
    total_time: float
    terminated_at: str
    step_timings: dict[str, Any]
    # Per-LLM-node token counts: {node_name: {"input": N, "output": M, "estimated": bool}}.
    # ``estimated`` is True when counts came from a chars/4 fallback rather than
    # the provider's usage object via the backend.
    step_tokens: dict[str, dict[str, int | bool]]
    llm_provider: str
    pipeline_params: dict[str, Any]
    diagnostics: dict[str, Any]


class QueryMeasurement(TypedDict):
    """Core per-sample measurement result.

    Raw trace fields (``query``, ``ground_truth``, ``predicted``, ``error``,
    ``pipeline_data``) are populated at measurement time. ``hit`` and ``fitness``
    are the *active-scorer projection* — written exclusively by
    ``rescore_results`` (in ``application.scoring.formula``), which also
    populates the authoritative ``scored`` audit map (``{scorer_id: {fitness,
    hit, formula}}`` — one entry per scorer the trace has been scored
    under). They are ``NotRequired`` because a freshly measured trace has not
    yet been scored.

    ``sample_id`` is the foreign key back to ``Sample.id`` — canonical,
    assigned at dataset creation, stable across campaigns.
    """

    sample_id: int
    query: str
    ground_truth: str
    predicted: str
    hit: NotRequired[bool]
    fitness: NotRequired[float]
    error: str | None
    pipeline_data: PipelineData | None


# ---------------------------------------------------------------------------
# Scorer type aliases + sentinel ids
# ---------------------------------------------------------------------------

Scorer = Callable[[dict], float]
RoundScorer = Callable[[dict[str, float]], float]

DEFAULT_SCORER_ID = "default_hit"
EMPTY_SCORER_ID = "none"


# ---------------------------------------------------------------------------
# Twin-form scoring block — parsed shape
# ---------------------------------------------------------------------------


class ScoringSpec(NamedTuple):
    """Parsed ``campaign.json::scoring`` block — ``(per_sample, per_round, scorer_id)``."""

    per_sample: str | None
    per_round: str | None
    scorer_id: str


__all__ = [
    "DEFAULT_SCORER_ID",
    "EMPTY_SCORER_ID",
    "PipelineData",
    "QueryMeasurement",
    "RoundScorer",
    "Scorer",
    "ScoringSpec",
]
