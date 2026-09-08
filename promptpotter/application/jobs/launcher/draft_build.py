"""Projections from a :class:`DraftCampaign` to the files and wire shapes the commit +
new-campaign UI need — no launch side effects, no JobRegistry, no asyncio.

One read of the tenant workspace, not pure: the per-model capability layers
(``infrastructure/llm/capabilities``). It is passed in as a path rather than reached for, so a
caller with no workspace simply gets no capability block instead of a fabricated one."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter import connectors
from promptpotter.application.campaign_config import freeze_campaign_config, load_campaign_config
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    merge_pipeline_overlay,
    resolved_node_schema,
)
from promptpotter.application.datasets.origin_readiness import origin_readiness
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    CANDIDATE_LIBRARY,
    NodeSearchNarrowing,
    PipelineDependency,
    dependencies_from_node_types,
)
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.llm.capabilities import resolve_menu


def _origin_pipeline_json(draft: DraftCampaign, nodes: dict[str, Any]) -> dict[str, Any]:
    """*nodes* is the caller's choice of layer depth, and the two callers deliberately differ:
    the committed file gets :func:`merge_pipeline_overlay` (an OVERLAY — the backend still owns
    the schema at run time), the rendered one gets :func:`resolved_node_schema` (the backend's
    declaration underneath it, because ``param_keys`` lives nowhere else). Passing it in rather
    than branching inside is what keeps "what we write" and "what we draw" from drifting into
    one flag nobody can read. ``pipelines.default`` overrides the pipeline order."""
    pipeline: dict[str, Any] = {
        "name": draft.slug,
        "backend_type": draft.connector,
        "backend_name": draft.connector,
    }
    connector = connectors.get(draft.connector)
    steps = draft.pipeline_steps or list(connector.default_pipeline)
    if steps:
        pipeline["pipelines"] = {"default": list(steps)}
    # The model MENU, from the one function that feeds both the committed file and the
    # pre-commit render — so a check-in dataset gets the same catalogue a hand-authored
    # benchmark declares, instead of the empty list that leaves its model list with nothing
    # to offer. The ADMIN's catalogue and nothing else: a model the operator typed rides
    # `nodes.{n}.optimizer.param_allowed_values.model`, which is what BOUNDS the run
    # (`PipelineSchema.model_options` prefers it), and folding it in here would erase the one
    # difference that lets a surface say which values are theirs. Absent when the connector
    # declares none: no menu is a real answer.
    if connector.available_models:
        pipeline["available_models"] = list(connector.available_models)

    if nodes:
        pipeline["nodes"] = nodes
    return pipeline


def _build_origin_pipeline_json(draft: DraftCampaign) -> dict[str, Any]:
    """What gets COMMITTED as ``datasets/{slug}/pipeline.yaml``."""
    return _origin_pipeline_json(
        draft, merge_pipeline_overlay(draft, connectors.get(draft.connector))
    )


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


def draft_active_steps(draft: DraftCampaign) -> list[str]:
    """The pipeline this draft actually runs — its own choice (preserved on reuse) over the
    connector default, so the UI shows the dataset's real pipeline and not `llm_only`."""
    connector = connectors.get(draft.connector)
    return draft.pipeline_steps or list(connector.default_pipeline)


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


def _draft_pipeline_render(draft: DraftCampaign, workspace: Path | None) -> dict[str, Any]:
    """A check-in has no committed ``datasets/{slug}/``, so its pipeline is read off the draft, not
    disk — the ingest node editor renders with no fetch-by-slug and no second endpoint."""
    connector = connectors.get(draft.connector)
    schema = parse_pipeline_response(
        _origin_pipeline_json(draft, resolved_node_schema(draft, connector))
    )
    cfg = load_campaign_config(_build_default_campaign_json(draft)["campaign_config"])
    schema = schema.narrow(cfg.optimizer_narrowing)
    return {
        "pipeline_view": schema.view.model_dump(by_alias=True) if schema.view is not None else None,
        "node_config_schema": schema.node_config_schema(),
        "node_output_schema": schema.node_output_schemas(),
        # Every model on the MENU, not only the picked one: switching models must re-answer the
        # reasoning ladder with no round-trip, which is what keeps the surface honest while the
        # operator is still deciding.
        "model_capabilities": {
            m: c.model_dump()
            for m, c in resolve_menu(schema.available_models, workspace=workspace).items()
        },
        # WHY the axes read as they do, so an empty answer is never mistaken for a locked one.
        # A remote connector with no captured declaration means the probe failed, and the editor
        # must say "axes unknown" rather than draw a padlock nobody set.
        "schema_source": (
            "backend"
            if draft.backend_nodes
            else "local"
            if connector.in_process_run is not None
            else "unreachable"
        ),
    }


def draft_wire(draft: DraftCampaign, workspace: Path | None = None) -> dict[str, Any]:
    """``readiness`` is the **server-authoritative** mint gate, recomputed on every draft response —
    the UI gates Start on it, never on a client re-derivation that would drift.

    *workspace* is the tenant's own root, and only the per-model capability block needs it. Omit
    it and that block is empty, which every reader must render as UNKNOWN."""
    readiness = origin_readiness(draft)
    return {
        **draft.to_wire(),
        "active_steps": draft_active_steps(draft),
        **_draft_pipeline_render(draft, workspace),
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
        }
    )
    return {"campaign_config": freeze_campaign_config(config)}


def _build_task_context(draft: DraftCampaign) -> dict[str, Any]:
    """The check-in already decomposed the task, so the run reads ``task_context.yaml`` directly
    instead of re-decomposing through a second LLM call."""
    return TaskDecomposition.from_dict(
        {**draft.decomposed_task_context, "raw_description": draft.raw_task_description}
    ).to_dict()
