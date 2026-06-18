"""Draft → on-disk artifact builders for the launcher commit path.

Pure projections from a :class:`DraftCampaign` to the files / wire shapes the
commit + new-campaign UI need — no launch side effects, no JobRegistry, no
asyncio. Split off :mod:`.core` so the launch / commit orchestration stays
readable; the core module imports these.

* ``_build_origin_pipeline_json`` / ``_build_default_campaign_json`` /
  ``_build_task_context`` — the committed dataset's ``pipeline.json`` overlay,
  ``campaign.json`` sibling, and ``task_context.json`` framing.
* ``merge_pipeline_overlay`` — connector node-config seed + operator edits.
* ``split_overlay`` — split a reused-dataset overlay into its two
  campaign-config homes (``pipeline_overrides`` + ``optimizer_narrowing``).
* ``derive_optimizer_locks`` / ``draft_pipeline_dependencies`` /
  ``draft_wire_with_locks`` — the new-campaign permission + dependency surface.
"""

from __future__ import annotations

import copy
from typing import Any

from promptpotter import connectors
from promptpotter.application.datasets.draft_campaign import DraftCampaign
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.pipeline_schema import (
    CANDIDATE_LIBRARY,
    NodeSearchNarrowing,
    PipelineDependency,
    dependencies_from_node_types,
)
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS, TaskDecomposition


def _build_origin_pipeline_json(draft: DraftCampaign) -> dict[str, Any]:
    """Slice-1 pipeline overlay seeded from the connector's first-tenant default.

    The committed file is the dataset's ``pipeline.json`` overlay; the
    backend's live ``GET /pipeline`` response is the actual schema.
    ``backend_type`` is mandatory for connector resolution
    (``_read_backend_type`` reads it on bootstrap); ``pipelines.default``
    overrides the backend's pipeline order per the merge contract in
    ``application/bootstrap/wiring.py::_apply_dataset_overlay``.

    The step list is the draft's chosen pipeline (``draft.pipeline_steps`` —
    preserved when reusing an existing dataset) and falls back to
    :attr:`Connector.default_pipeline` for a fresh upload. The launcher carries
    no hard-coded ``["llm_only"]``; connectors that leave the field empty inherit
    the backend's own default.
    """
    pipeline: dict[str, Any] = {
        "name": draft.slug,
        "backend_type": draft.connector,
        "backend_name": draft.connector,
    }
    connector = connectors.get(draft.connector)
    steps = draft.pipeline_steps or list(connector.default_pipeline)
    if steps:
        pipeline["pipelines"] = {"default": list(steps)}

    nodes = merge_pipeline_overlay(draft, connector)
    if nodes:
        pipeline["nodes"] = nodes
    return pipeline


def merge_pipeline_overlay(draft: DraftCampaign, connector: Connector) -> dict[str, Any]:
    """Connector node-config seed (e.g. TermNorm's reasoning clamp) underneath,
    operator draft edits on top — the effective ``pipeline.json::nodes`` block.

    Sub-blocks (``config`` / ``optimizer``) shallow-merge per node so an operator
    override narrows the seed rather than replacing the whole node. Shared by the
    committed pipeline.json builder and the wire-side optimizer-locks block so
    the two never drift.
    """
    nodes: dict[str, Any] = copy.deepcopy(dict(connector.default_node_config))
    for node_name, node_overlay in (draft.pipeline_overlay or {}).items():
        dst = nodes.setdefault(node_name, {})
        for key, val in node_overlay.items():
            if isinstance(val, dict) and isinstance(dst.get(key), dict):
                dst[key].update(val)
            else:
                dst[key] = val
    return nodes


def split_overlay(
    pipeline_overlay: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, NodeSearchNarrowing]]:
    """Split a draft ``pipeline_overlay`` into its two campaign-config homes.

    The lock editor writes one transport shape — ``nodes.{n}.{config, optimizer}``.
    Each node's ``config`` block is the origin-floor value override
    (→ ``CampaignConfig.pipeline_overrides``); its ``optimizer`` block
    (``param_keys`` subset + narrowed ``param_allowed_values``) is the
    search-space narrowing (→ ``CampaignConfig.optimizer_narrowing``). A reused
    dataset's mint applies this split onto the per-campaign snapshot so the
    shared, immutable dataset is never mutated. Fresh uploads instead fold the
    whole overlay into the committed ``pipeline.json`` (``merge_pipeline_overlay``)
    — the dataset is theirs to author."""
    overrides: dict[str, Any] = {}
    narrowing: dict[str, NodeSearchNarrowing] = {}
    for node, block in pipeline_overlay.items():
        if not isinstance(block, dict):
            continue
        config = block.get("config")
        if isinstance(config, dict) and config:
            overrides[node] = dict(config)
        optimizer = block.get("optimizer")
        if isinstance(optimizer, dict) and optimizer:
            narrowing[node] = NodeSearchNarrowing(
                param_keys=optimizer.get("param_keys"),
                param_allowed_values=optimizer.get("param_allowed_values", {}),
            )
    return overrides, narrowing


