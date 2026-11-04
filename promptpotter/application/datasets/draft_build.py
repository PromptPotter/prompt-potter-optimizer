from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from promptpotter import connectors
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    default_campaign_config,
    owned_draft,
)
from promptpotter.application.datasets.origin_readiness import (
    SETUP_STEP_TITLES,
    OriginReadiness,
    load_resolution,
    origin_readiness,
)
from promptpotter.application.hierarchy import draft_starts
from promptpotter.application.jobs.quota import (
    LaunchRefusal,
    account_wallet,
    launch_bound,
    launch_refusal,
)
from promptpotter.application.pipeline_resolve import (
    ServedPipeline,
    draft_pipeline,
    draft_sends,
    draft_served,
    resolve_pipeline_for_draft,
)
from promptpotter.application.pipeline_responses import OptimizerSetup
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.checkin_draft import OriginResolution, SetupStep, UploadBasis
from promptpotter.domain.draft_edit import MAX_EDITED_ROUNDS, EditDraftPatch, OptimizationOverrides
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.phases import LaunchStage
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
from promptpotter.domain.spend import CAP_AXIS_INFO, Budget, money_text
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NoApiKeyError


class DraftDependency(PipelineDependency):
    """One input the draft's active pipeline needs. Soft: unfulfilled, it does not block mint."""

    fulfilled: bool


