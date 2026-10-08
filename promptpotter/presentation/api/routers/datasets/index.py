"""The dataset LIST and one dataset's resolved pipeline — the two reads that answer "what is here"
and need nothing measured. The leaderboard reads live in ``leaderboard.py``, ingest in ``ingest.py``."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from promptpotter.application.pipeline_resolve import (
    DatasetPipelineResponse,
    resolve_pipeline_for_dataset,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import list_readable_datasets
from promptpotter.presentation.api.deps import (
    StoresDep,
)
from promptpotter.presentation.api.routers.datasets._router import datasets_router


class DatasetIndexEntry(StrictModel):
    """One row in the dataset registry — backs the Dashboard ``New campaign`` view.

    Wire shape pinned in ``docs/specs/api-openapi.yaml::DatasetIndexEntry``.
    """

    name: str = Field(description="Slug used as the path segment under `datasets/`.")
    title: str | None = Field(default=None, description="Display title (from `dataset.md`).")
    tier: Literal["yours", "install"] = Field(
        description=(
            "``yours`` = user-owned Origin under ``projects/{tenant}/datasets/{slug}/``. "
            "``install`` = content that ships with the product at ``datasets/{slug}/`` "
            "(benchmarks, demos, ``promptpotter-self``) — tracked in git, so readable by "
            "anyone using the install. A ``yours`` slug shadows an ``install`` one."
        ),
    )
    n_samples: int | None = Field(
        default=None,
        description=(
            "Sample bank size from ``cache.json``; ``null`` when the cache has not been "
            "materialized, which is not the same as a dataset holding zero usable rows."
        ),
    )


class DatasetIndexResponse(StrictModel):
    datasets: list[DatasetIndexEntry]


@datasets_router.get("", response_model=DatasetIndexResponse)
def list_datasets(stores: StoresDep) -> DatasetIndexResponse:
    """Every dataset this identity may read — its own tenant Origins, then install content.

    The rule lives once in ``store/dataset_access.py`` and is shared with the
    per-dataset read endpoints, so the picker can never list a dataset that
    ``GET /datasets/{name}/...`` would then deny.
    """
    return DatasetIndexResponse(
        datasets=[
            DatasetIndexEntry(
                name=ref.name, title=ref.title, tier=ref.tier, n_samples=ref.n_samples
            )
            for ref in list_readable_datasets(stores)
        ]
    )


@datasets_router.get("/{name}/pipeline", response_model=DatasetPipelineResponse)
def get_dataset_pipeline(name: str, stores: StoresDep) -> DatasetPipelineResponse:
    """One dataset's declared pipeline, gated by the resolver every dataset read shares."""
    return resolve_pipeline_for_dataset(stores, name)


__all__ = ["DatasetIndexEntry", "DatasetIndexResponse"]
