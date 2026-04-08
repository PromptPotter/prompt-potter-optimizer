"""Evaluation context — infrastructure bundle for eval calls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.backend_client import BackendClient
    from promptpotter.services.project_store import ProjectStore
    from promptpotter.services.search.search_memory import SearchMemory
    from promptpotter.services.tracing.observability_logger import ObsLogger


@dataclass
class EvalContext:
    """Infrastructure bundle shared across evaluation calls.

    Pure infrastructure — does NOT carry search-space dimensions
    (pipeline_params).  Those live on the SearchPoint passed alongside.
    Per-candidate state (candidate_idx, degradation_checks) is passed
    explicitly to eval_search_point(), not stored here.
    """

    backend_client: BackendClient
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
