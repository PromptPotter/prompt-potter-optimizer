from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import Field

from promptpotter.application.cycle_addressing import active_pointer
from promptpotter.application.jobs.machine_status import MachineStatusResponse, machine_status
from promptpotter.application.library import LibraryResponse, library
from promptpotter.application.pipeline_resolve import (
    OptimizerPipelineResponse,
    OptimizerRoster,
    optimizer_roster,
    resolve_pipeline_for_optimizer,
)
from promptpotter.domain.campaign_config import OptimizationConfig
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import (
    IdentityDep,
    JobRegistryDep,
    StoresDep,
)

active_router = APIRouter()


class ActiveSessionResponse(StrictModel):
    """The tenant's latest launch — not the set of live runs, which is `state.attached` on `/hierarchy`."""

    tenant_id: str = Field(
        description="Tenant the pointer belongs to — the caller's own, always known"
    )
    campaign_id: str | None = Field(
        description="Campaign of the latest launch; null when no session is active."
    )
    cycle_id: str | None = Field(
        description="Cycle of the latest launch; null when no session is active."
    )


@active_router.get("/sessions/active", response_model=ActiveSessionResponse, tags=["Sessions"])
def get_active_session(stores: StoresDep) -> ActiveSessionResponse:
    """The caller-tenant's active-session pointer: its latest launch from any entry point.

    No active session is a steady state: it answers 200 with null ids, and this route has no 404.
    """
    pointer = active_pointer(stores)
    return ActiveSessionResponse(
        tenant_id=stores.tenant_id,
        campaign_id=pointer.campaign_id,
        cycle_id=pointer.cycle_id,
    )


@active_router.get("/machine-status", response_model=MachineStatusResponse, tags=["Sessions"])
def get_machine_status(identity: IdentityDep, jobs: JobRegistryDep) -> MachineStatusResponse:
    """Machine occupancy: how many campaigns may run here and how many do, and the slots the caller may see."""
    # Sync, so the blocking read stays off the event loop.
    return machine_status(jobs, identity=identity)


@active_router.get(
    "/optimizer-pipeline", tags=["Optimizer"], response_model=OptimizerPipelineResponse
)
def get_optimizer_pipeline(
    stores: StoresDep,
    optimizer: str = Query(
        default=OptimizationConfig.model_fields["optimizer"].default,
        description="The registered optimizer whose manifest to read",
    ),
) -> OptimizerPipelineResponse:
    """One optimizer manifest: its ``view`` topology plus the per-node typed config surface, read-only."""
    return resolve_pipeline_for_optimizer(stores, optimizer)


@active_router.get("/optimizers", tags=["Optimizer"], response_model=OptimizerRoster)
def get_optimizers() -> OptimizerRoster:
    """The optimizers this install can run, one per registered manifest: what ``optimization.optimizer`` accepts."""
    return optimizer_roster()


@active_router.get("/library", tags=["Optimizer"], response_model=LibraryResponse)
def get_library(stores: StoresDep) -> LibraryResponse:
    """Every snippet the caller's library holds, the install's packs beside its own, for a target or an optimizer prompt alike."""
    return library(stores.block_packs)
