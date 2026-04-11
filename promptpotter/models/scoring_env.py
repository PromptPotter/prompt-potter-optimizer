"""Scoring environment — infrastructure bundle for dataset scoring calls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


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
