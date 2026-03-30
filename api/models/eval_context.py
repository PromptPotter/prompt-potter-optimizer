"""Evaluation context — infrastructure bundle for eval calls."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema
    from api.services.backend_client import BackendClient
    from api.services.campaign.escalation import DegradationCheck
    from api.services.obs.observability_logger import ObsLogger
    from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


@dataclass
class EvalContext:
    """Infrastructure bundle shared across evaluation calls.

    Pure infrastructure — does NOT carry search-space dimensions
    (pipeline_params).  Those live on the SearchPoint passed alongside.
    """

    backend_client: BackendClient
    store: ProjectStore | None = None
    backend_id: str = ""
    pipeline_schema: PipelineSchema | None = None
    obs: ObsLogger | None = None
    source: str = ""
    experiment_id: str = ""
    # Escalation checks (passed through to _run_eval_batch)
    escalation_checks: list[DegradationCheck] | None = None
    # Mutated per-candidate in l1_evaluate loop
    candidate_idx: int = 0
    n_total_candidates: int = 1
    max_consecutive_errors: int = 3
