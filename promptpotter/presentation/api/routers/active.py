"""Active-session pointer + read-only cycle list + optimizer/registry reads.

Carries the tenant-global read-only surface: the active session
(``GET /sessions/active``, ``GET /sessions/active/live-state``), the global
cycle list (``GET /cycles``), and two registry reads
(``GET /optimizer-pipeline``, ``GET /evaluators``).
Routes are tagged per-resource for OpenAPI grouping since this router spans
several resources. Cycle mutations ride the closed-set command highway at
``POST /api/v1/commands/{kind}`` (``docs/specs/m12-api-openapi.yaml`` for the
wire contract; ``presentation/api/routers/commands.py`` for the dispatch shell).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from promptpotter.infrastructure.runtime_flags import is_paused, read_spend_cap
from promptpotter.infrastructure.store import (
    cycle_dir_for,
    read_active_pointer,
)
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.shared.errors import NotFoundError

active_router = APIRouter()

_OPTIMIZER_PIPELINE_PATH = (
    Path(__file__).resolve().parents[4] / "datasets" / "_optimizer" / "pipeline.json"
)


class ActiveSessionResponse(BaseModel):
    tenant_id: str = Field(description="Tenant the active session belongs to")
    session_id: str = Field(description="Active session id")
    campaign_id: str = Field(description="Active campaign id (pinned by the webapp)")
    cycle_id: str = Field(description="Active cycle id within the campaign")


@active_router.get("/sessions/active", response_model=ActiveSessionResponse, tags=["Sessions"])
async def get_active_session(store: StoreDep) -> ActiveSessionResponse:
    """Return the caller-tenant's active-session pointer; 404 when none exists.

    Pointers are per-tenant on disk; the API never reads another tenant's
    state. Unauthed callers are rejected by ``resolve_identity`` before
    ``StoreDep`` resolves.
    """
    session_id, campaign_id, cycle_id = read_active_pointer(
        store.tenant_id, projects_root=store.projects_root
    )
    if not session_id:
        raise NotFoundError("No active session")
    return ActiveSessionResponse(
        tenant_id=store.tenant_id,
        session_id=session_id,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
    )


@active_router.get("/sessions/active/live-state", tags=["Sessions"])
async def get_live_state(store: StoreDep) -> dict[str, Any]:
    """Live state of the caller-tenant's **active** session — the stable
    surface every new web panel / chat state-read codes against.

    Resolves the active pointer, then returns the active cycle's own live
    telemetry (the same shape the per-cycle dashboard route serves). Each
    cycle owns its ``dashboard.json``, so a fork's active view shows the
    fork's trajectory under the fork's id — not the session root's. Keying
    on the active pointer means a consumer needs no campaign/cycle ids and no
    file path; it is insulated from how that telemetry is produced.

    404 when no session is active. While a fresh campaign's origin is still
    running (no telemetry flushed yet) returns a ``warming_up`` payload at
    200 so the panel can render an "initialising" placeholder rather than
    treat the session as offline.

    Beyond the dashboard telemetry, the payload carries two runtime facts the
    dashboard projection does not track (they are control-flow flags, not
    ledger events): ``is_paused`` (``.runtime/pause.flag`` present on the
    active cycle) and ``current_spend_cap_usd`` (the live cap from
    ``.runtime/spend_cap.json``, or ``null`` when uncapped). The run controls
    code against these rather than guessing pause-state from telemetry
    freshness — a paused runner emits no events, so liveness alone is blind.
    """
    session_id, campaign_id, cycle_id = read_active_pointer(
        store.tenant_id, projects_root=store.projects_root
    )
    if not session_id:
        raise NotFoundError("No active session")
    # Both the dashboard.json and the runtime flags live on the active cycle
    # dir (which may be a fork): each cycle owns its own live file, and the
    # runner writes + polls its flags there. Shared readers in
    # ``infrastructure.runtime_flags`` so this matches the per-cycle dashboard route.
    cycle_path = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    runtime_dir = cycle_path / ".runtime"
    paused = is_paused(runtime_dir)
    current_spend_cap_usd = read_spend_cap(runtime_dir)

    path = cycle_path / "dashboard.json"
    if not path.is_file():
        return {
            "warming_up": True,
            "session_id": session_id,
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
            "phase_hint": "origin",
            "is_paused": paused,
            "current_spend_cap_usd": current_spend_cap_usd,
        }
    state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    state["is_paused"] = paused
    state["current_spend_cap_usd"] = current_spend_cap_usd
    return state


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
    # Precise terminal reason (StopReason value) once finished, else "active"
    # ("unreadable" for a malformed index). The display label + outcome derive
    # from the one STOP_REASON_INFO table; do not re-map per surface.
    status: str
    run_phase: Literal["running", "paused", "stopping", "detached", "terminal"] = Field(
        default="detached",
        description="The single run-state value (RunPhase) — running / paused / stopping / detached / terminal. Computed once by derive_run_phase from lifecycle + control flags + freshness; every picker dot and badge reads this, none re-derive it. 'terminal' pairs with `status` for the reason label.",
    )
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


@active_router.get("/cycles", response_model=CyclesResponse, tags=["Cycles"])
async def get_cycles(store: StoreDep) -> CyclesResponse:
    """Every cycle on disk for the tenant + active pointer (one round-trip for the picker)."""
    _, active_cmp, active_cid = read_active_pointer(
        store.tenant_id, projects_root=store.projects_root
    )
    entries = store.campaigns.enumerate_cycles()
    return CyclesResponse(
        tenant_id=store.tenant_id,
        active_campaign_id=active_cmp or None,
        active_cycle_id=active_cid or None,
        cycles=[CycleListEntry(**e) for e in entries],
    )


@active_router.get("/optimizer-pipeline", tags=["Optimizer"])
async def get_optimizer_pipeline() -> dict[str, Any]:
    """Bundled ``optimizer_pipeline.json`` — nodes + pipelines + ``view`` topology for the webapp workflow."""
    pipeline: dict[str, Any] = json.loads(_OPTIMIZER_PIPELINE_PATH.read_text(encoding="utf-8"))
    return pipeline


@active_router.get("/evaluators", tags=["Evaluators"])
async def get_evaluators_meta() -> list[dict[str, Any]]:
    """Import-time evaluator registry (name/description/scope/direction/node_type) — feeds the What-If panel even when ``dashboard.json`` lacks ``cycle_info``."""
    from promptpotter.application.scoring.evaluators import evaluators_meta

    return evaluators_meta()


__all__ = [
    "ActiveSessionResponse",
    "CycleListEntry",
    "CyclesResponse",
]
