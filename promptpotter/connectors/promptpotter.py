"""PromptPotter-as-connector — the optimizer-of-the-optimizer. A THIN adapter: it declares
``execution="in_process"`` and delegates, because the recursion is not a wire binding."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application import optimizers
from promptpotter.application.campaign_config import OptimizationConfig
from promptpotter.application.intelligence import exploration
from promptpotter.application.optimizer_manifest import SelectedOptimizer, resolve_optimizer
from promptpotter.application.runner.inner import ruler
from promptpotter.application.runner.inner.spawn import inner_cell_envelope_s, run_inner_cycle
from promptpotter.application.runner.inner.tasks import InnerTasks
from promptpotter.application.scoring import metrics, selection
from promptpotter.config.prompt_blocks import block_library
from promptpotter.connectors.protocol import Connector, InProcessWorkload
from promptpotter.domain.l4 import proxies
from promptpotter.domain.l4.inner_origin import INNER_ORIGIN_KEY
from promptpotter.domain.l4.proxies import INNER_RESULT_KEY, OUTER_PROXY_KEYS
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay, stable_hash
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_yaml, read_yaml_optional
from promptpotter.shared.hashing import module_source_digest

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from types import ModuleType

    import httpx

    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


# Most inner campaigns the operator may set running at once — a RESOURCE ceiling (peak RSS and
# one shared provider key), never a scientific one: rows absorb in walk order at any depth.
# The true peak only because the ROUND counts a PoBB backfill — itself a whole inner campaign —
# against the same depth as a cell, and a cancelled call until it has wound down
# (`query_loop.py::run_walks`). Stop counting one and this constant understates the peak.
MAX_CELLS_IN_FLIGHT = 4


def _inner_optimizer_revision(dataset_dir: Path, inner: SelectedOptimizer) -> dict[str, Any]:
    """What the inner cycle's optimizer RESOLVES TO — the baseline every arm on the panel departs
    from: the manifest by name and version, and each node the OUTER dataset declares as its
    mutation surface by its prompt body, output schema and resolved config.

    NARROW on purpose. Hashing the whole manifest meant a node description, a widened
    ``available_models`` or a schema regenerated for an unrelated node voided a panel that cost an
    hour to measure. DERIVED from the outer declaration rather than a name list, so a surface that
    grows a node is covered without an edit here. The PARSED manifest, never its bytes."""
    outer = parse_pipeline_response(read_yaml(dataset_dir / "pipeline.yaml"))
    return {
        "manifest": inner.name,
        "version": inner.version,
        "nodes": {
            name: inner.node_digests[name]
            for name in sorted(n.name for n in outer.config_nodes if n.tunes_llm)
        },
    }


def measurement_modules() -> tuple[ModuleType, ...]:
    """The ESTIMATOR's own code, which decides what a banked cell's number means, in digest order.

    Never ``APP_VERSION`` here: it voids every banked cell on each release while saying nothing
    about whether the measurement changed, and a corpus that cannot survive a version bump cannot
    accumulate at all. These five modules are what genuinely decides the number: the composite,
    the election and its intervals, the ability fit the levels are expressed in, the SCALE that
    fit is read on, and the law that reads a finished inner cycle. Unlike its prompt-side twin
    ``facade.fingerprinted_modules``, this roster is a CHOICE within the layer rather than a
    package, so it is listed and pinned by ``tests/test_integrity.py`` instead of walked.
    """
    return (exploration, metrics, selection, proxies, ruler)


def _measurement_source_digest() -> str:
    """Same AST normalization as the prompt side — a docstring is free, an expression is not."""
    return module_source_digest(*measurement_modules())


def _check_prompt_closure() -> None:
    for runtime in optimizers.runtimes().values():
        runtime.source_digest(*measurement_modules())


def _identity_config(
    stores: Stores, dataset_dir: Path, inner_tasks: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """The inner optimizer's effective-revision fingerprint: which manifest the inner campaign
    selects and what its nodes resolve to, the source deciding what its prompts say, the estimator
    source, and the inner benchmark's own config. In the recursion the optimizer IS the instrument,
    so two optimizers' inner cells must never pool under one key. Not the task list — each task is
    its own sample's ``source_pin`` (:func:`_extract_experiment`), so adding one to
    ``inner_tasks.yaml`` voids none of the cells already banked."""
    inner_tasks = inner_tasks or {}
    # `config` only, deliberately. `available_models` is a permission list and
    # `optimizer.param_allowed_values` bounds what L1 may PROPOSE — neither changes what the
    # origin does, so widening either must not void a panel that cost an hour to measure.
    # The benchmark resolves through `readable_dataset_dir`, the dir the spawn runs and the ruler
    # grades; an unresolvable one hashes as ``None``, a distinct input from any real config.
    benchmark = inner_tasks.get("inner_benchmark")
    benchmark_dir: Path | None = None
    if benchmark:
        with contextlib.suppress(DatasetAccessError):
            benchmark_dir = readable_dataset_dir(stores, str(benchmark))
    inner_pipeline = read_yaml_optional(benchmark_dir / "pipeline.yaml") if benchmark_dir else None
    inner_campaign = read_yaml_optional(benchmark_dir / "campaign.yaml") if benchmark_dir else None
    inner_spec = {
        "benchmark": benchmark,
        "config": inner_tasks.get("inner_benchmark_config") or {},
        "nodes": (
            {name: (node or {}).get("config") for name, node in inner_pipeline["nodes"].items()}
            if inner_pipeline and isinstance(inner_pipeline.get("nodes"), dict)
            else None
        ),
        "campaign": (inner_campaign or {}).get("campaign_config"),
    }
    # The manifest the INNER campaign selects, under its own overlay. An unresolvable benchmark
    # hashes the default manifest, which is what such an inner campaign would run.
    inner_opt = ((inner_spec["campaign"] or {}).get("optimization")) or {}
    inner = resolve_optimizer(
        inner_opt.get("optimizer", OptimizationConfig.model_fields["optimizer"].default),
        {
            node: ManifestNodeOverlay.model_validate(raw)
            for node, raw in (inner_opt.get("nodes") or {}).items()
        },
    )
    inner_optimizer = _inner_optimizer_revision(dataset_dir, inner)
    # What the inner optimizer's prompts SAY, and which of its panels fill each one: both are
    # code, so nothing above reaches them — see `OptimizerRuntime.source_digest`.
    panel_text = inner.runtime.source_digest(*measurement_modules())
    fingerprint = stable_hash(
        [
            inner_optimizer,
            panel_text,
            # The block library is prompt MATERIAL stored as data, which no source digest reads —
            # hashed as data, like the manifest above.
            block_library(),
            _measurement_source_digest(),
            inner_spec,
        ]
    )[:12]
    return {"l1_generate": {INNER_ORIGIN_KEY: fingerprint}}


# ---------------------------------------------------------------------------
# Wire payload shape
# ---------------------------------------------------------------------------


def promptpotter_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Outbound payload describing an inner cycle to run. ``pipeline_params`` is keyed by
    inner-optimizer prompt node; a ``model`` key rides untouched and merges at the inner ``llm_call``."""
    payload: dict[str, Any] = {"query": query}

    optimizer_prompt_overrides: dict[str, dict[str, Any]] = {}
    for k, v in node_config_items(pipeline_params):
        # The inner-origin fingerprint is identity config, not an override —
        # the inner loop must never see it as a template field.
        stripped = {fk: fv for fk, fv in v.items() if fk != INNER_ORIGIN_KEY}
        if stripped:
            optimizer_prompt_overrides[k] = stripped

    if optimizer_prompt_overrides:
        payload["optimizer_prompt_overrides"] = optimizer_prompt_overrides

    return payload


