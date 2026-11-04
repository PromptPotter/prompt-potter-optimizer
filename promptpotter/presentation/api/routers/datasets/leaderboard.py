from __future__ import annotations

from typing import Annotated

from fastapi import Request, Response

from promptpotter.application.read_params import DatasetCellParams, DatasetCellsParams
from promptpotter.domain.cells import Cell, CellsResponse
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import CallerDep, read_input
from promptpotter.presentation.api.routers.campaigns._conditional import conditional_json
from promptpotter.presentation.api.routers.datasets._router import datasets_router


@datasets_router.get(
    "/{name:path}/cells", name=ReadKind.DATASET_CELLS, response_model=CellsResponse
)
def get_dataset_cells(
    request: Request,
    caller: CallerDep,
    read: Annotated[DatasetCellsParams, read_input(DatasetCellsParams)],
) -> Response:
    cells = read.scoped(caller.stores)
    return conditional_json(
        request,
        (cells.version(), read),
        lambda: read.logged(cells),
    )


@datasets_router.get("/{name:path}/cells/{answer}", name=ReadKind.DATASET_CELL, response_model=Cell)
async def get_dataset_cell(
    caller: CallerDep, read: Annotated[DatasetCellParams, read_input(DatasetCellParams)]
) -> Cell:
    return await read.answer(caller)
