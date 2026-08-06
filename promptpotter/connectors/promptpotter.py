"""PromptPotter-as-connector — the optimizer-of-the-optimizer. A THIN adapter: it declares
``execution="in_process"`` and delegates, because the recursion is not a wire binding."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.connectors.protocol import Connector
from promptpotter.domain.opt_search_point import node_config_items

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

logger = logging.getLogger(__name__)


# Reserved per-node config key carrying the inner-origin fingerprint. Part of
# measurement identity (rides node_configs / the origin cycle id), NEVER a wire
# tunable — the adapter strips it before building ``optimizer_prompt_overrides``.
INNER_ORIGIN_KEY = "inner_origin"


def _identity_config(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    """The inner optimizer's effective-revision fingerprint: the optimizer prompt text and response
    schemas, the per-node layouts, the engine version, ``inner_tasks.yaml``, the benchmark's config."""
    from promptpotter.application.optimization.dispatch.injections.registry import (
        injection_source_digest,
    )
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        optimizer_manifest,
        optimizer_resolved_schemas,
    )
    from promptpotter.config.settings import APP_VERSION
    from promptpotter.domain.l1_layout import NODE_LAYOUTS
    from promptpotter.domain.pipeline_schema import stable_hash
    from promptpotter.infrastructure.store.io import read_yaml_optional

    # The PARSED manifest, never its bytes. Its two siblings in this fingerprint
    # (`layouts`, `inner_spec`) already hash parsed values, and the file is now
    # comment-bearing YAML — byte-hashing would void every banked outer measurement
    # the moment someone documented a node, a change with no behavioural content.
    origin_manifest = optimizer_manifest()
    # The response schemas are prompt text — they ride `response_format` on every call, and
    # their field names and `description` prose ARE the mechanism where the grammar does not
    # bind (`docs/concepts/structured-output.md`). They sit in the generated sibling, so
    # hashing the manifest alone left them out.
    origin_schemas = optimizer_resolved_schemas()
    layouts = {name: spec.model_dump(mode="json") for name, spec in sorted(NODE_LAYOUTS.items())}
    # `layouts` names WHICH panels fill each prompt; this is what those panels SAY. The text
    # is code, so nothing above reaches it — see `injection_source_digest`.
    panel_text = injection_source_digest()
    # The inner-run config is part of the inner origin's effective behavior, so it
    # joins the fingerprint — changing `inner_optimizer_temperature` (or any geometry
    # knob) invalidates outer-sample rows measured under the prior value instead of
    # reusing them stale (the identity-joined plumbing l4-outer-loop.md § item 5 named).
    # The WHOLE inner spec defines the inner baseline — the benchmark NAME and its task list
    # (which bank + which seeds/cells), not only the numeric config. Switching
    # Switching the inner benchmark keeps the same `inner_benchmark_config` knobs but changes
    # what is measured; hashing only the knobs let a d23-banked origin be served against a d234
    # candidate — a stale-vs-fresh comparison that fabricates outer signal (the exact bug this
    # fingerprint exists to prevent).
    inner_tasks = read_yaml_optional(dataset_dir / "inner_tasks.yaml") or {}
    # ...and the inner benchmark's OWN node configs, which the name above does not carry.
    # `inner_tasks.yaml` pins a per-cell `inner_model`/`inner_provider` and those ride the
    # spec, but the DATASET DEFAULT — `datasets/{benchmark}/pipeline.yaml::nodes.*.config`,
    # where the worker model, temperature, reasoning_effort and output schema actually live —
    # escaped entirely. Repointing that model left this fingerprint unchanged, so every banked
    # outer cell still matched: a pp-self run would replay cells measured on the OLD worker
    # and report them as the new one's, having run nothing. Same stale-vs-fresh bug the rest
    # of this fingerprint exists to prevent, one file further down.
    #
    # `config` only, deliberately. `available_models` is a permission list and
    # `optimizer.param_allowed_values` bounds what L1 may PROPOSE — neither changes what the
    # origin does, so widening either must not void a panel that cost an hour to measure.
    # The benchmark resolves as a sibling of the outer dataset dir, which is how every layout
    # ships it (repo `datasets/`, staged `assets/benchmarks/`, tenant root); an unresolvable
    # one hashes as ``None``, which is a distinct input from any real config rather than a
    # silent match.
    benchmark = inner_tasks.get("inner_benchmark")
    inner_pipeline = (
        read_yaml_optional(dataset_dir.parent / str(benchmark) / "pipeline.yaml")
        if benchmark
        else None
    )
    inner_spec = {
        "benchmark": benchmark,
        "config": inner_tasks.get("inner_benchmark_config") or {},
        "tasks": inner_tasks.get("tasks") or [],
        "nodes": (
            {name: (node or {}).get("config") for name, node in inner_pipeline["nodes"].items()}
            if inner_pipeline and isinstance(inner_pipeline.get("nodes"), dict)
            else None
        ),
    }
    fingerprint = stable_hash(
        [origin_manifest, origin_schemas, layouts, panel_text, APP_VERSION, inner_spec]
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
    from promptpotter.application.runner.inner.cycle import run_inner_cycle

    return await run_inner_cycle(query, payload)


CONNECTOR = Connector(
    name="promptpotter",
    execution="in_process",
    wire_adapter=promptpotter_wire_adapter,
    session_factory=PromptPotterSession,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
    # The outer "samples" are the inner tasks — read from this file in the dataset
    # config dir and fed through ``extract_experiment`` at init (no CSV table).
    experiment_file="inner_tasks.yaml",
    identity_config=_identity_config,
)


__all__ = ["CONNECTOR", "PromptPotterSession", "promptpotter_wire_adapter"]
