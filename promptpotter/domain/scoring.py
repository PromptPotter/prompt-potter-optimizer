"""Scoring models — query-result types and the backend runner protocol."""

from __future__ import annotations

from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable

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
    """Core per-query evaluation result.

    Raw trace fields (``query``, ``ground_truth``, ``predicted``, ``error``,
    ``pipeline_data``) are populated at measurement time. ``hit`` and ``score``
    are the *active-scorer projection* — written exclusively by
    ``rescore_results`` (``shared/scoring.py``), which also populates the
    authoritative ``scored`` audit map (``{scorer_id: {score, hit, formula}}``
    — one entry per scorer the trace has been evaluated under). They are
    ``NotRequired`` because a freshly measured trace has not yet been scored.

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
    """Extended result with optional fields from eval pipeline and stale-data protocol."""

    # Multi-scorer audit map — {scorer_id: {score, hit, formula}}.
    # Accumulated by ``rescore_results``; persisted to both trial JSON
    # and ``library/dataset_runs/`` items so parent + forked cycles can
    # share the same traces with their own scorer-specific views.
    scored: dict[str, dict]

    # Eval pipeline
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
