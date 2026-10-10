from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response
from pydantic import Field

from promptpotter.application.campaign_config import OptimizationConfig
from promptpotter.application.cycle_listing import CyclesResponse, active_pointer, list_cycles
from promptpotter.application.jobs.capacity import MachineStatusResponse, machine_status
from promptpotter.application.optimizer_manifest import (
    OptimizerKnobsResponse,
    OptimizerRoster,
    optimizer_knobs,
    optimizer_roster,
)
from promptpotter.application.pipeline_resolve import (
    OptimizerPipelineResponse,
    resolve_pipeline_for_optimizer,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import (
    IdentityDep,
    JobRegistryDep,
    StoresDep,
    decode_descend,
)
from promptpotter.presentation.api.routers.campaigns._conditional import conditional_json
from promptpotter.shared.identity import acting_principal_id

active_router = APIRouter()


class ActiveSessionResponse(StrictModel):
    """The tenant's latest launch — not the set of live runs, which is `run_phase` on `/cycles`."""

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


@active_router.get("/cycles", response_model=CyclesResponse, tags=["Cycles"])
def get_cycles(
    request: Request,
    stores: StoresDep,
    descend: str | None = Query(None),
    campaign: str | None = Query(None),
    attached: bool = Query(False),
) -> Response:
    """Every cycle in one store plus that store's active pointer.

    ``descend`` is the ``~``-joined ``campaign::cycle`` chain of ``.inner/`` sandboxes to enter; absent is the tenant's own tree.
    The pointer is raw: whether the pointed cycle is live is its entry's ``run_phase``.
    """
    return conditional_json(
        request,
        list_cycles(
            stores, inside=decode_descend(descend), campaign_id=campaign, attached_only=attached
        ),
    )


@active_router.get("/machine-status", response_model=MachineStatusResponse, tags=["Sessions"])
def get_machine_status(identity: IdentityDep, jobs: JobRegistryDep) -> MachineStatusResponse:
    """Machine occupancy: how many campaigns may run here and how many do, holders across users included."""
    # `identity` is what 401s an unauthenticated read of cross-user holders; sync, so the blocking read stays off the event loop.
    return machine_status(jobs, principal_id=acting_principal_id(identity))


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
    """The optimizers this install can run, one per registered runtime: what ``optimization.optimizer`` accepts."""
    return optimizer_roster()


@active_router.get(
    "/optimizers/{name}/knobs", tags=["Optimizer"], response_model=OptimizerKnobsResponse
)
def get_optimizer_knobs(name: str) -> OptimizerKnobsResponse:
    """Every knob the manifest's nodes take, each with its type, closed options, bounds and declared value.

    404 when no such optimizer is registered.
    """
    return optimizer_knobs(name)


__all__ = ["ActiveSessionResponse"]