class DraftCampaignWire(StrictModel):
    """A check-in's draft as every check-in route answers it; Start mints its campaign.

    No ``tenant_id`` rides it (decisions.md § 0002).
    """

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
    optimization_overrides: OptimizationOverrides = Field(
        description="The draft's overrides, its `budget` the one Start declares: "
        "`pipeline_resolve.draft_pipeline`'s config, a free account's tier laid last."
    )
    optimizer_setup: OptimizerSetup = Field(
        description="The optimizer as this draft's config runs it, as a campaign serves it."
    )
    raw_task_description: str | None = Field(description="The task; null until one is set.")
    pipeline_overlay: dict[str, Any]
    headers: list[str]
    column_query: str | None = Field(description="The input header; null until one is set.")
    column_ground_truth: str | None = Field(description="The target header; null until one is set.")
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
    model_capabilities: CapabilityMenu = Field(
        description="A null `reasoning_efforts` is UNKNOWN, never unsupported."
    )
    dependencies: list[DraftDependency]
    readiness: OriginReadiness = Field(
        description="The server's mint gate, recomputed on every draft response."
    )
    launch_refusal: LaunchRefusal | None = Field(
        description="`admit_launch`'s refusal of Start at the declared budget; null where it admits."
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
        if narrowing.prompt_fields_held:
            optimizer["prompt_fields_held"] = list(narrowing.prompt_fields_held)
        if narrowing.param_allowed_values:
            optimizer["param_allowed_values"] = {
                k: list(v) for k, v in narrowing.param_allowed_values.items()
            }
        if optimizer:
            overlay.setdefault(node, {})["optimizer"] = optimizer
    return overlay


def _dependencies(connector: str, active: list[str]) -> tuple[PipelineDependency, ...]:
    roles = connectors.CONNECTORS.get(connector).node_roles
    return dependencies_from_node_roles({n: r for n, r in roles.items() if n in active})


def _dependency_fulfilled(dep: PipelineDependency, draft: DraftCampaign) -> bool:
    if dep.kind == CANDIDATE_LIBRARY:
        return bool(draft.candidate_library)
    return False


def _wire_overrides(draft: DraftCampaign, declared: Budget) -> OptimizationOverrides:
    """Constructed, not validated: a reused dataset's ``budget.rounds`` may exceed the edit bound."""
    held = draft.optimization_overrides
    return OptimizationOverrides.model_construct(
        budget=declared,
        halt_at_accuracy=held["halt_at_accuracy"],
        optimizer=held["optimizer"],
        nodes={
            node: ManifestNodeOverlay.model_validate(overlay)
            for node, overlay in held["nodes"].items()
        },
    )


_TITLE_MAX_CHARS = 72


def _task_title(task: str | None) -> str | None:
    if task is None:
        return None
    first = next((line.strip() for line in task.splitlines() if line.strip()), "")
    title = re.sub(r"[.\s]+$", "", re.sub(r"^#+\s*", "", first))
    return title if 0 < len(title) <= _TITLE_MAX_CHARS else None


def _recap(draft: DraftCampaign, connector: str, rounds: int | None) -> str:
    title = _task_title(draft.raw_task_description)
    lead = (
        f"evolve a prompt for “{title}”"
        if title
        else f"evolve a prompt that turns each “{draft.column_query or 'your input'}” into "
        f"the right “{draft.column_ground_truth or 'the target'}”"
    )
    over = (
        "with no rounds cap"
        if rounds is None
        else f"over {'1 round' if rounds == 1 else f'up to {rounds} rounds'}"
    )
    return (
        f"PromptPotter will {lead} — "
        f"running {connector}, "
        f"scoring by {draft.scoring_matcher}, "
        f"{over}."
    )


async def read_draft_wire(stores: Stores, draft: DraftCampaign) -> DraftCampaignWire:
    return draft_wire(stores, draft, await draft_served(stores, draft))


class DraftReopenResponse(StrictModel):
    """A re-opened check-in: its draft and what the last resolver turn left."""

    draft: DraftCampaignWire
    resolution: OriginResolution = Field(
        description="What the last resolver turn left, as `resolve-origin` answered it."
    )
    resolution_pending: bool = Field(
        description="Its task is on file but no resolver turn ever set it up "
        "(`DraftCampaign.resolution_pending`): the one case a reopen sends `resolve-origin`, so a "
        "revisit never re-buys a turn or re-proposes over authored prompts."
    )
    start: LaunchStage | None = Field(
        description="The stage of the Start that holds it: `queued` waits on a machine slot, "
        "`starting` is minting it. Set, a reopen offers no second Start; its listing's lifecycle "
        "tag says the same."
    )


async def reopen_draft(stores: Stores, draft_id: str) -> DraftReopenResponse:
    """404 once started or discarded."""
    draft = owned_draft(stores, draft_id)
    return DraftReopenResponse(
        draft=await read_draft_wire(stores, draft),
        resolution=load_resolution(stores, draft),
        resolution_pending=draft.resolution_pending(),
        start=draft_starts(stores).get(draft_id),
    )


def draft_wire(stores: Stores, draft: DraftCampaign, served: ServedPipeline) -> DraftCampaignWire:
    resolution = resolve_pipeline_for_draft(stores, draft, served)
    pipeline = draft_pipeline(stores, draft, served)
    declared = pipeline.config.optimization.budget
    active = list(pipeline.chain.active_steps)
    refusal, launch = _launch(stores, draft, declared)
    return DraftCampaignWire(
        draft_id=draft.draft_id,
        slug=draft.slug,
        sample_preview=[dict(row) for row in draft.sample_preview],
        n_samples=draft.n_samples,
        connector=served.connector,
        scoring_matcher=draft.scoring_matcher,
        scoring_matchers=sorted(MATCHERS),
        scoring_dials=draft.scoring_dials,
        optimization_overrides=_wire_overrides(draft, declared),
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
        active_steps=active,
        pipeline_view=resolution.view,
        node_config_schema=resolution.node_config_schema,
        node_output_schema=resolution.node_output_schema,
        reach=resolution.reach,
        is_single_node=resolution.is_single_node,
        model_capabilities=resolution.model_capabilities,
        dependencies=[
            DraftDependency(**dep.model_dump(), fulfilled=_dependency_fulfilled(dep, draft))
            for dep in _dependencies(served.connector, active)
        ],
        readiness=origin_readiness(draft, pipeline),
        launch_refusal=refusal,
        recap=_recap(draft, served.connector, declared.rounds),
        setup_schema=setup_schema(draft, launch=launch, declared=declared),
    )


def _launch(
    stores: Stores, draft: DraftCampaign, declared: Budget
) -> tuple[LaunchRefusal | None, Budget]:
    """``admit_launch``'s refusal of this Start, then the most it may declare."""
    wallet = account_wallet(stores)
    try:
        sends = draft_sends(stores, draft)
    except NoApiKeyError as unpaid:
        return LaunchRefusal.of(unpaid), wallet.headroom
    refused = launch_refusal(wallet, declared, sends, stores=stores, refusing=None)
    return (None if refused is None else LaunchRefusal.of(refused)), launch_bound(wallet, sends)


def _field(schema: dict[str, Any], name: str, title: str) -> dict[str, Any]:
    """One property off a model's own schema, so its bounds have one source; the null arm drops,
    since a blank field IS the absent value."""
    prop = dict(schema["properties"][name])
    arms = [arm for arm in prop.pop("anyOf", ()) if arm.get("type") != "null"]
    prop = {**(arms[0] if arms else {}), **prop}
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


def _cap(budget: dict[str, Any], axis: str, bound: float | None = None) -> dict[str, Any]:
    """Bounded at what admission takes, never defaulted to it: a default below the campaign's own
    ceiling is a clamp, and a clamped run halts mid-campaign."""
    field = _field(budget, axis, CAP_AXIS_INFO[axis].title)
    return field if bound is None else {**field, "maximum": bound}


def _blank_binds(declared: Budget) -> str:
    """What a blank launch cap leaves binding: the campaign's declared ceiling (a free account's
    is the tier's), else the account's allowance."""
    if declared.usd is None and declared.tokens is None:
        return "Blank: your account's allowance binds this launch."
    return f"Blank: the campaign's {money_text(declared)} ceiling binds this launch."


def setup_schema(draft: DraftCampaign, *, launch: Budget, declared: Budget) -> dict[str, Any]:
    patch = EditDraftPatch.model_json_schema()
    overrides = OptimizationOverrides.model_json_schema()
    budget = Budget.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            SetupStep.DATA: {
                "type": "object",
                "title": SETUP_STEP_TITLES[SetupStep.DATA],
                "required": ["slug"],
                "properties": {
                    "slug": _field(patch, "slug", "Name"),
                    "column_query": _column(draft, "column.query", "Input column"),
                    "column_ground_truth": _column(draft, "column.ground_truth", "Target column"),
                },
            },
            SetupStep.RUN: {
                "type": "object",
                "title": SETUP_STEP_TITLES[SetupStep.RUN],
                "properties": {
                    "optimization_overrides": {
                        "type": "object",
                        "title": "This campaign",
                        "properties": {
                            "budget": {
                                "type": "object",
                                "properties": {
                                    "rounds": _cap(budget, "rounds", MAX_EDITED_ROUNDS),
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
                        "description": _blank_binds(declared),
                        "writeOnly": True,
                        "properties": {
                            "usd": _cap(budget, "usd", launch.usd),
                            "tokens": _cap(budget, "tokens", launch.tokens),
                            "wall_clock_s": _cap(budget, "wall_clock_s"),
                        },
                    },
                },
            },
        },
    }


def default_campaign_block(draft: DraftCampaign, upload: UploadBasis) -> dict[str, Any]:
    """Without the node overlay: the mint splits that onto the per-campaign snapshot."""
    return default_campaign_config(draft, upload).model_dump(mode="json", exclude_defaults=True)


def draft_task_context(draft: DraftCampaign) -> TaskDecomposition:
    return TaskDecomposition.from_dict(
        {**draft.decomposed_task_context, "raw_description": draft.confirmed_task()}
    )
