"""Active-session pointer + read-only cycle list.

Carries the read-only surface (``GET /active``, ``GET /cycles``,
``GET /optimizer/pipeline``, ``GET /evaluators_meta``). Cycle mutations
ride the closed-set command highway at ``POST /api/v1/commands/{kind}``
(``docs/specs/m12-api-openapi.yaml`` for the wire contract;
``presentation/api/routers/commands.py`` for the dispatch shell).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from promptpotter.infrastructure.store import read_active_pointer
from promptpotter.presentation.api.deps import StoreDep

_active_router = APIRouter(tags=["Active"])

_OPTIMIZER_PIPELINE_PATH = (
    Path(__file__).resolve().parents[4] / "datasets" / "_optimizer" / "pipeline.json"
)


class ActiveSessionResponse(BaseModel):
    tenant_id: str = Field(description="Tenant the active session belongs to")
    session_id: str = Field(description="Active session id")
    campaign_id: str = Field(description="Active campaign id (pinned by the webapp)")
    cycle_id: str = Field(description="Active cycle id within the campaign")


@_active_router.get("/active", response_model=ActiveSessionResponse)
async def get_active_session(store: StoreDep) -> ActiveSessionResponse:
    """Return the caller-tenant's active-session pointer; 404 when none exists.

    Pointers are per-tenant on disk; the API never reads another tenant's
    state. Unauthed callers are rejected by ``resolve_identity`` before
    ``StoreDep`` resolves.
    """
    session_id, campaign_id, cycle_id = read_active_pointer(
        store.tenant_id, projects_root=store.base_dir.parent
    )
    if not session_id:
        raise HTTPException(404, "No active session")
    return ActiveSessionResponse(
        tenant_id=store.tenant_id,
        session_id=session_id,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
    )


class CycleListEntry(BaseModel):
    campaign_id: str = Field(description="Campaign the cycle belongs to")
    cycle_id: str
    parent_session_id: str = ""
    parent_cycle_id: str | None = Field(
        default=None,
        description="Immediate parent for siblings (forks/sweeps/diag); null for roots. Sidebar uses this to nest siblings.",
    )
    dataset_name: str = ""
    backend_id: str = ""
    sibling_kind: Literal["root", "fork", "diag", "sweep"]
    # Operator-facing unit kind — see _unit_kind() in campaign_store/cycles.py.
    unit_kind: Literal["session", "divergent_resume", "user_fork", "l3_fork"]
    is_root: bool
    status: str
    best_accuracy: float | None = None
    n_rounds: int = 0
    created_at: str = ""
    updated_at: str = ""


class CyclesResponse(BaseModel):
    tenant_id: str
    active_campaign_id: str | None = Field(
        description="Active campaign per active_session.json; null when no session is active."
    )
    active_cycle_id: str | None = Field(
        description="Active cycle per active_session.json; null when no session is active."
    )
    cycles: list[CycleListEntry]


@_active_router.get("/cycles", response_model=CyclesResponse)
async def get_cycles(store: StoreDep) -> CyclesResponse:
    """Every cycle on disk for the tenant + active pointer (one round-trip for the picker)."""
    _, active_cmp, active_cid = read_active_pointer(
        store.tenant_id, projects_root=store.base_dir.parent
    )
    entries = store.campaigns.enumerate_cycles()
    return CyclesResponse(
        tenant_id=store.tenant_id,
        active_campaign_id=active_cmp or None,
        active_cycle_id=active_cid or None,
        cycles=[CycleListEntry(**e) for e in entries],
    )


@_active_router.get("/optimizer/pipeline")
async def get_optimizer_pipeline() -> dict[str, Any]:
    """Bundled ``optimizer_pipeline.json`` — nodes + pipelines + ``view`` topology for the webapp workflow."""
    pipeline: dict[str, Any] = json.loads(_OPTIMIZER_PIPELINE_PATH.read_text(encoding="utf-8"))
    return pipeline


@_active_router.get("/evaluators_meta")
async def get_evaluators_meta() -> list[dict[str, Any]]:
    """Import-time evaluator registry (name/description/scope/direction/node_type) — feeds the What-If panel even when ``dashboard.json`` lacks ``cycle_info``."""
    from promptpotter.application.scoring.evaluators import evaluators_meta

    return evaluators_meta()


__all__ = [
    "ActiveSessionResponse",
    "CycleListEntry",
    "CyclesResponse",
]
