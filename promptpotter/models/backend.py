"""
Pydantic models for project-based backend storage.

BackendConnection represents a connected backend (e.g. TermNorm instance).
Execution captures results from pipeline replays triggered by PromptPotter.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class BackendConnection(BaseModel):
    """A registered backend connection (e.g. TermNorm instance)."""

    id: str = Field(..., description="Unique backend ID, e.g. 'local'")
    name: str = Field(..., description="Human-readable name")
    backend_type: str = Field(..., description="Backend type, e.g. 'termnorm'")
    base_url: str = Field(..., description="Backend API base URL")
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    last_synced_at: str | None = None


class ExecutionResultItem(BaseModel):
    """One query's result from a pipeline execution replay."""

    query: str
    bom_material: str | None = None
    process: str | None = None
    query_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Generic query metadata. Mirrors bom_material/process for "
        "TermNorm; other connectors populate with their own keys.",
    )
    ground_truth: str
    predicted: str
    confidence: float = 0.0
    ranked_candidates: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float = 0.0
    web_search_status: str | None = None
    pipeline_data: dict[str, Any] = Field(default_factory=dict)
    status: str = "success"
    error: str | None = None
    timestamp: str | None = None


class Execution(BaseModel):
    """A pipeline execution replay triggered by PromptPotter."""

    execution_id: str
    backend_id: str
    experiment_id: str
    variant_label: str = ""
    pipeline_notation: str = ""
    source_run_id: str | None = None
    index_terms_count: int | None = None
    limitations: list[str] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    pipeline_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Pipeline parameter overrides forwarded to the backend",
    )
    query_count: int = 0
    successful_count: int = 0
    error_count: int = 0
    results: list[ExecutionResultItem] = Field(default_factory=list)