def derive_optimizer_locks(draft: DraftCampaign) -> dict[str, Any]:
    """The backend-pipeline permission surface the new-campaign UI renders.

    Makes the otherwise-hidden connector defaults visible *before* commit: the
    default pipeline, the per-node config floor + the ``param_allowed_values``
    the optimizer may permute, and the campaign-wide forbidden axes
    (``model``/``provider`` under ``forbidden_axes_strict``). A draft's
    ``pipeline_overlay`` is empty until commit, so without this the UI couldn't
    show that the optimizer is *locked out* of escalating these — not merely
    that ``low`` is a default. Mirrors the commit-time merge via
    :func:`merge_pipeline_overlay`.
    """
    connector = connectors.get(draft.connector)
    forbidden_strict = draft.lock_model
    # The active pipeline is the permission surface — the optimizer can only move
    # nodes that actually run. Scope the per-node locks to it so the panel shows
    # only the dataset's real nodes (not every node the backend has registered,
    # e.g. llm_only / direct_prompt for a Research+Match dataset).
    steps = draft.pipeline_steps or list(connector.default_pipeline)
    active = set(steps)
    node_locks: dict[str, Any] = {}
    for node_name, overlay in merge_pipeline_overlay(draft, connector).items():
        if active and node_name not in active:
            continue
        optimizer = overlay.get("optimizer", {})
        node_locks[node_name] = {
            "config": dict(overlay.get("config", {})),
            "param_allowed_values": dict(optimizer.get("param_allowed_values", {})),
        }
    return {
        # The draft's chosen pipeline (preserved on reuse) over the connector
        # default — so the UI shows the dataset's real pipeline, not llm_only.
        "pipeline": steps,
        "forbidden_axes": sorted(PARAM_FORBIDDEN_KEYS) if forbidden_strict else [],
        "nodes": node_locks,
    }


def draft_pipeline_dependencies(draft: DraftCampaign) -> tuple[PipelineDependency, ...]:
    """The categorical inputs the draft's ACTIVE pipeline requires, read off the
    connector's static ``node_types`` for the chosen steps.

    Scoped to the active steps (operator override, else the connector default) so a
    dependency surfaces only when a node that needs it actually runs — TermNorm's
    ``llm_only`` default raises none; selecting the full pipeline (with
    ``token_matching``) raises ``candidate_library``. Shares the live
    :func:`dependencies_from_node_types` mapping."""
    connector = connectors.get(draft.connector)
    active = set(draft.pipeline_steps or connector.default_pipeline)
    node_types = {n: t for n, t in connector.node_types.items() if n in active}
    return dependencies_from_node_types(node_types)


def _dependency_fulfilled(dep: PipelineDependency, draft: DraftCampaign) -> bool:
    """Whether the draft already carries the input ``dep`` asks for."""
    if dep.kind == CANDIDATE_LIBRARY:
        return bool(draft.candidate_library)
    return False


def draft_wire_with_locks(draft: DraftCampaign) -> dict[str, Any]:
    """``DraftCampaign.to_wire()`` plus the connector-derived ``optimizer_locks``
    and ``dependencies`` blocks.

    The single wire shape every draft-returning endpoint emits — keeps
    :meth:`DraftCampaign.to_wire` pure (no connector import) and adds the
    connector-derived blocks once at the I/O boundary. ``dependencies`` carries
    each required input + whether it's ``fulfilled``, so the ingest UI shows the
    operator which input is missing and offers a drop in place.
    """
    return {
        **draft.to_wire(),
        "optimizer_locks": derive_optimizer_locks(draft),
        "dependencies": [
            {**dep.model_dump(), "fulfilled": _dependency_fulfilled(dep, draft)}
            for dep in draft_pipeline_dependencies(draft)
        ],
    }


def _build_default_campaign_json(draft: DraftCampaign) -> dict[str, Any]:
    """Default-campaign sibling — valid :class:`CampaignConfig` wrapped in the
    on-disk ``campaign_config`` outer key per the repo convention
    (see ``datasets/{benchmark}/campaign.json``).

    Per R4, ``exclude_nodes`` and the ``optimization`` knob overrides come
    from the connector (:attr:`Connector.default_exclude_nodes` +
    :attr:`Connector.default_optimization`) — the launcher no longer
    hard-codes ``["llm_ranking"]`` or ``n_variants=3``. Connectors that
    leave the fields empty get the schema defaults.
    """
    connector = connectors.get(draft.connector)
    optimization: dict[str, Any] = {"max_rounds": draft.max_rounds}
    optimization.update(dict(connector.default_optimization))
    # The operator's model-lock choice overrides the connector default —
    # mirrors derive_optimizer_locks so the committed campaign matches the panel.
    optimization["forbidden_axes_strict"] = draft.lock_model
    # The operator's mechanism-toggle choices ride straight onto the committed
    # campaign.json (sorting/selection + early-abort groups), like max_rounds.
    optimization["mechanisms"] = dict(draft.mechanisms)
    return {
        "campaign_config": {
            "dataset_name": draft.slug,
            "scoring": f"{draft.scoring_composite}(predicted, ground_truth)",
            "exclude_nodes": list(connector.default_exclude_nodes),
            "optimization": optimization,
        },
    }


def _build_task_context(draft: DraftCampaign) -> dict[str, Any]:
    """The run-start domain framing, written to ``task_context.json``.

    The check-in already decomposed the task into the 7-field ``task_context``
    (carried on :attr:`DraftCampaign.decomposed_task_context`); normalize it through
    :class:`TaskDecomposition` with the verbatim ``raw_description`` so the run
    reads it directly instead of re-decomposing via a second LLM call."""
    return TaskDecomposition.from_dict(
        {**draft.decomposed_task_context, "raw_description": draft.raw_task_description}
    ).to_dict()
