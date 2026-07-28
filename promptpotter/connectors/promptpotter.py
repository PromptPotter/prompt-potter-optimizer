"""PromptPotter-as-connector — the optimizer-of-the-optimizer.

An outer cycle optimizes an inner cycle's meta-prompts, and each inner cycle is a real
campaign run on a cheap proxy benchmark. The connector stays a THIN adapter: it declares
``execution="in_process"`` — the capability the loop dispatches on — and delegates to
``application/runner/inner/cycle.py``, because the recursion is heavy orchestration and
does not belong in a wire binding. Design: ``docs/specs/l4-outer-loop.md`` § 2.
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
    text (``datasets/_optimizer/pipeline.yaml``), the per-node information-flow
    layouts, the engine version, AND the dataset's WHOLE ``inner_tasks.yaml`` spec —
    the inner benchmark NAME + task list (which bank, which seeds) plus
    ``inner_benchmark_config`` (sample count, round / variant geometry, the
    ``inner_optimizer_temperature`` clamp). Otherwise an outer origin scored under an old origin/config is
    silently reused against candidates run under the new one — a stale-vs-fresh
    comparison that fabricates (or masks) outer signal.
    """
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        optimizer_manifest,
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
    layouts = {name: spec.model_dump(mode="json") for name, spec in sorted(NODE_LAYOUTS.items())}
    # The inner-run config is part of the inner origin's effective behavior, so it
    # joins the fingerprint — changing `inner_optimizer_temperature` (or any geometry
    # knob) invalidates outer-sample rows measured under the prior value instead of
    # reusing them stale (the identity-joined plumbing l4-outer-loop.md § item 5 named).
    # The WHOLE inner spec defines the inner baseline — the benchmark NAME and its task list
    # (which bank + which seeds/cells), not only the numeric config. Switching
    # justlogic-d23 → justlogic-d234 keeps the same `inner_benchmark_config` knobs but changes
    # what is measured; hashing only the knobs let a d23-banked origin be served against a d234
    # candidate — a stale-vs-fresh comparison that fabricates outer signal (the exact bug this
    # fingerprint exists to prevent).
    inner_tasks = read_yaml_optional(dataset_dir / "inner_tasks.yaml") or {}
    inner_spec = {
        "benchmark": inner_tasks.get("inner_benchmark"),
        "config": inner_tasks.get("inner_benchmark_config") or {},
        "tasks": inner_tasks.get("tasks") or [],
    }
    fingerprint = stable_hash([origin_manifest, layouts, APP_VERSION, inner_spec])[:12]
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
      "l1_generate":  {"instruction": "...", "model": "openai/gpt-oss-120b"},
      "l1_critique":  {"negative_critique_framing": "..."},
      "l2_context":   {"refinement_instruction": "..."},
      "l3_plan":      {"replan_trigger": "..."},
    }
    ```

    Each per-node dict overrides template fields on the inner cycle's
    ``datasets/_optimizer/pipeline.yaml`` for that run. A ``model`` key (present only
    when the operator directly set the inner-optimizer model on the carrier node — a
    cap-gated babysit override, never the optimizer's own search) rides the same
    channel untouched and is consumed at the inner optimizer's ``llm_call`` config
    merge — it is not a ``PromptTemplate`` field, so the prose merge ignores it.
    ``node_config_items`` owns the "reserved key or non-dict" question for every
    adapter (matching the TermNorm convention).
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
    runner emits (``inner:{query}``, ``runner/inner/cycle.py::run_inner_cycle``
    — which appends a compact outcome suffix to its prediction; keep the prefix
    in sync). Nothing matches predicted against ground_truth at the outer level
    (hit is ``fitness >= 1.0`` from the proxy formula) — the token exists so the
    outer optimizer stops reading every sample as a label-miss ("node fails 100%
    — reduce parsing errors"), which is false evidence for a proxy-scored sample.

    There is no target score to match against either: ``inner_tasks.yaml`` declares
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
    ``application/runner/inner/cycle.py`` (the connector stays a thin adapter).
    That runner calls ``run_optimization`` in its **own asyncio task** (so the
    per-task ContextVars — ``_CYCLE_LEDGER`` / ``_CURRENT_ROUND`` / ``_ABORT_CHECK``
    — don't clobber the outer's) under a store sandbox rooted at
    ``<workspace>/.inner/<this cycle_id>/`` — a FLAT registry, named by this cycle but
    never nested under it, so the tree stays shallow at every depth and L5+ nests
    logically rather than on disk (physical nesting blew Windows' ``MAX_PATH``). The spawning
    cycle's context is published by the runner seam (``publish_inner_spawn_context``)
    so this context-free hook can find where to sandbox + which inner benchmark to
    run."""
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
    # config dir and fed through ``extract_experiment`` at bootstrap (no CSV table).
    experiment_file="inner_tasks.yaml",
    identity_config=_identity_config,
)


__all__ = ["CONNECTOR", "PromptPotterSession", "promptpotter_wire_adapter"]
