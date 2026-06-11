"""Scoring domain types — pure data; compiler + ``rescore_results`` in ``application.scoring.formula``.

Datasets declare a formula in ``campaign.json::scoring``:
- string shorthand ⇒ ``per_sample`` (``per_round`` uses registry default);
- twin form ``{"per_sample", "per_round"}``.

Missing ``scoring`` raises in ``compile_scorer`` — no implicit default.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple, NotRequired, TypedDict

from promptpotter.shared.errors import ErrorCategory


class PipelineData(TypedDict, total=False):
    """Nested pipeline execution details within a QueryMeasurement."""

    # The pipeline's result ranking — the terminal ranker's output, derived at
    # measurement time (``terminal_ranking``). The scorer + ``find_gt_rank`` read this.
    result_ranking: list[dict[str, Any]]
    # Raw per-node ranker outputs, copied from the wire response for per-node
    # diagnostics (retriever recall vs ranker precision). One of these is the source
    # ``result_ranking`` was derived from; both may be absent for non-ranking pipelines.
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
    # True when this measurement was reused from a prior identical searchpoint
    # instead of a fresh backend call. Stamped ``False`` at measurement time,
    # ``True`` by ``_materialize_cached``. Always present so readers (the live
    # tape + the per-candidate audit table) can show fresh-vs-cached uniformly.
    cached: NotRequired[bool]
    error: str | None
    # Typed error channel: the category owns "this sample errored"; ``error`` is a
    # plain human message (no ``[TAG]`` prefix). ``None``/absent ⇒ clean measurement.
    error_category: NotRequired[ErrorCategory | None]
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
