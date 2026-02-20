"""
Pydantic models for project-based backend storage.

BackendConnection represents a connected backend (e.g. TermNorm instance).
Execution captures results from pipeline replays triggered by PromptPotter.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BackendConnection(BaseModel):
    """A registered backend connection (e.g. TermNorm instance)."""

    id: str = Field(..., description="Unique backend ID, e.g. 'termnorm-local'")
    name: str = Field(..., description="Human-readable name")
    backend_type: str = Field(..., description="Backend type, e.g. 'termnorm'")
    base_url: str = Field(..., description="Backend API base URL")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_synced_at: Optional[str] = None


class ExecutionResultItem(BaseModel):
    """One query's result from a pipeline execution replay."""

    query: str
    bom_material: Optional[str] = None
    process: Optional[str] = None
    ground_truth: str
    predicted: str
    confidence: float = 0.0
    ranked_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    latency_ms: float = 0.0
    web_search_status: Optional[str] = None
    status: str = "success"
    error: Optional[str] = None
    timestamp: Optional[str] = None
    # Original variant data for comparison
    variant_b_predicted: Optional[str] = None
    variant_b_latency_ms: Optional[float] = None
    variant_b_confidence: Optional[float] = None


class Execution(BaseModel):
    """A pipeline execution replay triggered by PromptPotter."""

    execution_id: str
    backend_id: str
    experiment_id: str
    variant_label: str = ""
    pipeline_notation: str = ""
    source_run_id: Optional[str] = None
    session_terms_count: Optional[int] = None
    limitations: List[str] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    query_count: int = 0
    successful_count: int = 0
    error_count: int = 0
    results: List[ExecutionResultItem] = Field(default_factory=list)
