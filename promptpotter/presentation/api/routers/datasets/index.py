from __future__ import annotations

from promptpotter.application import origin
from promptpotter.application.origin import DatasetIndexEntry
from promptpotter.application.pipeline_resolve import (
    DatasetPipelineResponse,
    resolve_pipeline_for_dataset,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import (
    StoresDep,
)
from promptpotter.presentation.api.routers.datasets._router import datasets_router


class DatasetIndexResponse(StrictModel):
    datasets: list[DatasetIndexEntry]


@datasets_router.get("", response_model=DatasetIndexResponse)
def list_datasets(stores: StoresDep) -> DatasetIndexResponse:
    """Every dataset this identity may read: its own tenant origins, then install content."""
    return DatasetIndexResponse(datasets=origin.list_datasets(stores))


@datasets_router.get("/{name}/pipeline", response_model=DatasetPipelineResponse)
def get_dataset_pipeline(name: str, stores: StoresDep) -> DatasetPipelineResponse:
    """One dataset's declared pipeline, gated by the resolver every dataset read shares."""
    return resolve_pipeline_for_dataset(stores, name)


__all__ = ["DatasetIndexResponse"]
