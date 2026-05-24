"""Cross-campaign measurement leverage — read-only aggregation over the archive.

Single endpoint backing the webapp's leverage panel. Walks the tenant's
``archive/measurements_index.json`` + per-run detail files, groups items by
query, and returns per-query stats so the operator can see how much
measurement reuse has accumulated across every campaign on the install.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from promptpotter.infrastructure.store.archive_views import aggregate_per_query
from promptpotter.presentation.api.deps import StoreDep

_measurements_router = APIRouter(tags=["Measurements"])


class PerQueryRow(BaseModel):
    query: str
    sample_id: int
    n_measurements: int
    n_unique_configs: int
    mean_fitness: float | None
    hit_rate: float
    last_seen: str


class LeverageResponse(BaseModel):
    n_runs: int
    n_measurements: int
    n_unique_queries: int
    per_query: list[PerQueryRow]


@_measurements_router.get("/measurements/leverage", response_model=LeverageResponse)
async def get_measurement_leverage(
    store: StoreDep,
    limit: int = Query(default=200, ge=1, le=5000),
) -> LeverageResponse:
    """Aggregate every measurement on the install into per-query stats.

    Output sorted by ``n_measurements`` desc; top *limit* rows returned.
    The archive is tenant-scoped — ``backend_id`` is required by the
    facade signature but ignored for path construction, so the aggregate
    covers every campaign on the install regardless of which backend wrote it.
    """
    agg: dict[str, Any] = aggregate_per_query(store, backend_id="")
    rows: list[dict[str, Any]] = agg["per_query"][:limit]
    return LeverageResponse(
        n_runs=agg["n_runs"],
        n_measurements=agg["n_measurements"],
        n_unique_queries=agg["n_unique_queries"],
        per_query=[PerQueryRow(**r) for r in rows],
    )


__all__ = ["LeverageResponse", "PerQueryRow", "_measurements_router"]
