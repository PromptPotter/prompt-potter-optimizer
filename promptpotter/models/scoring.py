"""Scoring models — infrastructure bundle and ground truth comparison.

``ScoringEnv`` bundles infrastructure for dataset scoring calls.
``ExactMatchComparator`` compares pipeline output to expected answers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


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
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.project_store import ProjectStore
    from promptpotter.services.search.search_memory import SearchMemory
    from promptpotter.services.tracing.observability_logger import ObsLogger


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
    # Mutable dict extracted from opt_sp.stale_data_observations — updated during eval
    stale_data_observations: dict[str, int | dict] | None = None
    # Per-dataset scoring formula (compiled callable from shared/scoring.py)
    scorer: Callable[[dict], float] | None = None


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
