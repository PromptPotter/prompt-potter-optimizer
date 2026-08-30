"""PromptPotter-as-connector — the optimizer-of-the-optimizer. A THIN adapter: it declares
``execution="in_process"`` and delegates, because the recursion is not a wire binding."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.connectors.protocol import Connector
from promptpotter.domain.pipeline_overlay import node_config_items

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    import httpx

logger = logging.getLogger(__name__)


# Reserved per-node config key carrying the inner-origin fingerprint. Part of
# measurement identity (rides node_configs / the origin cycle id), NEVER a wire
# tunable — the adapter strips it before building ``optimizer_prompt_overrides``.
INNER_ORIGIN_KEY = "inner_origin"


def instrument_of(pipeline_params: object) -> str | None:
    """Which INSTRUMENT a searchpoint was measured on — its inner-origin fingerprint, or ``None``
    where the backend has none. The one reader of ``INNER_ORIGIN_KEY`` out of a params map, so the
    mint-time cohort warning and the evidence roster cannot disagree about where it is written."""
    if not isinstance(pipeline_params, dict):
        return None
    node = pipeline_params.get("l1_generate")
    value = node.get(INNER_ORIGIN_KEY) if isinstance(node, dict) else None
    return value if isinstance(value, str) and value else None


# Most inner campaigns the operator may set running at once — a RESOURCE ceiling (peak RSS and
# one shared provider key), never a scientific one: rows absorb in walk order at any depth.
# The true peak only because `run_query_loop` pops and AWAITS a slot before `_absorb` runs, so a
# PoBB backfill (itself a whole inner campaign) replaces the finished cell rather than adding to
# it. Move `on_sample_pre_check` above the pop and this constant understates the peak.
MAX_CELLS_IN_FLIGHT = 4


def _revision_key(family: str | None, version: str | int | None) -> str | None:
    """``None`` where the node names no family — a distinct input from any real key, rather than
    a silent match against another node that also declares nothing."""
    if not family:
        return None
    return f"{family}/{version}" if version is not None else family


def _inner_optimizer_revision(dataset_dir: Path) -> dict[str, Any]:
    """What the inner cycle's optimizer nodes RESOLVE TO — the baseline every arm on the panel
    departs from, named by the artifacts it actually reads rather than by the file they sit in.

    NARROW on purpose, and that is the whole point. Hashing the entire manifest and the entire
    generated schema registry meant a ``checkin`` prompt edit, a node description, a widened
    ``available_models`` or a schema regenerated for an unrelated model voided a panel that cost
    an hour to measure — none of which changes what an inner campaign does. Read here is exactly
    the optimizer nodes the OUTER dataset declares as its mutation surface: each one's prompt
    body, its resolved output schema and its config. DERIVED from that declaration rather than a
    name list, so a surface that grows a node is covered without an edit here.
    """
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        optimizer_manifest,
        optimizer_resolved_schemas,
    )
    from promptpotter.infrastructure.store.io import read_yaml_optional

    # The PARSED manifest, never its bytes. Its siblings in this fingerprint already hash parsed
    # values, and the file is comment-bearing YAML — byte-hashing would void every banked outer
    # measurement the moment someone documented a node, a change with no behavioural content.
    manifest = optimizer_manifest()
    # The response schemas are prompt text — they ride `response_format` on every call, and
    # their field names and `description` prose ARE the mechanism where the grammar does not
    # bind (`docs/concepts/structured-output.md`). They sit in the generated sibling, so
    # reading the manifest alone left them out.
    schemas = optimizer_resolved_schemas()
    outer_nodes = (read_yaml_optional(dataset_dir / "pipeline.yaml") or {}).get("nodes") or {}
    revision: dict[str, Any] = {}
    for name, node in sorted(outer_nodes.items()):
        if (node or {}).get("type") != "optimizer_prompt":
            continue
        config = ((manifest.get("nodes") or {}).get(name) or {}).get("config") or {}
        prompt_key = _revision_key(config.get("prompt_family"), config.get("prompt_version"))
        schema_key = _revision_key(config.get("schema_family"), config.get("schema_version"))
        revision[name] = {
            "config": config,
            "prompt": (manifest.get("resolved_prompts") or {}).get(prompt_key),
            "schema": schemas.get(schema_key) if schema_key else None,
        }
    return revision


def measurement_modules() -> tuple[ModuleType, ...]:
    """The ESTIMATOR's own code, which decides what a banked cell's number means, in digest order.

    Never ``APP_VERSION`` here: it voids every banked cell on each release while saying nothing
    about whether the measurement changed, and a corpus that cannot survive a version bump cannot
    accumulate at all. These five modules are what genuinely decides the number: the composite,
    the election and its intervals, the ability fit the levels are expressed in, the SCALE that
    fit is read on, and the law that reads a finished inner cycle. Unlike its prompt-side twin
    ``registry.fingerprinted_modules``, this roster is a CHOICE within the layer rather than a
    package, so it is listed and pinned by ``tests/test_integrity.py`` instead of walked.
    """
    from promptpotter.application.intelligence import exploration
    from promptpotter.application.runner.inner import ruler
    from promptpotter.application.scoring import metrics, selection
    from promptpotter.domain.l4 import proxies

    return (exploration, metrics, selection, proxies, ruler)


def _measurement_source_digest() -> str:
    """Same AST normalization as the prompt side — a docstring is free, an expression is not."""
    from promptpotter.shared.hashing import module_source_digest

    return module_source_digest(*measurement_modules())


def _identity_config(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    """The inner optimizer's effective-revision fingerprint: what the inner optimizer nodes resolve
    to, the per-node layouts, the panel and estimator source, and the inner benchmark's own config."""
    from promptpotter.application.optimization.dispatch.injections.registry import (
        injection_source_digest,
    )
    from promptpotter.domain.l1_layout import NODE_LAYOUTS
    from promptpotter.domain.pipeline_schema import stable_hash
    from promptpotter.infrastructure.store.io import read_yaml_optional

    inner_optimizer = _inner_optimizer_revision(dataset_dir)
    layouts = {name: spec.model_dump(mode="json") for name, spec in sorted(NODE_LAYOUTS.items())}
    # `layouts` names WHICH panels fill each prompt; this is what those panels SAY. The text
    # is code, so nothing above reaches it — see `injection_source_digest`.
    panel_text = injection_source_digest()
    inner_tasks = read_yaml_optional(dataset_dir / "inner_tasks.yaml") or {}
    # `config` only, deliberately. `available_models` is a permission list and
    # `optimizer.param_allowed_values` bounds what L1 may PROPOSE — neither changes what the
    # origin does, so widening either must not void a panel that cost an hour to measure.
    # The benchmark resolves as a sibling of the outer dataset dir, which is how every layout
    # ships it (repo `datasets/`, staged `assets/benchmarks/`, tenant root); an unresolvable
    # one hashes as ``None``, which is a distinct input from any real config rather than a
    # silent match.
    benchmark = inner_tasks.get("inner_benchmark")
    benchmark_dir = dataset_dir.parent / str(benchmark) if benchmark else None
    inner_pipeline = read_yaml_optional(benchmark_dir / "pipeline.yaml") if benchmark_dir else None
    inner_campaign = read_yaml_optional(benchmark_dir / "campaign.yaml") if benchmark_dir else None
    inner_spec = {
        "benchmark": benchmark,
        "config": inner_tasks.get("inner_benchmark_config") or {},
        "tasks": inner_tasks.get("tasks") or [],
        "nodes": (
            {name: (node or {}).get("config") for name, node in inner_pipeline["nodes"].items()}
            if inner_pipeline and isinstance(inner_pipeline.get("nodes"), dict)
            else None
        ),
        "campaign": (inner_campaign or {}).get("campaign_config"),
    }
    fingerprint = stable_hash(
        [
            inner_optimizer,
            layouts,
            panel_text,
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


def _extract_experiment(
    experiment_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Inner-benchmark tasks → ``(queries, index_terms)``. **There is no label to match in L4**:
    ``ground_truth`` is the ``inner:{query}`` token ``run_inner_cycle`` emits — keep the two in sync."""
    tasks = experiment_data.get("tasks", [])
    queries: list[dict[str, Any]] = []
    for t in tasks:
        tid = t.get("id")
        if not tid:
            continue
        queries.append({"query": tid, "ground_truth": f"inner:{tid}"})
    return queries, []


async def _in_process_run(query: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run an inner cycle and return its three proxy metrics. The runner sandboxes it under a FLAT
    ``<workspace>/.inner/<key>/`` registry — never nested, because physical nesting blew MAX_PATH."""
    from promptpotter.application.runner.inner.spawn import run_inner_cycle

    return await run_inner_cycle(query, payload)


CONNECTOR = Connector(
    name="promptpotter",
    execution="in_process",
    wire_adapter=promptpotter_wire_adapter,
    session_factory=PromptPotterSession,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
    # One sample is a whole inner campaign — tens of minutes, almost all of it waiting on the
    # provider — and a round is hours, so the group is the only unit that can bound a press.
    max_cells_in_flight=MAX_CELLS_IN_FLIGHT,
    concurrency_arming="batch",
    measured_unit="cell",
    # The outer "samples" are the inner tasks — read from this file in the dataset
    # config dir and fed through ``extract_experiment`` at init (no CSV table).
    experiment_file="inner_tasks.yaml",
    identity_config=_identity_config,
)


__all__ = ["CONNECTOR", "PromptPotterSession"]
