from __future__ import annotations

from typing import Annotated

from promptpotter.application.cycle_files import FileContentResponse, FilesResponse
from promptpotter.application.read_params import CycleFileParams, CycleFilesParams
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import CallerDep, read_input
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/files",
    name=ReadKind.CYCLE_FILES,
    response_model=FilesResponse,
)
def list_cycle_files(
    caller: CallerDep, read: Annotated[CycleFilesParams, read_input(CycleFilesParams)]
) -> FilesResponse:
    return read.answer(caller)


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/file",
    name=ReadKind.CYCLE_FILE,
    response_model=FileContentResponse,
)
def get_cycle_file(
    caller: CallerDep, read: Annotated[CycleFileParams, read_input(CycleFileParams)]
) -> FileContentResponse:
    return read.answer(caller)
