"""The recursion's connector; the connector table loads it by name, so no backend package imports the application."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench import difficulty
from promptpotter.application.intelligence import rasch
from promptpotter.application.runner.inner.spawn import (
    inner_cell_envelope_s,
    inner_cell_hop,
    run_inner_cycle,
)
from promptpotter.application.runner.inner.tasks import (
    InnerCells,
    ResolvedPanel,
    parse_panel,
    parse_resolved,
)
from promptpotter.application.scoring import metrics, selection
from promptpotter.connectors.protocol import Connector, InProcessWorkload
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cells import Seat
from promptpotter.domain.l4 import proxies
from promptpotter.domain.l4.inner_origin import INNER_ORIGIN_KEY
from promptpotter.domain.l4.proxies import INNER_RESULT_KEY, OUTER_PROXY_KEYS
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.hashing import module_source_digest, stable_hash

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType

    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


# A RESOURCE ceiling (peak RSS, one shared provider key), never a scientific one.
MAX_CELLS_IN_FLIGHT = 4


def measurement_modules() -> tuple[ModuleType, ...]:
    """Never ``APP_VERSION``: it voids every banked cell on each release, measurement changed or not."""
    # The δ scale: how it is fit, and what each cell reads it on.
    return (rasch, metrics, selection, proxies, difficulty)


def _inner_cells(stores: Stores, experiment: Mapping[str, Any] | None) -> InnerCells:
    if experiment is None:
        raise PayloadInvalidError(
            "an outer dataset's graph and identity derive from its panel, "
            "which this box does not hold.",
            code="pipeline_config_invalid",
        )
    return parse_resolved(experiment).panel.resolve_cells(stores)


def _pipeline_declaration(
    stores: Stores, _dataset: ResolvedDataset, experiment: Mapping[str, Any] | None
) -> dict[str, Any]:
    return _inner_cells(stores, experiment).pipeline()


def _identity_config(
    stores: Stores, _dataset: ResolvedDataset, experiment: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """What reads every cell's number alike; what measures ONE cell is in its pin (`_instrument`)."""
    cells = _inner_cells(stores, experiment)
    return {cells.chain[0]: {INNER_ORIGIN_KEY: module_source_digest(*measurement_modules())}}


def _instrument(cells: InnerCells, cell: Seat) -> str:
    held = cells.held(cell)
    # `config` only: widening a permission list changes nothing the origin does, so it voids no cell.
    nodes = (
        {n: (node or {}).get("config") for n, node in held.pipeline["nodes"].items()}
        if held.pipeline and isinstance(held.pipeline.get("nodes"), dict)
        else None
    )
    config = CampaignConfig.model_validate(held.campaign_config).identity()
    # The spec's treatment, which subtracts the depth a seat may deepen to.
    return stable_hash([cells.spec(cell).treatment(), nodes, config])


def promptpotter_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query}

    optimizer_prompt_overrides = {k: dict(v) for k, v in node_config_items(pipeline_params) if v}
    if optimizer_prompt_overrides:
        payload["optimizer_prompt_overrides"] = optimizer_prompt_overrides

    return payload


def _resolve_panel(stores: Stores, panel: Mapping[str, Any]) -> dict[str, Any]:
    """Each cell's instrument beside the panel; only what was declared survives the dump, so a
    field that gains a default re-keys no banked cell."""
    parsed = parse_panel(panel)
    cells = parsed.resolve_cells(stores)
    resolved = ResolvedPanel(
        panel=parsed,
        instruments={t.id: _instrument(cells, t) for t in parsed.tasks},
        datasets={t.id: parsed.dataset_for(t) for t in parsed.tasks},
    )
    return resolved.model_dump(mode="json", exclude_unset=True)


def _extract_experiment(experiment_data: dict[str, Any]) -> list[dict[str, Any]]:
    """``ground_truth`` is ``None``: an L4 cell is graded by its outer proxies, never a label."""
    return parse_resolved(experiment_data).samples()


async def _in_process_run(
    _workload: InProcessWorkload, sample: Sample, payload: dict[str, Any]
) -> dict[str, Any]:
    return await run_inner_cycle(sample, payload)


def _cell_envelope_s(sample: Sample, pipeline_params: dict[str, Any] | None) -> float:
    """Through this connector's OWN adapter, so the envelope and the run that spends it read one payload."""
    return inner_cell_envelope_s(sample, promptpotter_wire_adapter(sample.query, pipeline_params))


def _cell_hop(sample: Sample, pipeline_params: dict[str, Any] | None) -> CycleHop | None:
    return inner_cell_hop(sample, promptpotter_wire_adapter(sample.query, pipeline_params))


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
    cell_hop=_cell_hop,
    measured_unit="cell",
    required_observation_keys=(INNER_RESULT_KEY, *OUTER_PROXY_KEYS),
    resolve_experiment=_resolve_panel,
    # The outer's graph IS the inner optimizer's, so it is served here, never mirrored in a file.
    pipeline_declaration=_pipeline_declaration,
    prompt_fields_as_node_params=True,
    identity_config=_identity_config,
)
