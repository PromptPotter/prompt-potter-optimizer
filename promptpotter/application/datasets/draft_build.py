from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from promptpotter import connectors
from promptpotter.application.datasets.draft_campaign import (
    MAX_EDITED_ROUNDS,
    DraftCampaign,
    EditDraftPatch,
    OptimizationOverrides,
    default_campaign_config,
    owned_drafts,
)
from promptpotter.application.datasets.origin_readiness import OriginReadiness, origin_readiness
from promptpotter.application.jobs.quota import launch_usd_bound
from promptpotter.application.pipeline_resolve import (
    OptimizerSetup,
    resolve_pipeline_for_draft,
)
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.domain.campaign_config import CampaignConfig
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
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores

SchemaSource = Literal["backend", "local", "unreachable"]


class DraftDependency(PipelineDependency):
    """One input the draft's active pipeline needs. Soft: unfulfilled, it does not block mint."""

    fulfilled: bool


class DraftCampaignWire(StrictModel):
    """A check-in's draft as every check-in route answers it; Start mints its campaign. No
    ``tenant_id`` rides it (decisions.md § 0002)."""

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
    optimizer_setup: OptimizerSetup = Field(
        description="The optimizer as this draft's config runs it, as a campaign serves it."
    )
    raw_task_description: str
    pipeline_overlay: dict[str, Any]
    headers: list[str]
    column_query: str
    column_ground_truth: str
    field_provenance: dict[str, Provenance] = Field(
        description="By checklist field id. Nothing reaches mint until `confirmed`."
    )
    origin_prompts: dict[str, dict[str, str]] = Field(
        description="Per active step, its starting prompt's fields by name; empty until the "
        "check-in fills it. A step bearing no prompt reads none."
    )
    candidate_library_size: int = Field(
        description="A count, not the list: a library runs to tens of thousands of entries."
    )
    created_at: str
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
    recap: str = Field(
        description="One plain sentence restating what this draft would run, for a surface to say."
    )
    setup_schema: dict[str, Any] = Field(
        description="JSON Schema (2020-12) of the check-in's settings: each top-level property is a "
        "step, each leaf keyed by its `EditDraftPatch` path and valued by this wire's own field of "
        "that path. A `writeOnly` subtree is `StartCheckinPayload`'s, sent with Start and stored "
        "nowhere."
    )


class DraftSummary(StrictModel):
    draft_id: str
    slug: str
    n_samples: int
    updated_at: str


class DraftListResponse(StrictModel):
    """The caller's open check-ins, most recently edited first."""

    drafts: list[DraftSummary]


