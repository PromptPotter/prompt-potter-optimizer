"""PromptPotter-as-connector — the optimizer-of-the-optimizer.

Self-referential connector: an outer PromptPotter cycle optimizes an inner
PromptPotter cycle's meta-prompts (``l1_generate`` / ``l1_critique`` /
``l2_context`` / ``l3_plan``). Each inner cycle is a real campaign run on a
cheap proxy benchmark; the outer L1's mutation surface is the inner
meta-prompt template fields, exposed via ``pipeline_params``.

See ``docs/specs/roadmap.md`` for the full design — five-hook contract, the composed
inner-cycle proxy vector (:class:`~promptpotter.domain.outer_verdict.OuterSampleProxies`),
inner isolation under ``.runtime/inner/``, cost-realism warning.

The five hooks are wired to the protocol; ``promptpotter_wire_adapter`` shapes
the inner-cycle payload; ``PromptPotterSession`` is the in-process noop session.
The connector declares ``execution="in_process"`` — the capability the loop
dispatches on. ``_in_process_run`` delegates to the inner-cycle runner in
``application/runner/inner_recursion.py`` (the recursion is heavy orchestration;
the connector stays a thin adapter), which mints + runs a sandboxed inner campaign
in its own asyncio task and returns the three proxy metrics. Decided in
``docs/specs/l4-outer-loop.md`` § 2 (in-process recursion, re-entrant isolation).

Exports the ``CONNECTOR`` binding consumed by
:data:`promptpotter.connectors.CONNECTORS`.
"""

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
# tunable — the adapter strips it before building ``meta_prompt_overrides``.
INNER_ORIGIN_KEY = "inner_origin"


def _identity_config(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    """The inner optimizer's effective-revision fingerprint, as identity config.

    The backend this connector runs IS the inner optimizer, so a measurement's
    identity must change whenever the inner origin does: the shared meta-prompt
    text (``datasets/_optimizer/pipeline.json``), the per-node information-flow
    layouts, the engine version, AND the dataset's ``inner_tasks.json``
    ``inner_benchmark_config`` (the inner-run behavior — sample count, round /
    variant geometry, target, and the ``inner_optimizer_temperature`` determinism
    clamp). Otherwise an outer origin scored under an old origin/config is
    silently reused against candidates run under the new one — a stale-vs-fresh
    comparison that fabricates (or masks) outer signal.
    """
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        OPTIMIZER_PIPELINE_PATH,
    )
    from promptpotter.config.settings import APP_VERSION
    from promptpotter.domain.l1_layout import NODE_LAYOUTS
    from promptpotter.domain.pipeline_schema import stable_hash
    from promptpotter.infrastructure.store.io import read_json_optional

    origin_text = OPTIMIZER_PIPELINE_PATH.read_text(encoding="utf-8")
    layouts = {name: spec.model_dump(mode="json") for name, spec in sorted(NODE_LAYOUTS.items())}
    # The inner-run config is part of the inner baseline's effective behavior, so it
    # joins the fingerprint — changing `inner_optimizer_temperature` (or any geometry
    # knob) invalidates outer-sample rows measured under the prior value instead of
    # reusing them stale (the identity-joined plumbing l4-outer-loop.md § item 5 named).
    inner_cfg = (read_json_optional(dataset_dir / "inner_tasks.json") or {}).get(
        "inner_benchmark_config"
    ) or {}
    fingerprint = stable_hash([origin_text, layouts, APP_VERSION, inner_cfg])[:12]
    return {"l1_generate": {INNER_ORIGIN_KEY: fingerprint}}


# ---------------------------------------------------------------------------
# Wire payload shape
# ---------------------------------------------------------------------------


