from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter

from promptpotter.application.cycle_addressing import ActiveSessionResponse
from promptpotter.application.jobs.machine_status import MachineStatusResponse
from promptpotter.application.library import LibraryResponse
from promptpotter.application.pipeline_responses import OptimizerRoster
from promptpotter.application.read_params import (
    ActiveSessionParams,
    LibraryParams,
    MachineStatusParams,
    OptimizersParams,
)
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import CallerDep, read_input

active_router = APIRouter()


@active_router.get(
    "/sessions/active",
    response_model=ActiveSessionResponse,
    tags=["Sessions"],
    name=ReadKind.ACTIVE_SESSION,
)
def get_active_session(
    caller: CallerDep, read: Annotated[ActiveSessionParams, read_input(ActiveSessionParams)]
) -> ActiveSessionResponse:
    return read.answer(caller)


@active_router.get(
    "/machine-status",
    response_model=MachineStatusResponse,
    tags=["Sessions"],
    name=ReadKind.MACHINE_STATUS,
)
def get_machine_status(
    caller: CallerDep, read: Annotated[MachineStatusParams, read_input(MachineStatusParams)]
) -> MachineStatusResponse:
    return read.answer(caller)


@active_router.get(
    "/optimizers", tags=["Optimizer"], response_model=OptimizerRoster, name=ReadKind.OPTIMIZERS
)
def get_optimizers(
    caller: CallerDep, read: Annotated[OptimizersParams, read_input(OptimizersParams)]
) -> OptimizerRoster:
    return read.answer(caller)


@active_router.get(
    "/library", tags=["Optimizer"], response_model=LibraryResponse, name=ReadKind.LIBRARY
)
def get_library(
    caller: CallerDep, read: Annotated[LibraryParams, read_input(LibraryParams)]
) -> LibraryResponse:
    return read.answer(caller)