# ---------------------------------------------------------------------------
# Session lifecycle (in-process noop)
# ---------------------------------------------------------------------------


class PromptPotterSession:
    """In-process noop session — there is no remote service, so there is no handshake and
    ``set_terms`` / ``recover`` are no-ops."""

    __slots__ = ()

    async def set_terms(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        terms: list[str],
    ) -> dict[str, Any]:
        return {"status": "noop", "terms_count": len(terms)}

    async def recover(self, http: httpx.AsyncClient, base_url: str) -> bool:
        return True


# ---------------------------------------------------------------------------
# Experiment-data extraction
# ---------------------------------------------------------------------------


def _resolve_panel(panel: Mapping[str, Any]) -> dict[str, Any]:
    """The panel through its type, ``axes:`` already expanded into ``tasks:``. Only what was
    declared survives the dump, so a field that gains a default re-keys no banked cell."""
    return InnerTasks.model_validate(panel).model_dump(mode="json", exclude_unset=True)


def _extract_experiment(
    experiment_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Inner-benchmark tasks → ``(queries, index_terms)``. **There is no label to match in L4** —
    the cell is graded by ``compute_outer_proxies``, so ``ground_truth`` is ``None`` and says so."""
    queries = [
        {"query": t["id"], "ground_truth": None, "source_pin": dict(t)}
        for t in experiment_data["tasks"]
    ]
    return queries, []


async def _in_process_run(
    _workload: InProcessWorkload, query: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Run an inner cycle and return its three proxy metrics."""
    return await run_inner_cycle(query, payload)


def _cell_envelope_s(query: str, pipeline_params: dict[str, Any] | None) -> float:
    """Through this connector's OWN adapter, so the envelope and the run that spends it read one
    payload — the cell's identity, and therefore its banked depth, is in the overrides."""
    return inner_cell_envelope_s(query, promptpotter_wire_adapter(query, pipeline_params))


CONNECTOR = Connector(
    name="promptpotter",
    execution="in_process",
    wire_adapter=promptpotter_wire_adapter,
    session_factory=PromptPotterSession,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
    # One sample is a whole inner campaign — tens of minutes, almost all of it waiting on the
    # provider — so the ceiling here is what bounds a press, and it is the only thing that does.
    max_cells_in_flight=MAX_CELLS_IN_FLIGHT,
    # The inner campaign's calls go through this process's clients, each admitted on its own; and
    # cancelling one stops the calls it has not made yet.
    holds_own_sends=True,
    cancel_stops_billing=True,
    # A whole campaign runs per cell, so the awaits inside one are unbounded in sum: without this
    # a throttle storm stretches one cell across the round that was measuring it.
    cell_envelope_s=_cell_envelope_s,
    measured_unit="cell",
    # Every key `run_inner_cycle` puts on the wire that the outer formula reads. Verified against
    # the dataset's declared observation_mappings at init.
    required_observation_keys=(INNER_RESULT_KEY, *OUTER_PROXY_KEYS),
    # The outer "samples" are the inner tasks — read from this file in the dataset
    # config dir and fed through ``extract_experiment`` at init (no CSV table).
    experiment_file="inner_tasks.yaml",
    resolve_experiment=_resolve_panel,
    identity_config=_identity_config,
    completion_check=_check_prompt_closure,
)


__all__ = ["CONNECTOR", "PromptPotterSession"]
