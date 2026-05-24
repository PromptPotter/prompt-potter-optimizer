"""Scoring domain types — pure data; compiler + ``rescore_results`` in ``application.scoring.formula``.

Datasets declare a formula in ``campaign.json::scoring``:
- string shorthand ⇒ ``per_sample`` (``per_round`` uses registry default);
- twin form ``{"per_sample", "per_round"}``.

Missing ``scoring`` raises in ``compile_scorer`` — no implicit default.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple, NotRequired, TypedDict


class PipelineData(TypedDict, total=False):
    """Nested pipeline execution details within a QueryMeasurement."""

    final_ranking: list[dict[str, Any]]
    total_time: float
    terminated_at: str
    step_timings: dict[str, Any]
    # Per-LLM-node tokens: ``{node: {input, output, estimated}}``;
    # ``estimated=True`` ⇒ counts came from chars/4 fallback, not provider usage.
    step_tokens: dict[str, dict[str, int | bool]]
    llm_provider: str
    pipeline_params: dict[str, Any]
    diagnostics: dict[str, Any]


class QueryMeasurement(TypedDict):
    """Per-sample measurement.

    Raw trace fields (query/ground_truth/predicted/error/pipeline_data) populated
    at measurement time. ``hit``/``fitness`` are the active-scorer projection,
    written exclusively by ``rescore_results`` (which also populates the
    ``scored`` audit map: ``{scorer_id: {fitness, hit, formula}}``). ``NotRequired``
    because a freshly measured trace hasn't been scored yet.

    ``sample_id`` = foreign key to ``Sample.id``, stable across campaigns.
    """

    sample_id: int
    query: str
    ground_truth: str
    predicted: str
    hit: NotRequired[bool]
    fitness: NotRequired[float]
    error: str | None
    pipeline_data: PipelineData | None


Scorer = Callable[[dict[str, Any]], float]
RoundScorer = Callable[[dict[str, float]], float]

DEFAULT_SCORER_ID = "default_hit"
EMPTY_SCORER_ID = "none"


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
