from __future__ import annotations

from typing import Annotated

from promptpotter.application.origin_listing import DatasetIndexResponse
from promptpotter.application.pipeline_responses import DatasetPipelineResponse
from promptpotter.application.read_params import DatasetPipelineParams, DatasetsParams
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import CallerDep, read_input
from promptpotter.presentation.api.routers.datasets._router import datasets_router


@datasets_router.get("", response_model=DatasetIndexResponse, name=ReadKind.DATASETS)
def list_datasets(
    caller: CallerDep, read: Annotated[DatasetsParams, read_input(DatasetsParams)]
) -> DatasetIndexResponse:
    return read.answer(caller)


@datasets_router.get(
    "/{name:path}/pipeline", response_model=DatasetPipelineResponse, name=ReadKind.DATASET_PIPELINE
)
async def get_dataset_pipeline(
    caller: CallerDep, read: Annotated[DatasetPipelineParams, read_input(DatasetPipelineParams)]
) -> DatasetPipelineResponse:
    return await read.answer(caller)
