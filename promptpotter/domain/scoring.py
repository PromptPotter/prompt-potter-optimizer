"""Scoring models — query results, infrastructure bundle, and ground truth comparison.

``QueryResult`` / ``QueryResultFull`` define the per-query measurement schema.
``ScoringEnv`` bundles infrastructure for dataset scoring calls.
``ExactMatchComparator`` compares pipeline output to expected answers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

from pydantic import BaseModel, Field

from promptpotter.shared.scoring import Scorer

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
    """Core per-query evaluation result — always present."""

    query: str
    ground_truth: str
    predicted: str
    hit: bool
    score: float
    error: str | None
    pipeline_data: PipelineData | None


class QueryResultFull(QueryResult, total=False):
    """Extended result with optional fields from eval pipeline and stale-data protocol."""

    # Eval pipeline
    n_candidates: int
    ground_truth_rank: int | None
    precomputed_through: list[str]
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

    Satisfied by both ``BackendClient`` (multi-node pipelines) and
    ``LLMOnlyAdapter`` (single-call LLM datasets).  Aligns with the
    planned M11 ``ConnectorProtocol``.
    """

    async def run_query(
        self,
        query: str,
        pipeline_params: dict[str, Any] | None = ...,
        precomputed: dict[str, Any] | None = ...,
    ) -> dict[str, Any]: ...

    async def check_status(self) -> dict[str, Any]: ...

    async def fetch_pipeline(self) -> dict[str, Any]: ...

    async def init_session(self, terms: list[str]) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


if TYPE_CHECKING:
    from promptpotter.application.search.search_memory import SearchMemory
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store.project_store import ProjectStore
    from promptpotter.infrastructure.tracing.observability_logger import ObsLogger


@dataclass
class ScoringEnv:
    """Infrastructure bundle shared across scoring and measurement calls.

    Pure infrastructure — does NOT carry search-space dimensions
    (pipeline_params).  Those live on the SearchPoint passed alongside.
    Per-candidate state (candidate_idx, degradation_checks) is passed
    explicitly to score_search_point(), not stored here.
    """

    backend_client: QueryRunner
    store: ProjectStore | None = None
    backend_id: str = ""
    pipeline_schema: PipelineSchema | None = None
    obs: ObsLogger | None = None
    source: str = ""
    experiment_id: str = ""
    max_consecutive_errors: int = 3
    # Stale data load protocol — optimizer pipeline node sequence
    stale_data_load_protocol: list[str] | None = None
    search_memory: SearchMemory | None = None
    # Per-dataset scoring formula (compiled callable from shared/scoring.py)
    scorer: Scorer | None = None
    # Graceful-stop hook: scorer checks between queries; caller owns the flag source.
    stop_check: Callable[[], bool] | None = None

    @classmethod
    def for_loop(
        cls,
        backend_client: QueryRunner,
        store: ProjectStore | None,
        backend_id: str,
        pipeline_schema: PipelineSchema | None,
        obs: ObsLogger | None,
        experiment_id: str,
        cycle_id: str | None,
        *,
        max_consecutive_errors: int,
        stale_data_load_protocol: list[str] | None,
        scoring_formula: str | None,
    ) -> ScoringEnv:
        """Build a ScoringEnv for an optimization-loop run.

        Owns the derived ``experiment_id`` fallback (short cycle-id prefix
        when not explicitly set) and the scoring-formula compilation so the
        caller just hands over raw config.
        """
        from promptpotter.shared.scoring import compile_scorer

        derived_experiment_id = experiment_id or (
            cycle_id.replace("cycle_", "")[:12] if cycle_id else ""
        )
        return cls(
            backend_client=backend_client,
            store=store,
            backend_id=backend_id,
            pipeline_schema=pipeline_schema,
            obs=obs,
            source="optimization_loop",
            experiment_id=derived_experiment_id,
            max_consecutive_errors=max_consecutive_errors,
            stale_data_load_protocol=stale_data_load_protocol,
            scorer=compile_scorer(scoring_formula),
        )


# ---------------------------------------------------------------------------
# Ground truth comparison
# ---------------------------------------------------------------------------


class GroundTruthResult(StrEnum):
    """Single-sample comparison outcome."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class GroundTruthOutput(BaseModel):
    """Result of comparing pipeline output to ground truth."""

    result: GroundTruthResult = Field(..., description="pass, fail, or error")
    score: float = Field(
        ..., ge=0.0, le=1.0, description="Comparison score (0.0 = mismatch, 1.0 = match)"
    )
    expected: Any = Field(..., description="Expected value (ground truth)")
    actual: Any = Field(..., description="Actual value from pipeline")


class ExactMatchComparator:
    """
    Compares expected and actual for exact equality.

    Config options:
        strip: Strip leading/trailing whitespace (default: True)
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.strip = self.config.get("strip", True)

    def _normalize(self, value: Any) -> Any:
        """Normalize a value for comparison."""
        if isinstance(value, str) and self.strip:
            return value.strip()
        return value

    def compare(self, expected: Any, actual: Any) -> GroundTruthOutput:
        """Compare expected and actual for exact match."""
        if expected is None and actual is None:
            return GroundTruthOutput(
                result=GroundTruthResult.PASS,
                score=1.0,
                expected=expected,
                actual=actual,
            )

        if expected is None or actual is None:
            return GroundTruthOutput(
                result=GroundTruthResult.FAIL,
                score=0.0,
                expected=expected,
                actual=actual,
            )

        norm_expected = self._normalize(expected)
        norm_actual = self._normalize(actual)

        if norm_expected == norm_actual:
            return GroundTruthOutput(
                result=GroundTruthResult.PASS,
                score=1.0,
                expected=expected,
                actual=actual,
            )
        return GroundTruthOutput(
            result=GroundTruthResult.FAIL,
            score=0.0,
            expected=expected,
            actual=actual,
        )
