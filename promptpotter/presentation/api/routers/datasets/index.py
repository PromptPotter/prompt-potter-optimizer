"""The dataset LIST and one dataset's resolved pipeline — the two reads that answer "what is here"
and need nothing measured. The leaderboard reads live in ``leaderboard.py``, ingest in ``ingest.py``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    load_dataset_campaign_config,
)
from promptpotter.application.runner.inner.tasks import inner_tasks_path, load_inner_tasks
from promptpotter.domain.l4.proxies import InnerCycleUnscoreableError
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import NodeConfigParam, NodeOutputSchema, PipelineView
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import (
    dataset_pipeline_path,
    list_readable_datasets,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.presentation.api.deps import (
    StoresDep,
)
from promptpotter.presentation.api.routers.datasets._router import datasets_router
from promptpotter.shared.errors import (
    NotFoundError,
)

if TYPE_CHECKING:
    from pathlib import Path


class DatasetIndexEntry(StrictModel):
    """One row in the dataset registry — backs the Dashboard ``New campaign`` view.

    Wire shape pinned in ``docs/specs/m12-api-openapi.yaml::DatasetIndexEntry``.
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


class NestedPipelineRef(StrictModel):
    """Which node of THIS pipeline runs another whole pipeline, and whose. Both halves are
    derived from ``inner_tasks.yaml``, never declared a second time. Null on an ordinary
    dataset."""

    node: str = Field(description="Node id in this pipeline whose measurement runs `dataset`.")
    dataset: str = Field(description="Slug of the pipeline that node runs; fetch it the same way.")


class DatasetPipelineResponse(StrictModel):
    """Target pipeline view for a dataset overlay. `view` drives the webapp chat-pane hero;
    `pipeline` is the full parsed schema for consumers needing per-node config; `connector` is
    the original-cased name for chip labelling.
    """

    name: str
    connector: str
    # The connector KIND read straight off the raw overlay (`termnorm` / `promptpotter` / …).
    # A connector-level fact, peer of `connector` — NOT a `PipelineSchema` field, so it is
    # surfaced here rather than smuggled through `pipeline` (the parser drops unknown keys).
    # The webapp branches self-optimization (pp-self) rendering on it.
    backend_type: str | None
    pipeline: dict[str, Any]
    view: dict[str, Any] | None
    # Every param each node carries, keyed by node — COMPLETE (prompt + nested params
    # included, carrying no value), because `optimizer_tunable` is summed per node to
    # answer "is this node optimizer-locked". The config editor filters to the widget
    # kinds; `optimizer_locked` marks what the optimizer may never permute
    # (model/provider), which the operator may still set on a fork seed.
    node_config_schema: dict[str, list[NodeConfigParam]]
    # Per-node structured-output contract (read-only) — the steer panel shows it
    # beside the config so the operator sees the WHOLE node (model + params +
    # prompt + the structured output it produces). None for nodes with no schema.
    node_output_schema: dict[str, NodeOutputSchema | None]
    # The only wire naming a nested pipeline: otherwise a client can recover the inner
    # benchmark only as a prefix of `spawned_by.task`, which needs a cell already spawned.
    nests: NestedPipelineRef | None


def _nested_pipeline(dataset_dir: Path, view: PipelineView | None) -> NestedPipelineRef | None:
    """Owning an ``inner_tasks.yaml`` IS what makes a dataset outer (``runner/inner/tasks.py``);
    no name test recognises one. The node is the schema's own measurement node."""
    try:
        panel = load_inner_tasks(inner_tasks_path(dataset_dir))
    except InnerCycleUnscoreableError:
        # A read-only view must not raise where the runner would.
        return None
    node = next((n for n in (view.nodes if view else []) if n.kind == "measurement"), None)
    return NestedPipelineRef(node=node.id, dataset=panel.inner_benchmark) if node else None


@datasets_router.get("/{name}/pipeline", response_model=DatasetPipelineResponse)
def get_dataset_pipeline(name: str, stores: StoresDep) -> DatasetPipelineResponse:
    """Return the dataset overlay's parsed pipeline schema, graph view, and per-node
    config + output schema.

    Identity-gated through the same resolver as the other dataset reads — there is
    no unauthenticated path to a benchmark's pipeline/overlay config.
    """
    dataset_dir = readable_dataset_dir(stores, name)
    pipeline_path = dataset_pipeline_path(dataset_dir)
    if not pipeline_path.is_file():
        raise NotFoundError(f"Dataset '{name}' has no pipeline.yaml")
    raw = read_yaml(pipeline_path)
    # `parse_pipeline_response` strips lone surrogates at parse time so the
    # rendered model is already wire-safe (some overlays carry escape
    # sequences pointing at lone low surrogates that crash UTF-8 encode).
    schema = parse_pipeline_response(raw)
    connector = (raw.get("backend_name") or raw.get("name") or name).strip()
    backend_type = raw.get("backend_type")
    # Apply the dataset's default search-space narrowing so the setup editor opens
    # showing the recommended per-node locks (e.g. retrieval nodes origin-locked); the
    # draft's own overlay edits layer on top client-side. model/provider are always
    # optimizer-locked (operator-owned axes), so no per-campaign policy is read here.
    campaign_path = dataset_campaign_path(dataset_dir)
    if campaign_path.is_file():
        cfg = load_dataset_campaign_config(campaign_path)
        schema = schema.narrow(cfg.optimizer_narrowing)
    return DatasetPipelineResponse(
        name=name,
        connector=connector,
        backend_type=backend_type,
        pipeline=schema.model_dump(by_alias=True),
        view=schema.view.model_dump(by_alias=True) if schema.view is not None else None,
        node_config_schema=schema.node_config_schema(),
        node_output_schema=schema.node_output_schemas(),
        nests=_nested_pipeline(dataset_dir, schema.view),
    )


__all__ = [
    "DatasetIndexEntry",
    "DatasetIndexResponse",
    "DatasetPipelineResponse",
    "NestedPipelineRef",
]