def promptpotter_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Outbound payload describing an inner PromptPotter cycle to run.

    ``query`` is the inner-benchmark task identifier (e.g.
    ``"justlogic-d67/seed-0"``). ``pipeline_params`` carries the outer L1's
    mutation surface — a nested dict keyed by inner-meta-prompt node:

    ```
    {
      "l1_generate":  {"instruction": "...", "decomposition_hint": "..."},
      "l1_critique":  {"negative_critique_framing": "..."},
      "l2_context":   {"refinement_instruction": "..."},
      "l3_plan":      {"replan_trigger": "..."},
    }
    ```

    Each per-node dict overrides template fields on the inner cycle's
    ``datasets/_optimizer/pipeline.json`` for that run. ``node_config_items`` owns the
    "reserved key or non-dict" question for every adapter (matching the TermNorm
    convention).
    """
    payload: dict[str, Any] = {"query": query}

    meta_prompt_overrides: dict[str, dict[str, Any]] = {}
    for k, v in node_config_items(pipeline_params):
        # The inner-origin fingerprint is identity config, not an override —
        # the inner loop must never see it as a template field.
        stripped = {fk: fv for fk, fv in v.items() if fk != INNER_ORIGIN_KEY}
        if stripped:
            meta_prompt_overrides[k] = stripped

    if meta_prompt_overrides:
        payload["meta_prompt_overrides"] = meta_prompt_overrides

    return payload


# ---------------------------------------------------------------------------
# Session lifecycle (in-process noop)
# ---------------------------------------------------------------------------


class PromptPotterSession:
    """In-process noop session — implements ``SessionProtocol``.

    PromptPotter-as-connector has no remote service, so there's no
    handshake. ``set_terms`` and ``recover`` are no-ops.
    """

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
    """Inner-benchmark tasks → ``(queries, index_terms)``.

    PromptPotter-self ``experiment_data`` is a small JSON describing the
    inner-benchmark suite: a list of task identifiers + their target
    scores. ``queries`` contains one item per inner task; ``index_terms``
    is empty (no retrieval index on this connector).

    Shape::

        {
          "tasks": [
            {"id": "justlogic-d67/seed-0", "inner_dataset_seed": 0},
            ...
          ]
        }

    **There is no label to match in L4.** The "sample" is an inner campaign
    whose fitness is the proxy composite (``campaign.json::scoring``), not a
    correct answer. So ``ground_truth`` is the inner-result token PREFIX the
    runner emits (``inner:{query}``, ``runner/inner_recursion.py::run_inner_cycle``
    — which appends a compact outcome suffix to its prediction; keep the prefix
    in sync). Nothing matches predicted against ground_truth at the outer level
    (hit is ``fitness >= 1.0`` from the proxy formula) — the token exists so the
    outer optimizer stops reading every sample as a label-miss ("node fails 100%
    — reduce parsing errors"), which is false evidence for a proxy-scored sample.

    There is no target score to match against either: ``inner_tasks.json`` declares
    what an inner cycle may SPEND, never what it is expected to REACH.
    """
    tasks = experiment_data.get("tasks", [])
    queries: list[dict[str, Any]] = []
    for t in tasks:
        tid = t.get("id")
        if not tid:
            continue
        queries.append({"query": tid, "ground_truth": f"inner:{tid}"})
    return queries, []


async def _in_process_run(query: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run an inner PromptPotter cycle and return its three proxy metrics — the L4
    arm of the shared ``in_process`` seam.

    Thin delegate: the recursion is heavy orchestration, so it lives in
    ``application/runner/inner_recursion.py`` (the connector stays a thin adapter).
    That runner calls ``run_optimization`` in its **own asyncio task** (so the
    per-task ContextVars — ``_CYCLE_LEDGER`` / ``_CURRENT_ROUND`` / ``_ABORT_CHECK``
    — don't clobber the outer's) under a store sandbox rooted at *this* cycle's
    ``.runtime/inner/`` (re-entrant, so L5+ nests by construction). The spawning
    cycle's context is published by the runner seam (``publish_inner_spawn_context``)
    so this context-free hook can find where to sandbox + which inner benchmark to
    run."""
    from promptpotter.application.runner.inner_recursion import run_inner_cycle

    return await run_inner_cycle(query, payload)


CONNECTOR = Connector(
    name="promptpotter",
    execution="in_process",
    wire_adapter=promptpotter_wire_adapter,
    session_factory=PromptPotterSession,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
    # The outer "samples" are the inner tasks — read from this file in the dataset
    # config dir and fed through ``extract_experiment`` at bootstrap (no CSV table).
    experiment_file="inner_tasks.json",
    identity_config=_identity_config,
)


__all__ = ["CONNECTOR", "PromptPotterSession", "promptpotter_wire_adapter"]
