from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from promptpotter import connectors
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    OptimizationOverrides,
    default_campaign_config,
)
from promptpotter.application.datasets.origin_readiness import OriginReadiness, origin_readiness
from promptpotter.application.pipeline_resolve import resolve_pipeline_for_draft
from promptpotter.application.scoring.formula import SCORING_FUNCTIONS
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_schema import (
    CANDIDATE_LIBRARY,
    CapabilityMenu,
    ManifestNodeOverlay,
    NodeConfigParam,
    NodeOutputSchema,
    NodeReach,
    PipelineDependency,
    PipelineView,
    dependencies_from_node_roles,
)
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.strict_model import StrictModel

SchemaSource = Literal["backend", "local", "unreachable"]


class DraftDependency(PipelineDependency):
    """One input the draft's active pipeline needs. Soft: unfulfilled, it does not block mint."""

    fulfilled: bool


class DraftCampaignWire(StrictModel):
    """A check-in campaign's draft as every check-in route answers it. ``draft_id`` IS the owning
    ``campaign_id``; no ``tenant_id`` rides it (ADR-0002 no-drift gate #3)."""

    draft_id: str
    slug: str
    sample_preview: list[dict[str, str]] = Field(
        description="The head of the upload, keyed by the RAW headers and never projected through "
        "the column mapping, which is empty until confirmed: render against `headers`."
    )
    n_samples: int
    connector: str
    scoring_matcher: str
    scoring_matchers: list[str] = Field(
        description="The matchers a check-in may pick between — the compiler's own set."
    )
    scoring_dials: str = Field(
        description="`term=weight` dials joined by commas; empty scores correctness alone."
    )
    optimization_overrides: OptimizationOverrides
    raw_task_description: str
    pipeline_overlay: dict[str, Any]
    headers: list[str]
    column_query: str
    column_ground_truth: str
    field_provenance: dict[str, Provenance] = Field(
        description="By checklist field id. Nothing reaches mint until `confirmed`."
    )
    origin_prompt_fields: dict[str, Any] = Field(
        description="`OptSearchPoint.prompt_field_dict()` shape; empty until the check-in fills it."
    )
    candidate_library_size: int = Field(
        description="A count, not the list: a library runs to tens of thousands of entries."
    )
    created_at: str
    updated_at: str
    active_steps: list[str] = Field(
        description="The pipeline this draft runs. Who may move a node's axes is "
        "`node_config_schema`'s answer alone."
    )
    pipeline_view: PipelineView | None = Field(
        description="`GET /campaigns/{id}/pipeline`'s answer, computed from the draft: a "
        "pre-commit check-in has no dataset dir, so its pipeline is never fetched by slug."
    )
    node_config_schema: dict[str, list[NodeConfigParam]]
    node_output_schema: dict[str, NodeOutputSchema | None]
    reach: dict[str, NodeReach]
    is_single_node: bool
    schema_source: SchemaSource = Field(
        description="WHY the axes read as they do. `unreachable` = the backend probe failed, so "
        "nothing in the schema is a lock anyone set and an editor draws no padlock off it."
    )
    model_capabilities: CapabilityMenu = Field(
        description="A null `reasoning_efforts` is UNKNOWN, never unsupported."
    )
    dependencies: list[DraftDependency]
    readiness: OriginReadiness = Field(
        description="The server's mint gate, recomputed on every draft response."
    )


def overlay_from_campaign_config(config: CampaignConfig) -> dict[str, Any]:
    """The INVERSE of ``draft_campaign.split_overlay``: the pair must lose nothing."""
    overlay: dict[str, Any] = {}
    for node, block in config.pipeline_overlay.items():
        if isinstance(block, dict) and block:
            overlay.setdefault(node, {})["config"] = dict(block)
    for node, narrowing in config.optimizer_narrowing.items():
        optimizer: dict[str, Any] = {}
        # `[]` is a real answer (every axis closed), so `is not None`, never truthiness.
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
    connector = connectors.get(draft.connector)
    return draft.pipeline_steps or list(connector.default_pipeline)


def draft_pipeline_dependencies(draft: DraftCampaign) -> tuple[PipelineDependency, ...]:
    active = set(draft_active_steps(draft))
    return dependencies_from_node_roles(
        {n: r for n, r in connectors.get(draft.connector).node_roles.items() if n in active}
    )


def _dependency_fulfilled(dep: PipelineDependency, draft: DraftCampaign) -> bool:
    if dep.kind == CANDIDATE_LIBRARY:
        return bool(draft.candidate_library)
    return False


def _draft_schema_source(draft: DraftCampaign) -> SchemaSource:
    if draft.backend_nodes:
        return "backend"
    return "local" if connectors.get(draft.connector).execution == "in_process" else "unreachable"


def _wire_overrides(draft: DraftCampaign) -> OptimizationOverrides:
    """Constructed, not validated: a reused dataset's ``max_rounds`` may exceed the edit bound."""
    held = draft.optimization_overrides
    return OptimizationOverrides.model_construct(
        max_rounds=held["max_rounds"],
        optimizer=held["optimizer"],
        nodes={
            node: ManifestNodeOverlay.model_validate(overlay)
            for node, overlay in held["nodes"].items()
        },
    )


def draft_wire(draft: DraftCampaign, workspace: Path | None = None) -> DraftCampaignWire:
    resolution = resolve_pipeline_for_draft(
        draft, campaign_id=draft.draft_id, cycle_id="", workspace=workspace
    )
    return DraftCampaignWire(
        draft_id=draft.draft_id,
        slug=draft.slug,
        sample_preview=[dict(row) for row in draft.sample_preview],
        n_samples=draft.n_samples,
        connector=draft.connector,
        scoring_matcher=draft.scoring_matcher,
        scoring_matchers=sorted(SCORING_FUNCTIONS),
        scoring_dials=draft.scoring_dials,
        optimization_overrides=_wire_overrides(draft),
        raw_task_description=draft.raw_task_description,
        pipeline_overlay=dict(draft.pipeline_overlay),
        headers=list(draft.headers),
        column_query=draft.column_query,
        column_ground_truth=draft.column_ground_truth,
        field_provenance=dict(draft.field_provenance),
        origin_prompt_fields=dict(draft.origin_prompt_fields),
        candidate_library_size=len(draft.candidate_library),
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        active_steps=draft_active_steps(draft),
        pipeline_view=resolution.view,
        node_config_schema=resolution.node_config_schema,
        node_output_schema=resolution.node_output_schema,
        reach=resolution.reach,
        is_single_node=resolution.is_single_node,
        schema_source=_draft_schema_source(draft),
        model_capabilities=resolution.model_capabilities,
        dependencies=[
            DraftDependency(**dep.model_dump(), fulfilled=_dependency_fulfilled(dep, draft))
            for dep in draft_pipeline_dependencies(draft)
        ],
        readiness=origin_readiness(draft),
    )


def default_campaign_json(draft: DraftCampaign) -> dict[str, Any]:
    """Without the node overlay: the mint splits that onto the per-campaign snapshot."""
    config = default_campaign_config(draft)
    return {"campaign_config": config.model_dump(mode="json", exclude_defaults=True)}


def draft_task_context(draft: DraftCampaign) -> TaskDecomposition:
    return TaskDecomposition.from_dict(
        {**draft.decomposed_task_context, "raw_description": draft.raw_task_description}
    )
