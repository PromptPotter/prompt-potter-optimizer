"""Projections from a :class:`DraftCampaign` to the files and wire shapes the commit +
new-campaign UI need — no launch side effects, no JobRegistry, no asyncio.

One read of the tenant workspace, not pure: the per-model capability layers
(``infrastructure/llm/capabilities``). It is passed in as a path rather than reached for, so a
caller with no workspace simply gets no capability block instead of a fabricated one."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter import connectors
from promptpotter.application.campaign_config import CampaignConfig, freeze_campaign_config
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    default_campaign_config,
)
from promptpotter.application.datasets.origin_readiness import origin_readiness
from promptpotter.application.pipeline_resolve import resolve_pipeline_for_draft
from promptpotter.domain.pipeline_schema import (
    CANDIDATE_LIBRARY,
    PipelineDependency,
    dependencies_from_node_types,
)
from promptpotter.domain.search_point import TaskDecomposition


def overlay_from_campaign_config(config: CampaignConfig) -> dict[str, Any]:
    """The INVERSE of :func:`split_overlay`: a frozen campaign's own config back as a node overlay,
    so a reused origin seeds what it RAN rather than what the shared dataset file says today. The
    seeded draft freezes back through `split_overlay` at Start, so the pair must lose nothing."""
    overlay: dict[str, Any] = {}
    for node, block in config.pipeline_overlay.items():
        if isinstance(block, dict) and block:
            overlay.setdefault(node, {})["config"] = dict(block)
    for node, narrowing in config.optimizer_narrowing.items():
        optimizer: dict[str, Any] = {}
        # `param_keys` is None-able and `[]` is a real answer (every axis closed), so the test is
        # `is not None` — `or` would drop a campaign that deliberately closed the whole node.
        if narrowing.param_keys is not None:
            optimizer["param_keys"] = list(narrowing.param_keys)
        if narrowing.param_allowed_values:
            optimizer["param_allowed_values"] = {
                k: list(v) for k, v in narrowing.param_allowed_values.items()
            }
        if optimizer:
            overlay.setdefault(node, {})["optimizer"] = optimizer
    return overlay


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
    """The draft's half of the ONE resolution, reshaped for the wire.

    It COMPUTES nothing: ``resolve_pipeline_for_draft`` is the same function
    ``GET /campaigns/{id}/pipeline`` serves for a check-in, so the ingest surface and the campaign
    route cannot answer differently about the draft between them. It used to parse and narrow the
    manifest itself, which meant every ingest row came back ``source: "unset"`` with no merge
    behind it, and the operator's own narrowing reached the editor only through a browser-side
    derivation."""
    resolution = resolve_pipeline_for_draft(
        draft,
        campaign_id=draft.draft_id,
        cycle_id="",
        workspace=workspace,
    )
    return {
        "pipeline_view": resolution.view,
        "node_config_schema": resolution.node_config_schema,
        "node_output_schema": resolution.node_output_schema,
        "reach": {node: r.model_dump() for node, r in resolution.reach.items()},
        "model_capabilities": resolution.model_capabilities,
        "is_single_node": resolution.is_single_node,
        "schema_source": _draft_schema_source(draft),
    }


def _draft_schema_source(draft: DraftCampaign) -> str:
    """WHY the axes read as they do, so an empty axis set is never mistaken for a locked one.
    `backend` = the service's own declaration was captured; `local` = an in-process connector,
    whose manifest IS the declaration; `unreachable` = the probe failed, and nothing here may be
    read as a lock the operator set. A DRAFT fact, so it rides the draft wire and not the
    resolution — a campaign read has a schema whoever answered for it."""
    if draft.backend_nodes:
        return "backend"
    return (
        "local"
        if connectors.CONNECTORS[draft.connector].in_process_run is not None
        else "unreachable"
    )


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
    rename cannot make the file unreadable — which matters because ``CampaignConfig`` forbids extras.

    The node overlay is deliberately NOT folded in here: the mint splits it onto the per-campaign
    snapshot at launch (``_campaign_config_for_launch``), which is what leaves a REUSED dataset's
    shared file untouched."""
    return {"campaign_config": freeze_campaign_config(default_campaign_config(draft))}


def _build_task_context(draft: DraftCampaign) -> dict[str, Any]:
    """The check-in already decomposed the task, so the run reads ``task_context.yaml`` directly
    instead of re-decomposing through a second LLM call."""
    return TaskDecomposition.from_dict(
        {**draft.decomposed_task_context, "raw_description": draft.raw_task_description}
    ).to_dict()
