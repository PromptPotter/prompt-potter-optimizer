from __future__ import annotations

from fastapi import APIRouter

from promptpotter.application.cycle_addressing import ActiveSessionResponse, active_session
from promptpotter.application.jobs.machine_status import MachineStatusResponse, machine_status
from promptpotter.application.library import LibraryResponse, library
from promptpotter.application.pipeline_responses import OptimizerRoster, optimizer_roster
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import (
    IdentityDep,
    JobRegistryDep,
    StoresDep,
)

active_router = APIRouter()


@active_router.get(
    "/sessions/active",
    response_model=ActiveSessionResponse,
    tags=["Sessions"],
    name=ReadKind.ACTIVE_SESSION,
)
def get_active_session(stores: StoresDep) -> ActiveSessionResponse:
    """The caller-tenant's active-session pointer: its latest launch from any entry point.

    No active session is a steady state: it answers 200 with null ids, and this route has no 404.
    """
    return active_session(stores)


@active_router.get(
    "/machine-status",
    response_model=MachineStatusResponse,
    tags=["Sessions"],
    name=ReadKind.MACHINE_STATUS,
)
def get_machine_status(identity: IdentityDep, jobs: JobRegistryDep) -> MachineStatusResponse:
    """Machine occupancy: how many campaigns may run here and how many do, and the slots the caller may see."""
    # Sync, so the blocking read stays off the event loop.
    return machine_status(jobs, identity=identity)


@active_router.get(
    "/optimizers", tags=["Optimizer"], response_model=OptimizerRoster, name=ReadKind.OPTIMIZERS
)
def get_optimizers() -> OptimizerRoster:
    """The optimizers this install can run, one per registered manifest: what ``optimization.optimizer`` accepts."""
    return optimizer_roster()


@active_router.get(
    "/library", tags=["Optimizer"], response_model=LibraryResponse, name=ReadKind.LIBRARY
)
def get_library(stores: StoresDep) -> LibraryResponse:
    """Every snippet the caller's library holds, the install's packs beside its own, for a target or an optimizer prompt alike."""
    return library(stores.block_packs)
