"""The recursion's connector; the connector table loads it by name, so no backend package imports the application."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence import exploration
from promptpotter.application.runner.inner import ruler
from promptpotter.application.runner.inner.spawn import inner_cell_envelope_s, run_inner_cycle
from promptpotter.application.runner.inner.tasks import (
    InnerCells,
    InnerTasks,
    resolve_inner_cells,
)
from promptpotter.application.scoring import metrics, selection
from promptpotter.connectors.protocol import Connector, InProcessWorkload
from promptpotter.domain.l4 import proxies
from promptpotter.domain.l4.inner_origin import INNER_ORIGIN_KEY
from promptpotter.domain.l4.proxies import INNER_RESULT_KEY, OUTER_PROXY_KEYS
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.hashing import module_source_digest, stable_hash

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from types import ModuleType

    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


# A RESOURCE ceiling (peak RSS, one shared provider key), never a scientific one.
MAX_CELLS_IN_FLIGHT = 4


def measurement_modules() -> tuple[ModuleType, ...]:
    """Never ``APP_VERSION``: it voids every banked cell on each release, measurement changed or not."""
    return (exploration, metrics, selection, proxies, ruler)


def _measurement_source_digest() -> str:
    return module_source_digest(*measurement_modules())


def _inner_cells(stores: Stores, experiment: Mapping[str, Any] | None) -> InnerCells:
    if experiment is None:
        raise PayloadInvalidError(
            f"an outer dataset's graph and identity derive from its {CONNECTOR.experiment_file}, "
            "which this box does not hold.",
            code="pipeline_config_invalid",
        )
    return resolve_inner_cells(stores, InnerTasks.model_validate(experiment))


def _pipeline_declaration(stores: Stores, experiment: Mapping[str, Any] | None) -> dict[str, Any]:
    return _inner_cells(stores, experiment).pipeline()


def _identity_config(
    stores: Stores, _dataset_dir: Path, experiment: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """Not the task list: each task is its own sample's ``source_pin``, so adding one voids no banked cell."""
    cells = _inner_cells(stores, experiment)
    # `config` only: widening a permission list changes nothing the origin does, so it voids no panel.
    datasets = {
        name: {
            "nodes": {n: (node or {}).get("config") for n, node in cell.pipeline["nodes"].items()}
            if cell.pipeline and isinstance(cell.pipeline.get("nodes"), dict)
            else None,
            "campaign": dict(cell.campaign_config),
        }
        for name, cell in cells.by_dataset.items()
    }
    inner_spec = {
        "benchmark": cells.panel.inner_benchmark,
        "config": (experiment or {}).get("inner_benchmark_config") or {},
        "datasets": datasets,
    }
    fingerprint = stable_hash([cells.treatment, _measurement_source_digest(), inner_spec])
    return {cells.chain[0]: {INNER_ORIGIN_KEY: fingerprint}}


def promptpotter_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query}

    optimizer_prompt_overrides = {k: dict(v) for k, v in node_config_items(pipeline_params) if v}
    if optimizer_prompt_overrides:
        payload["optimizer_prompt_overrides"] = optimizer_prompt_overrides

    return payload


def _resolve_panel(panel: Mapping[str, Any]) -> dict[str, Any]:
    """Only what was declared survives the dump, so a field that gains a default re-keys no banked cell."""
    return InnerTasks.model_validate(panel).model_dump(mode="json", exclude_unset=True)


def _extract_experiment(experiment_data: dict[str, Any]) -> list[dict[str, Any]]:
    """``ground_truth`` is ``None``: an L4 cell is graded by ``compute_outer_proxies``, never a label."""
    return [
        {"query": t["id"], "ground_truth": None, "source_pin": dict(t)}
        for t in experiment_data["tasks"]
    ]


async def _in_process_run(
    _workload: InProcessWorkload, sample: Sample, payload: dict[str, Any]
) -> dict[str, Any]:
    return await run_inner_cycle(sample, payload)


def _cell_envelope_s(sample: Sample, pipeline_params: dict[str, Any] | None) -> float:
    """Through this connector's OWN adapter, so the envelope and the run that spends it read one payload."""
    return inner_cell_envelope_s(sample, promptpotter_wire_adapter(sample.query, pipeline_params))


CONNECTOR = Connector(
    name="promptpotter",
    execution="in_process",
    wire_adapter=promptpotter_wire_adapter,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
    max_cells_in_flight=MAX_CELLS_IN_FLIGHT,
    holds_own_sends=True,
    cancel_stops_billing=True,
    # Without it a throttle storm stretches one cell, a whole campaign, across the round measuring it.
    cell_envelope_s=_cell_envelope_s,
    measured_unit="cell",
    required_observation_keys=(INNER_RESULT_KEY, *OUTER_PROXY_KEYS),
    experiment_file="inner_tasks.yaml",
    resolve_experiment=_resolve_panel,
    # The outer's graph IS the inner optimizer's, so it is served here, never mirrored in a file.
    pipeline_declaration=_pipeline_declaration,
    prompt_fields_as_node_params=True,
    identity_config=_identity_config,
)


__all__ = ["CONNECTOR"]
