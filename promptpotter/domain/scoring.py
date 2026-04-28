"""Scoring domain types — query-result shapes, runner protocol, scorer aliases.

Pure data layer: no behavior, no I/O. The formula compiler, match/display
primitives, and ``rescore_results`` mutator live in
``promptpotter.application.scoring.formula``.

Each dataset declares a scoring formula in ``campaign.json`` under ``"scoring"``.
Accepted shapes:

- **String shorthand** — interpreted as ``per_query``; ``per_round`` uses the evaluator-registry default.
- **Twin form** — ``{"per_query": "...", "per_round": "..."}``; omitted keys fall back to defaults.

No ``scoring`` key → defaults to ``float(hit)`` (exact-match, legacy).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple, NotRequired, Protocol, TypedDict, runtime_checkable

# ---------------------------------------------------------------------------
# Per-query result types
# ---------------------------------------------------------------------------


class PipelineData(TypedDict, total=False):
    """Nested pipeline execution details within a QueryResult."""

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


class QueryResult(TypedDict):
    """Core per-query measurement result.

    Raw trace fields (``query``, ``ground_truth``, ``predicted``, ``error``,
    ``pipeline_data``) are populated at measurement time. ``hit`` and ``score``
    are the *active-scorer projection* — written exclusively by
    ``rescore_results`` (in ``application.scoring.formula``), which also
    populates the authoritative ``scored`` audit map (``{scorer_id: {score,
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
    score: NotRequired[float]
    error: str | None
    pipeline_data: PipelineData | None


class QueryResultFull(QueryResult, total=False):
    """Extended result with optional fields from scoring pipeline and stale-data protocol."""

    # Multi-scorer audit map — {scorer_id: {score, hit, formula}}.
    # Accumulated by ``rescore_results``; persisted to both trial JSON
    # and ``library/measurements/`` items so parent + forked cycles can
    # share the same traces with their own scorer-specific views.
    scored: dict[str, dict]

    # Scoring pipeline
    n_candidates: int
    ground_truth_rank: int | None
    cached: bool

    # Stale-data protocol fields (set by stale_data.py)
    retry_of_degraded: bool
    rerun_comparison: dict[str, Any]
    samplescan_resolved: bool
    samplescan_config: dict[str, Any]
    degraded_observed: bool
    degraded_obs_count: int
    degraded_obs_threshold: int
    switched_out: bool
    persistently_degraded: bool

    # Deprecated-sample retry: set in search_point_scorer when a fresh
    # measurement supersedes a cached entry that carried a fatal warning.
    retry_of_deprecated_cache: bool


# ---------------------------------------------------------------------------
# Query runner protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class QueryRunner(Protocol):
    """Async backend connector interface.

    Satisfied by :class:`BackendClient`.
    """

    async def run_query(
        self,
        query: str,
        pipeline_params: dict[str, Any] | None = ...,
    ) -> dict[str, Any]: ...

    async def check_status(self) -> dict[str, Any]: ...

    async def fetch_pipeline(self) -> dict[str, Any]: ...

    async def init_session(self, terms: list[str]) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


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
    """Parsed ``campaign.json::scoring`` block — ``(per_query, per_round, scorer_id)``."""

    per_query: str | None
    per_round: str | None
    scorer_id: str
