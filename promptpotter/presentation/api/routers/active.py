"""Active-session pointer + read-only cycle list + optimizer/registry reads.

Carries the tenant-global read-only surface: the active session
(``GET /sessions/active``), the global cycle list (``GET /cycles``), and the
optimizer-pipeline read (``GET /optimizer-pipeline``). The evaluator registry is
NOT served here — it is import-time constant, so it reaches the webapp through
``scripts/build_ts_types.py`` (CI-gated) rather than a route.
Live telemetry is NOT served here — a cycle's ``dashboard.json`` is served by
the per-cycle dashboard route, which the operator reaches by resolving the
active pointer above. Routes are tagged per-resource for OpenAPI grouping since
this router spans several resources. Cycle mutations ride the closed-set command
highway at ``POST /api/v1/commands/{kind}`` (``docs/specs/m12-api-openapi.yaml``
for the wire contract; ``presentation/api/routers/commands.py`` for the dispatch
shell).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    OPTIMIZER_PIPELINE_PATH,
)
from promptpotter.infrastructure.store import read_active_pointer
from promptpotter.infrastructure.store.io import read_json
from promptpotter.presentation.api.deps import (
    IdentityDep,
    JobRegistryDep,
    StoreDep,
)
from promptpotter.shared.errors import NotFoundError

active_router = APIRouter()


class ActiveSessionResponse(BaseModel):
    tenant_id: str = Field(description="Tenant the active session belongs to")
    session_id: str = Field(description="Active session id")
    campaign_id: str = Field(description="Active campaign id (pinned by the webapp)")
    cycle_id: str = Field(description="Active cycle id within the campaign")


@active_router.get("/sessions/active", response_model=ActiveSessionResponse, tags=["Sessions"])
def get_active_session(store: StoreDep) -> ActiveSessionResponse:
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
    unit_kind: Literal["session", "divergent_resume", "user_fork", "auto_rebase"]
    is_root: bool
    # Precise terminal reason (StopReason value) once finished, else "active"
    # ("unreadable" for a malformed index). The display label + outcome derive
    # from the one STOP_REASON_INFO table; do not re-map per surface.
    status: str
    run_phase: Literal["checkin", "running", "paused", "detached", "terminal"] = Field(
        default="detached",
        description="The single run-state value (RunPhase) — checkin (origin still being authored, pre-loop) / running / paused / detached / terminal. Computed once by derive_run_phase from lifecycle + control flags + freshness; every picker dot and badge reads this, none re-derive it. 'checkin' wins first (the campaign hasn't run); 'terminal' pairs with `status` for the reason label.",
    )
    best_accuracy: float | None = None
    origin_accuracy: float | None = Field(
        default=None,
        description="Round 0's accuracy — the origin's measurement, derived from rounds[] (no stored copy). Null until round 0 lands.",
    )
    n_rounds: int = 0
    created_at: str = ""
    updated_at: str = ""
    human_intervened: bool = Field(
        default=False,
        description="True once an operator manually intervened (e.g. skip-searchpoint); the cycle is babysat and no longer purely reproducible. Drives the 'babysat' badge; orthogonal to run_phase.",
    )


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
def get_cycles(store: StoreDep) -> CyclesResponse:
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


class MachineHolder(BaseModel):
    user: str = Field(description="user_id of the operator whose run owns the slot")
    campaign_id: str
    cycle_id: str
    started_at: str | None = Field(
        default=None, description="ISO start time of the holding run; null if still pending"
    )


class MachineStatusResponse(BaseModel):
    busy: bool = Field(
        description="True iff a *different* user holds a running job (the server runs one campaign at a time)."
    )
    holder: MachineHolder | None = Field(
        default=None, description="Who holds the slot; null when free for this caller."
    )


@active_router.get("/machine-status", response_model=MachineStatusResponse, tags=["Sessions"])
async def get_machine_status(identity: IdentityDep, jobs: JobRegistryDep) -> MachineStatusResponse:
    """Whether another user is currently running a campaign on this machine.

    The server is single-process and runs campaigns in sequence, so a launch
    is rejected (409 ``machine_busy``) while anyone else's run is live. This
    read is the poll that lets the webapp surface that state as a critical-alert
    banner *before* the operator tries — the always-on twin of the 409. Reads
    the same :meth:`JobRegistry.machine_holder` the launch gate uses, so banner
    and gate never disagree. Cross-user holder info is intentionally exposed
    (the seed of an admin presence view).
    """
    holder = jobs.machine_holder(exclude_user_id=str(identity.user_id))
    if holder is None:
        return MachineStatusResponse(busy=False)
    return MachineStatusResponse(
        busy=True,
        holder=MachineHolder(
            user=holder.user_id,
            campaign_id=holder.campaign_id,
            cycle_id=holder.cycle_id,
            started_at=holder.started_at,
        ),
    )


@active_router.get("/optimizer-pipeline", tags=["Optimizer"])
def get_optimizer_pipeline() -> dict[str, Any]:
    """Bundled ``datasets/_optimizer/pipeline.json`` — nodes + pipelines + ``view`` topology for the webapp workflow."""
    pipeline: dict[str, Any] = read_json(OPTIMIZER_PIPELINE_PATH)
    return pipeline


__all__ = [
    "ActiveSessionResponse",
    "CycleListEntry",
    "CyclesResponse",
    "MachineHolder",
    "MachineStatusResponse",
]
