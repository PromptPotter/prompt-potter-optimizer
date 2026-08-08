"""Pure projections from a :class:`DraftCampaign` to the files and wire shapes the commit +
new-campaign UI need — no launch side effects, no JobRegistry, no asyncio."""

from __future__ import annotations

from typing import Any

from promptpotter import connectors
from promptpotter.application.campaign_config import freeze_campaign_config, load_campaign_config
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    merge_pipeline_overlay,
)
from promptpotter.application.datasets.origin_readiness import origin_readiness
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    CANDIDATE_LIBRARY,
    NodeSearchNarrowing,
    PipelineDependency,
    dependencies_from_node_types,
)
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS, TaskDecomposition


def _build_origin_pipeline_json(draft: DraftCampaign) -> dict[str, Any]:
    """The committed file is the dataset's ``pipeline.yaml`` OVERLAY; the backend's live
    ``GET /pipeline`` is the actual schema. ``pipelines.default`` overrides the pipeline order."""
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


def split_overlay(
    pipeline_overlay: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, NodeSearchNarrowing]]:
    """A reused dataset's mint applies this split onto the per-campaign snapshot, so the shared,
    immutable dataset is never mutated; a fresh upload folds the whole overlay into its own file."""
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
    """Makes the connector defaults visible BEFORE commit — a draft's ``pipeline_overlay`` is empty
    until then, so without this the UI cannot show the optimizer is LOCKED OUT of an axis."""
    connector = connectors.get(draft.connector)
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
        "forbidden_axes": sorted(PARAM_FORBIDDEN_KEYS),
        "nodes": node_locks,
    }


def draft_pipeline_dependencies(draft: DraftCampaign) -> tuple[PipelineDependency, ...]:
    """Scoped to the ACTIVE steps, so a dependency surfaces only when a node needing it runs —
    TermNorm's ``llm_only`` default raises none, the full pipeline raises ``candidate_library``."""
    connector = connectors.get(draft.connector)
    active = set(draft.pipeline_steps or connector.default_pipeline)
    node_types = {n: t for n, t in connector.node_types.items() if n in active}
    return dependencies_from_node_types(node_types)


def _dependency_fulfilled(dep: PipelineDependency, draft: DraftCampaign) -> bool:
    if dep.kind == CANDIDATE_LIBRARY:
        return bool(draft.candidate_library)
    return False


def _draft_pipeline_render(draft: DraftCampaign) -> dict[str, Any]:
    """A check-in has no committed ``datasets/{slug}/``, so its pipeline is read off the draft, not
    disk — the ingest node editor renders with no fetch-by-slug and no second endpoint."""
    schema = parse_pipeline_response(_build_origin_pipeline_json(draft))
    cfg = load_campaign_config(_build_default_campaign_json(draft)["campaign_config"])
    schema = schema.narrow(cfg.optimizer_narrowing)
    return {
        "pipeline_view": schema.view.model_dump(by_alias=True) if schema.view is not None else None,
        "node_config_schema": schema.node_config_schema(),
        "node_output_schema": schema.node_output_schemas(),
    }


def draft_wire_with_locks(draft: DraftCampaign) -> dict[str, Any]:
    """``readiness`` is the **server-authoritative** mint gate, recomputed on every draft response —
    the UI gates Start on it, never on a client re-derivation that would drift."""
    readiness = origin_readiness(draft)
    return {
        **draft.to_wire(),
        "optimizer_locks": derive_optimizer_locks(draft),
        **_draft_pipeline_render(draft),
        "dependencies": [
            {**dep.model_dump(), "fulfilled": _dependency_fulfilled(dep, draft)}
            for dep in draft_pipeline_dependencies(draft)
        ],
        "readiness": {
            "complete": readiness.complete,
            "gaps": [gap.to_wire() for gap in readiness.gaps],
        },
    }


def _build_default_campaign_json(draft: DraftCampaign) -> dict[str, Any]:
    """Written as the DELTA from defaults, so a knob nobody chose never reaches disk and a later
    rename cannot make the file unreadable — which matters because ``CampaignConfig`` forbids extras."""
    connector = connectors.get(draft.connector)
    overrides = draft.optimization_overrides
    optimization: dict[str, Any] = {"max_rounds": overrides["max_rounds"]}
    optimization.update(dict(connector.default_optimization))
    optimization["prompt_block_catalogue"] = overrides["prompt_block_catalogue"]
    optimization["mechanisms"] = dict(overrides["mechanisms"])
    config = load_campaign_config(
        {
            "dataset_name": draft.slug,
            "scoring": f"{draft.scoring_composite}(predicted, ground_truth)",
            "exclude_nodes": list(connector.default_exclude_nodes),
            "optimization": optimization,
            **({"allowed_models": list(draft.allowed_models)} if draft.allowed_models else {}),
        }
    )
    return {"campaign_config": freeze_campaign_config(config)}


def _build_task_context(draft: DraftCampaign) -> dict[str, Any]:
    """The check-in already decomposed the task, so the run reads ``task_context.yaml`` directly
    instead of re-decomposing through a second LLM call."""
    return TaskDecomposition.from_dict(
        {**draft.decomposed_task_context, "raw_description": draft.raw_task_description}
    ).to_dict()
