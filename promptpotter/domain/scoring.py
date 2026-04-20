"""Scoring models — query results and the infrastructure bundle.

``QueryResult`` / ``QueryResultFull`` define the per-query measurement schema.
``ScoringEnv`` bundles infrastructure for dataset scoring calls. Ground-truth
comparison is owned by the compiled ``Scorer`` callable (from
``shared/scoring.py``) — there is no separate comparator class.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NotRequired, Protocol, TypedDict, runtime_checkable

from promptpotter.shared.scoring import RoundScorer, Scorer

# ---------------------------------------------------------------------------
# Per-query result types
# ---------------------------------------------------------------------------


class PipelineData(TypedDict, total=False):
    """Nested pipeline execution details within a QueryResult."""

    final_ranking: list[dict[str, Any]]
    total_time: float
    terminated_at: str
    step_timings: dict[str, Any]
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
    """

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
    samplescan_probe: bool
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


if TYPE_CHECKING:
    from promptpotter.application.intelligence.search_memory import SearchMemory
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores
    from promptpotter.infrastructure.tracing import ObservabilityBridge


@dataclass
class ScoringEnv:
    """Infrastructure bundle shared across scoring and measurement calls.

    Pure infrastructure — does NOT carry search-space dimensions
    (pipeline_params).  Those live on the SearchPoint passed alongside.
    Per-candidate state (candidate_idx, degradation_checks) is passed
    explicitly to score_search_point(), not stored here.
    """

    backend_client: QueryRunner
    # Per-dataset scoring formula (compiled callable from shared/scoring.py).
    # Required — ``rescore_results`` is the sole writer of ``hit``/``score``
    # on results, so a live scorer must be threaded through every path that
    # measures or loads traces.
    scorer: Scorer
    store: Stores | None = None
    backend_id: str = ""
    pipeline_schema: PipelineSchema | None = None
    obs: ObservabilityBridge | None = None
    source: str = ""
    experiment_id: str = ""
    max_consecutive_errors: int = 3
    # Stale data load protocol — optimizer pipeline node sequence
    stale_data_load_protocol: list[str] | None = None
    search_memory: SearchMemory | None = None
    # Tag recorded alongside every score this scorer produces — forms the
    # key into the per-trace ``scored`` multi-scorer map. Derived from
    # ``campaign.json::scoring.id`` (or auto-hashed from the formula).
    scorer_id: str = "none"
    scorer_formula: str | None = None
    # Per-round scoring formula (composite). None = default formula.
    round_scorer: RoundScorer | None = None
    # Graceful-stop hook: scorer checks between queries; caller owns the flag source.
    stop_check: Callable[[], bool] | None = None

    @classmethod
    def for_loop(
        cls,
        backend_client: QueryRunner,
        store: Stores | None,
        backend_id: str,
        pipeline_schema: PipelineSchema | None,
        obs: ObservabilityBridge | None,
        experiment_id: str,
        cycle_id: str | None,
        *,
        max_consecutive_errors: int,
        stale_data_load_protocol: list[str] | None,
        scoring_formula: str | None,
        scoring_round_formula: str | None = None,
        scorer_id: str | None = None,
        source: str = "optimization_loop",
    ) -> ScoringEnv:
        """Build a ScoringEnv for an optimization-loop run.

        Owns the derived ``experiment_id`` fallback (short cycle-id prefix
        when not explicitly set) and the scoring-formula compilation so the
        caller just hands over raw config.
        """
        from promptpotter.shared.scoring import (
            auto_scorer_id,
            compile_round_scorer,
            compile_scorer,
        )

        derived_experiment_id = experiment_id or (
            cycle_id.replace("cycle_", "")[:12] if cycle_id else ""
        )
        round_scorer = (
            compile_round_scorer(scoring_round_formula) if scoring_round_formula else None
        )
        return cls(
            backend_client=backend_client,
            store=store,
            backend_id=backend_id,
            pipeline_schema=pipeline_schema,
            obs=obs,
            source=source,
            experiment_id=derived_experiment_id,
            max_consecutive_errors=max_consecutive_errors,
            stale_data_load_protocol=stale_data_load_protocol,
            scorer=compile_scorer(scoring_formula),
            scorer_id=scorer_id or auto_scorer_id(scoring_formula),
            scorer_formula=scoring_formula,
            round_scorer=round_scorer,
        )

    @classmethod
    def for_baseline(
        cls,
        backend_client: QueryRunner,
        store: Stores | None,
        backend_id: str,
        pipeline_schema: PipelineSchema | None,
        obs: ObservabilityBridge | None,
        *,
        scoring_formula: str | None,
        scoring_round_formula: str | None = None,
        scorer_id: str | None = None,
    ) -> ScoringEnv:
        """Build a ScoringEnv for baseline scoring (phase 0 of ``optimize``).

        Thin delegation to ``for_loop`` — keeps one source of truth for the
        scorer-trio compilation. The three hard-defaults below are the only
        baseline-specific divergences (baseline runs before ``cycle_id`` is
        bound and on a small scoring set, so it doesn't carry the loop's
        cycle-derived ``experiment_id`` or stale-data protocol).
        """
        return cls.for_loop(
            backend_client,
            store,
            backend_id,
            pipeline_schema,
            obs,
            experiment_id="",
            cycle_id=None,
            max_consecutive_errors=3,
            stale_data_load_protocol=None,
            scoring_formula=scoring_formula,
            scoring_round_formula=scoring_round_formula,
            scorer_id=scorer_id,
            source="baseline",
        )