def draft_list(stores: Stores) -> DraftListResponse:
    drafts = sorted(owned_drafts(stores), key=lambda d: d.updated_at, reverse=True)
    return DraftListResponse(
        drafts=[
            DraftSummary(
                draft_id=d.draft_id, slug=d.slug, n_samples=d.n_samples, updated_at=d.updated_at
            )
            for d in drafts
        ]
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


def draft_pipeline_dependencies(draft: DraftCampaign) -> tuple[PipelineDependency, ...]:
    active = set(draft.active_steps())
    return dependencies_from_node_roles(
        {
            n: r
            for n, r in connectors.CONNECTORS.get(draft.connector).node_roles.items()
            if n in active
        }
    )


def _dependency_fulfilled(dep: PipelineDependency, draft: DraftCampaign) -> bool:
    if dep.kind == CANDIDATE_LIBRARY:
        return bool(draft.candidate_library)
    return False


def _draft_schema_source(draft: DraftCampaign) -> SchemaSource:
    if draft.backend_nodes:
        return "backend"
    return (
        "local"
        if connectors.CONNECTORS.get(draft.connector).execution == "in_process"
        else "unreachable"
    )


def _wire_overrides(draft: DraftCampaign) -> OptimizationOverrides:
    """Constructed, not validated: a reused dataset's ``budget.rounds`` may exceed the edit bound."""
    held = draft.optimization_overrides
    return OptimizationOverrides.model_construct(
        budget=Budget.model_validate(held["budget"]),
        halt_at_accuracy=held["halt_at_accuracy"],
        optimizer=held["optimizer"],
        nodes={
            node: ManifestNodeOverlay.model_validate(overlay)
            for node, overlay in held["nodes"].items()
        },
    )


_SPOKEN_CONNECTOR = {"termnorm": "the TermNorm pipeline"}
_SPOKEN_MATCHER = {"label_match": "an exact match against the target"}
_TITLE_MAX_CHARS = 72


def _task_title(task: str) -> str | None:
    first = next((line.strip() for line in task.splitlines() if line.strip()), "")
    title = re.sub(r"[.\s]+$", "", re.sub(r"^#+\s*", "", first))
    return title if 0 < len(title) <= _TITLE_MAX_CHARS else None


def _recap(draft: DraftCampaign) -> str:
    title = _task_title(draft.raw_task_description)
    lead = (
        f"evolve a prompt for “{title}”"
        if title
        else f"evolve a prompt that turns each “{draft.column_query or 'your input'}” into "
        f"the right “{draft.column_ground_truth or 'the target'}”"
    )
    rounds = default_campaign_config(draft).optimization.budget.rounds
    over = (
        "with no rounds cap"
        if rounds is None
        else f"over {'1 round' if rounds == 1 else f'up to {rounds} rounds'}"
    )
    return (
        f"PromptPotter will {lead} — "
        f"running {_SPOKEN_CONNECTOR.get(draft.connector, draft.connector)}, "
        f"scoring by {_SPOKEN_MATCHER.get(draft.scoring_matcher, draft.scoring_matcher)}, "
        f"{over}."
    )


def draft_wire(stores: Stores, draft: DraftCampaign) -> DraftCampaignWire:
    resolution = resolve_pipeline_for_draft(stores, draft)
    return DraftCampaignWire(
        draft_id=draft.draft_id,
        slug=draft.slug,
        sample_preview=[dict(row) for row in draft.sample_preview],
        n_samples=draft.n_samples,
        connector=draft.connector,
        scoring_matcher=draft.scoring_matcher,
        scoring_matchers=sorted(MATCHERS),
        scoring_dials=draft.scoring_dials,
        optimization_overrides=_wire_overrides(draft),
        optimizer_setup=resolution.optimizer_setup,
        raw_task_description=draft.raw_task_description,
        pipeline_overlay=dict(draft.pipeline_overlay),
        headers=list(draft.headers),
        column_query=draft.column_query,
        column_ground_truth=draft.column_ground_truth,
        field_provenance=dict(draft.field_provenance),
        origin_prompts={node: dict(fields) for node, fields in draft.origin_prompts.items()},
        candidate_library_size=len(draft.candidate_library),
        created_at=draft.created_at,
        active_steps=draft.active_steps(),
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
        recap=_recap(draft),
        setup_schema=setup_schema(draft, launch_usd=launch_usd_bound(stores)),
    )


def _field(schema: dict[str, Any], name: str, title: str) -> dict[str, Any]:
    """One property off a model's own schema, so its bounds have one source; the null arm drops,
    since a blank field IS the absent value."""
    prop = dict(schema["properties"][name])
    arms = [arm for arm in prop.pop("anyOf", ()) if arm.get("type") != "null"]
    prop = {**(arms[0] if arms else {}), **prop}
    for loose, standard in (("ge", "minimum"), ("le", "maximum"), ("gt", "exclusiveMinimum")):
        if loose in prop:
            prop[standard] = prop.pop(loose)
    if prop.get("default") is None:
        prop.pop("default", None)
    return {**prop, "title": title}


def _column(draft: DraftCampaign, field_key: str, title: str) -> dict[str, Any]:
    said = draft.field_provenance.get(field_key, Provenance.UNSET)
    return {
        "type": "string",
        "title": title,
        "enum": list(draft.headers),
        **({} if said is Provenance.CONFIRMED else {"description": said.value}),
    }


def _usd_cap(budget: dict[str, Any], launch: float | None) -> dict[str, Any]:
    """Bounded at what admission takes, never defaulted to it: a default below the campaign's own
    ceiling is a clamp, and a clamped run halts mid-campaign."""
    field = _field(budget, "usd", "Spend cap in USD")
    if launch is None:
        return field
    return {
        **field,
        "maximum": launch,
        "description": f"This account launches at up to ${launch:.2f}.",
    }


def setup_schema(draft: DraftCampaign, *, launch_usd: float | None) -> dict[str, Any]:
    patch = EditDraftPatch.model_json_schema()
    overrides = OptimizationOverrides.model_json_schema()
    budget = Budget.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "title": "Data",
                "required": ["slug"],
                "properties": {
                    "slug": _field(patch, "slug", "Name"),
                    "column_query": _column(draft, "column.query", "Input column"),
                    "column_ground_truth": _column(draft, "column.ground_truth", "Target column"),
                },
            },
            "run": {
                "type": "object",
                "title": "Run bounds",
                "properties": {
                    "optimization_overrides": {
                        "type": "object",
                        "title": "This campaign",
                        "properties": {
                            "budget": {
                                "type": "object",
                                "properties": {
                                    "rounds": {
                                        **_field(budget, "rounds", "Rounds cap"),
                                        "maximum": MAX_EDITED_ROUNDS,
                                    },
                                },
                            },
                            "halt_at_accuracy": _field(
                                overrides, "halt_at_accuracy", "Halt at accuracy"
                            ),
                        },
                    },
                    "budget": {
                        "type": "object",
                        "title": "This launch only",
                        "description": "Blank inherits your account's allowance, which still "
                        "binds underneath; whichever cap trips first stops the run.",
                        "writeOnly": True,
                        "properties": {
                            "usd": _usd_cap(budget, launch_usd),
                            "tokens": _field(budget, "tokens", "Token cap"),
                            "wall_clock_s": _field(
                                budget, "wall_clock_s", "Wall-clock cap in seconds"
                            ),
                        },
                    },
                },
            },
        },
    }


def default_campaign_block(draft: DraftCampaign) -> dict[str, Any]:
    """Without the node overlay: the mint splits that onto the per-campaign snapshot."""
    return default_campaign_config(draft).model_dump(mode="json", exclude_defaults=True)


def draft_task_context(draft: DraftCampaign) -> TaskDecomposition:
    return TaskDecomposition.from_dict(
        {**draft.decomposed_task_context, "raw_description": draft.raw_task_description}
    )
