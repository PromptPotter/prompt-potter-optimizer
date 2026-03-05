"""
Project-based backend storage endpoints.

Manages backend connections, syncs experiments from backends in their
native format, and triggers pipeline replays via the backend API.
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.models.backend import BackendConnection, Execution, ExecutionResultItem
from api.services.backend_client import BackendClient
from api.services.comparison import compute_comparison
from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backends", tags=["Backends"])


def _get_store() -> ProjectStore:
    return ProjectStore()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RegisterBackendRequest(BaseModel):
    name: str = Field(..., description="Human-readable name")
    backend_type: str = Field(..., description="Backend type, e.g. 'termnorm'")
    base_url: str = Field(..., description="Backend API base URL")
    id: str | None = Field(
        None, description="Custom ID (auto-generated from name if omitted)",
    )


class RegisterBackendResponse(BaseModel):
    id: str
    name: str
    backend_type: str
    base_url: str
    created_at: str


class SyncResponse(BaseModel):
    backend_id: str
    experiments_synced: int
    synced_at: str


class ExecuteRequest(BaseModel):
    experiment_id: str = Field(..., description="Synced experiment to replay")
    variant_label: str = Field(
        default="",
        description="Human-readable variant label",
    )
    pipeline_notation: str = Field(
        default="",
        description="Pipeline notation string",
    )
    skip_llm_ranking: bool = Field(
        default=True,
        description="Whether to skip LLM ranking in the backend",
    )
    pipeline_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional pipeline parameters forwarded to the backend",
    )
    limit: int = Field(
        default=0,
        description="Limit number of queries (0 = all)",
    )
    delay_between: float = Field(
        default=2.0,
        description="Seconds between queries",
    )


class ExecuteResponse(BaseModel):
    execution_id: str
    backend_id: str
    experiment_id: str
    pipeline_params: dict[str, Any] = Field(default_factory=dict)
    query_count: int
    successful_count: int
    error_count: int
    created_at: str


class ComparisonResponse(BaseModel):
    execution_id: str
    pipeline: dict[str, Any]
    dataset: dict[str, Any]
    metrics: dict[str, Any]
    classification: dict[str, Any]
    limitations: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Convert a name to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _get_backend_or_404(backend_id: str, store: ProjectStore | None = None) -> BackendConnection:
    store = store or _get_store()
    backend = store.backends.get(backend_id)
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_id}' not found")
    return backend


# ---------------------------------------------------------------------------
# Backend CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=RegisterBackendResponse, status_code=201)
async def register_backend(request: RegisterBackendRequest):
    """Register a new backend connection."""
    store = _get_store()
    backend_id = request.id or _slugify(request.name)
    if store.backends.get(backend_id):
        raise HTTPException(
            status_code=409, detail=f"Backend '{backend_id}' already exists"
        )

    backend = BackendConnection(
        id=backend_id,
        name=request.name,
        backend_type=request.backend_type,
        base_url=request.base_url.rstrip("/"),
    )
    store.backends.register(backend)

    return RegisterBackendResponse(
        id=backend.id,
        name=backend.name,
        backend_type=backend.backend_type,
        base_url=backend.base_url,
        created_at=backend.created_at,
    )


@router.get("", response_model=list[RegisterBackendResponse])
async def list_backends():
    """List all registered backends."""
    store = _get_store()
    return [
        RegisterBackendResponse(
            id=b.id,
            name=b.name,
            backend_type=b.backend_type,
            base_url=b.base_url,
            created_at=b.created_at,
        )
        for b in store.backends.list_all()
    ]


@router.get("/{backend_id}", response_model=RegisterBackendResponse)
async def get_backend(backend_id: str):
    """Get backend details."""
    b = _get_backend_or_404(backend_id, _get_store())
    return RegisterBackendResponse(
        id=b.id,
        name=b.name,
        backend_type=b.backend_type,
        base_url=b.base_url,
        created_at=b.created_at,
    )


@router.delete("/{backend_id}", status_code=204)
async def delete_backend(backend_id: str):
    """Remove a backend and all its stored data."""
    store = _get_store()
    if not store.backends.delete(backend_id):
        raise HTTPException(status_code=404, detail=f"Backend '{backend_id}' not found")


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


@router.post("/{backend_id}/sync", response_model=SyncResponse)
async def sync_experiments(backend_id: str):
    """Sync experiments from backend API into project store (verbatim)."""
    store = _get_store()
    backend = _get_backend_or_404(backend_id, store)
    client = BackendClient(backend.base_url)

    try:
        count = await client.sync_experiments(store, backend_id)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to sync from {backend.base_url}: {e}",
        )

    now = datetime.now(timezone.utc).isoformat()
    backend.last_synced_at = now
    store.backends.update(backend)

    return SyncResponse(
        backend_id=backend_id,
        experiments_synced=count,
        synced_at=now,
    )


@router.get("/{backend_id}/experiments")
async def list_experiments(backend_id: str):
    """List synced experiments (from local store, native format)."""
    store = _get_store()
    _get_backend_or_404(backend_id, store)

    # First try the experiments list file
    data = store.backends.load_sync(backend_id, "experiments.json")
    if data:
        return data

    # Fall back to individual files
    experiments = store.backends.list_synced_experiments(backend_id)
    if not experiments:
        raise HTTPException(
            status_code=404,
            detail="No synced experiments. Run POST /sync first.",
        )
    return {"experiments": experiments}


@router.get("/{backend_id}/experiments/{experiment_id}")
async def get_experiment(backend_id: str, experiment_id: str):
    """Get a synced experiment in native backend format."""
    store = _get_store()
    _get_backend_or_404(backend_id, store)
    data = store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"Experiment '{experiment_id}' not synced. Run POST /sync first.",
        )
    return data


# ---------------------------------------------------------------------------
# Executions (pipeline replays)
# ---------------------------------------------------------------------------


@router.post("/{backend_id}/execute", response_model=ExecuteResponse)
async def execute_replay(backend_id: str, request: ExecuteRequest):
    """Trigger a pipeline replay via the backend API.

    Reads synced experiment data, extracts queries and session terms,
    calls the backend's /sessions and /matches endpoints, and stores
    results as an Execution.
    """
    store = _get_store()
    backend = _get_backend_or_404(backend_id, store)

    # Load synced experiment
    exp_data = store.backends.load_sync(
        backend_id, f"experiments/{request.experiment_id}.json"
    )
    if not exp_data:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Experiment '{request.experiment_id}' not synced. "
                "Run POST /sync first."
            ),
        )

    client = BackendClient(backend.base_url)
    terms = client.extract_session_terms(exp_data)
    queries = client.extract_replay_queries(exp_data)

    if not queries:
        raise HTTPException(
            status_code=400,
            detail="No valid queries with ground truth found in experiment data.",
        )

    if request.limit > 0:
        queries = queries[: request.limit]

    # Generate execution_id before replay so incremental writes use it
    execution_id = uuid.uuid4().hex[:12]

    def _on_result(result: dict, index: int, total: int) -> None:
        store.executions.append_result(backend_id, execution_id, result)

    # Run replay with incremental persistence
    try:
        results = await client.replay_queries(
            queries=queries,
            terms=terms,
            skip_llm_ranking=request.skip_llm_ranking,
            delay_between=request.delay_between,
            on_result=_on_result,
            pipeline_params=request.pipeline_params,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Replay failed after partial progress: {e}",
        )

    # Build and finalize execution
    successful = sum(1 for r in results if r.get("status") == "success")
    errors = sum(1 for r in results if r.get("status") == "error")

    execution = Execution(
        execution_id=execution_id,
        backend_id=backend_id,
        experiment_id=request.experiment_id,
        variant_label=request.variant_label,
        pipeline_notation=request.pipeline_notation,
        source_run_id=exp_data.get("runs", [{}])[0].get("run_id")
        if exp_data.get("runs")
        else None,
        session_terms_count=len(terms),
        pipeline_params=request.pipeline_params,
        limitations=[
            "Session terms pool is from synced experiment mappings"
            " (may be smaller than production DB)",
            "Web search results may differ from original run",
            "LLM1 entity profiling may produce different profiles (non-deterministic)",
        ],
        query_count=len(results),
        successful_count=successful,
        error_count=errors,
        results=[ExecutionResultItem(**r) for r in results],
    )
    store.executions.finalize(execution)

    return ExecuteResponse(
        execution_id=execution_id,
        backend_id=backend_id,
        experiment_id=request.experiment_id,
        pipeline_params=request.pipeline_params,
        query_count=execution.query_count,
        successful_count=successful,
        error_count=errors,
        created_at=execution.created_at,
    )


@router.get("/{backend_id}/executions")
async def list_executions(backend_id: str):
    """List all executions for a backend."""
    store = _get_store()
    _get_backend_or_404(backend_id, store)
    return store.executions.list_all(backend_id)


@router.get("/{backend_id}/executions/{execution_id}")
async def get_execution(backend_id: str, execution_id: str):
    """Get full execution results."""
    store = _get_store()
    _get_backend_or_404(backend_id, store)
    execution = store.executions.load(backend_id, execution_id)
    if not execution:
        raise HTTPException(
            status_code=404, detail=f"Execution '{execution_id}' not found"
        )
    return execution


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@router.get(
    "/{backend_id}/compare/{execution_id}", response_model=ComparisonResponse
)
async def compare_execution(backend_id: str, execution_id: str):
    """Compute statistical comparison for an execution."""
    store = _get_store()
    _get_backend_or_404(backend_id, store)
    execution = store.executions.load(backend_id, execution_id)
    if not execution:
        raise HTTPException(
            status_code=404, detail=f"Execution '{execution_id}' not found"
        )

    results = [r.model_dump() for r in execution.results]
    metadata = {
        "pipeline_notation": execution.pipeline_notation,
        "variant_label": execution.variant_label,
        "session_terms_count": execution.session_terms_count,
        "limitations": execution.limitations,
    }

    try:
        comparison = compute_comparison(results, metadata)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ComparisonResponse(
        execution_id=execution_id,
        **comparison,
    )
