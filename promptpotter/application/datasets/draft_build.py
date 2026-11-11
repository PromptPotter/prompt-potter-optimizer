from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from promptpotter.application.datasets.draft_campaign import DraftCriterion, owned_draft
from promptpotter.application.datasets.origin_readiness import (
    SETUP_STEP_TITLES,
    OriginReadiness,
    load_resolution,
    origin_readiness,
)
from promptpotter.application.hierarchy import draft_starts
from promptpotter.application.jobs.quota import account_wallet, launch_bound, launch_refusal
from promptpotter.application.pipeline_resolve import (
    ServedPipeline,
    draft_pipeline,
    draft_sends,
    draft_served,
    resolve_pipeline_for_draft,
)
from promptpotter.application.pipeline_responses import CampaignPipelineResponse
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.domain.campaign_config import CampaignConfig, OptimizationConfig
from promptpotter.domain.checkin_draft import DraftFile, OriginResolution, SetupStep
from promptpotter.domain.command_events import CommandRecord, draft_subject
from promptpotter.domain.command_kinds import CommandKind
from promptpotter.domain.draft_edit import EditDraftPatch
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.phases import LaunchStage
from promptpotter.domain.pipeline_schema import (
    CANDIDATE_LIBRARY,
    PipelineDependency,
    PipelineSchema,
    dependencies_from_node_roles,
)
from promptpotter.domain.refusal import Refusal
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import CAP_AXIS_INFO, Budget, CapAxis, money_text
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.infrastructure.store.stores import Stores


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
    scoring_matchers: list[str] = Field(
        description="The matchers a check-in may pick between — the compiler's own set."
    )
    criterion: DraftCriterion = Field(
        description="`config.scoring` read as the matcher and dials a check-in picks; no client "
        "rebuilds a scoring block from them."
    )
    config: CampaignConfig = Field(
        description="What Start declares: the draft's partial config laid over its basis "
        "(`pipeline_resolve.draft_pipeline`'s config, a free account's tier laid last). An "
        "`EditDraftPatch.config` addresses it by the same paths, but for `scoring`, which also "
        "takes a check-in's pick (`matcher`, `dials`); an upload's `pipeline_overlay` is its "
        "definition's, read in `pipeline`, never here."
    )
    pipeline: CampaignPipelineResponse = Field(
        description="`GET /campaigns/{id}/pipeline`'s answer, computed from the draft: a "
        "pre-commit check-in has no dataset dir, so its pipeline is never fetched by slug."
    )
    raw_task_description: str | None = Field(description="The task; null until one is set.")
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
        "`pipeline.node_config_schema`'s answer alone."
    )
    dependencies: list[DraftDependency]
    readiness: OriginReadiness = Field(
        description="The server's mint gate, recomputed on every draft response."
    )
    launch_refusal: Refusal | None = Field(
        description="`admit_launch`'s refusal of Start at the declared budget, its "
        "`LaunchRefusalReason` in `details.reason`; null where it admits."
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


def _dependencies(chain: PipelineSchema, active: list[str]) -> tuple[PipelineDependency, ...]:
    return dependencies_from_node_roles(
        {n.name: n.role for n in chain.nodes if n.name in active and n.role is not None}
    )


def _dependency_fulfilled(dep: PipelineDependency, draft: DraftFile) -> bool:
    if dep.kind == CANDIDATE_LIBRARY:
        return bool(draft.candidate_library)
    return False


_TITLE_MAX_CHARS = 72


def _task_title(task: str | None) -> str | None:
    if task is None:
        return None
    first = next((line.strip() for line in task.splitlines() if line.strip()), "")
    title = re.sub(r"[.\s]+$", "", re.sub(r"^#+\s*", "", first))
    return title if 0 < len(title) <= _TITLE_MAX_CHARS else None


def _recap(draft: DraftFile, connector: str, criterion: DraftCriterion) -> str:
    """No rounds: the Start step sends them after this is said, so its blank names them
    (`_blank_binds`)."""
    title = _task_title(draft.raw_task_description)
    lead = (
        f"evolve a prompt for “{title}”"
        if title
        else f"evolve a prompt that turns each “{draft.column_query or 'your input'}” into "
        f"the right “{draft.column_ground_truth or 'the target'}”"
    )
    return (
        f"PromptPotter will {lead} — "
        f"running {connector}, "
        f"scoring by {criterion.matcher or 'its formula'}."
    )


async def read_draft_wire(stores: Stores, draft: DraftFile) -> DraftCampaignWire:
    return draft_wire(stores, draft, await draft_served(stores, draft))


class HeldStart(FrozenModel):
    """The Start holding a check-in: its stage, and the command a reopen follows to its end."""

    stage: LaunchStage = Field(
        description="`queued` waits on a machine slot, `starting` is minting it."
    )
    command_id: str = Field(
        description="The `start-checkin` command that filed it, whose reading says how it ends."
    )

    @classmethod
    def of(cls, stores: Stores, draft_id: str, stage: LaunchStage) -> HeldStart:
        """Its command is the last `start-checkin` filed on the draft: a Start's claim refuses every
        command on the draft, a second Start among them, before it is filed, until its mint closes."""
        filed = [
            event.command_id
            for event in stores.commands.book().subject_events(draft_subject(draft_id))
            if isinstance(event, CommandRecord) and event.kind == CommandKind.START_CHECKIN
        ]
        return cls(stage=stage, command_id=filed[-1])


class DraftReopenResponse(StrictModel):
    """A re-opened check-in: its draft and what the last resolver turn left."""

    draft: DraftCampaignWire
    resolution: OriginResolution = Field(
        description="What the last resolver turn left, as `resolve-origin` answered it."
    )
    resolution_pending: bool = Field(
        description="Its task is on file and no resolver turn ever set it up "
        "(`checkin_draft.py::DraftFile.resolution_pending`): the one case a surface sends "
        "`resolve-origin` unasked, so a revisit never re-buys a turn or re-proposes over authored "
        "prompts. With no task on file a surface asks for one first; an empty answer sends "
        "`resolve-origin`, refused `resolver_not_owed` where no turn is owed."
    )
    start: HeldStart | None = Field(
        description="The Start that holds it, at its stage, beside the command a reopen follows "
        "until it mints or ends. Set, a reopen offers no second Start; its listing's lifecycle tag "
        "says the same."
    )


async def reopen_draft(stores: Stores, draft_id: str) -> DraftReopenResponse:
    """404 once started or discarded."""
    draft = owned_draft(stores, draft_id)
    stage = draft_starts(stores).get(draft_id)
    return DraftReopenResponse(
        draft=await read_draft_wire(stores, draft),
        resolution=load_resolution(stores, draft),
        resolution_pending=draft.resolution_pending(),
        start=None if stage is None else HeldStart.of(stores, draft_id, stage),
    )


def draft_wire(stores: Stores, draft: DraftFile, served: ServedPipeline) -> DraftCampaignWire:
    pipeline = draft_pipeline(stores, draft, served)
    declared = pipeline.config.optimization.budget
    active = list(pipeline.chain.active_steps)
    refusal, launch = _launch(stores, draft, declared)
    return DraftCampaignWire(
        draft_id=draft.draft_id,
        slug=draft.slug,
        sample_preview=[dict(row) for row in draft.sample_preview],
        n_samples=draft.n_samples,
        scoring_matchers=sorted(MATCHERS),
        criterion=pipeline.criterion,
        config=pipeline.config,
        pipeline=resolve_pipeline_for_draft(stores, draft, served),
        raw_task_description=draft.raw_task_description,
        headers=list(draft.headers),
        column_query=draft.column_query,
        column_ground_truth=draft.column_ground_truth,
        field_provenance=dict(draft.field_provenance),
        origin_prompts={node: dict(fields) for node, fields in draft.origin_prompts.items()},
        candidate_library_size=len(draft.candidate_library),
        created_at=draft.created_at,
        active_steps=active,
        dependencies=[
            DraftDependency(**dep.model_dump(), fulfilled=_dependency_fulfilled(dep, draft))
            for dep in _dependencies(pipeline.chain, active)
        ],
        readiness=origin_readiness(draft, pipeline),
        launch_refusal=refusal,
        recap=_recap(draft, served.connector, pipeline.criterion),
        setup_schema=setup_schema(draft, launch=launch, declared=declared),
    )


def _launch(stores: Stores, draft: DraftFile, declared: Budget) -> tuple[Refusal | None, Budget]:
    """``admit_launch``'s refusal of this Start, then the most it may declare."""
    wallet, sends = account_wallet(stores), draft_sends(stores, draft)
    refused = launch_refusal(wallet, declared, sends, stores=stores, refusing=None)
    return (None if refused is None else Refusal.of(refused)), launch_bound(wallet, sends)


def _field(schema: dict[str, Any], name: str, title: str) -> dict[str, Any]:
    """One property off a model's own schema, so its bounds have one source; the null arm drops,
    since a blank field IS the absent value."""
    prop = dict(schema["properties"][name])
    arms = [arm for arm in prop.pop("anyOf", ()) if arm.get("type") != "null"]
    prop = {**(arms[0] if arms else {}), **prop}
    if prop.get("default") is None:
        prop.pop("default", None)
    return {**prop, "title": title}


def _column(draft: DraftFile, field_key: str, title: str) -> dict[str, Any]:
    said = draft.field_provenance.get(field_key, Provenance.UNSET)
    return {
        "type": "string",
        "title": title,
        "enum": list(draft.headers),
        **({} if said is Provenance.CONFIRMED else {"description": said.value}),
    }


def _cap(budget: dict[str, Any], axis: CapAxis, bound: float | None = None) -> dict[str, Any]:
    """Bounded at what admission takes, never defaulted to it: a default below the campaign's own
    ceiling is a clamp, and a clamped run halts mid-campaign."""
    field = _field(budget, axis, CAP_AXIS_INFO[axis].title)
    return field if bound is None else {**field, "maximum": bound}


def _blank_binds(declared: Budget) -> str:
    """What a blank launch cap leaves binding: the campaign's declared ceiling (a free account's
    is the tier's), else the account's allowance; and its declared rounds."""
    money = (
        "your account's allowance"
        if declared.usd is None and declared.tokens is None
        else f"the campaign's {money_text(declared)} ceiling"
    )
    rounds = declared.rounds
    over = (
        "with no rounds cap"
        if rounds is None
        else f"over {'1 round' if rounds == 1 else f'up to {rounds} rounds'}"
    )
    return f"Blank: {money} binds this launch, {over}."


def setup_schema(draft: DraftFile, *, launch: Budget, declared: Budget) -> dict[str, Any]:
    patch = EditDraftPatch.model_json_schema()
    optimization = OptimizationConfig.model_json_schema()
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
                    "config": {
                        "type": "object",
                        "title": "This campaign",
                        "properties": {
                            "optimization": {
                                "type": "object",
                                "properties": {
                                    "halt_at_accuracy": _field(
                                        optimization, "halt_at_accuracy", "Halt at accuracy"
                                    ),
                                },
                            },
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
                            "rounds": _cap(budget, "rounds"),
                            "wall_clock_s": _cap(budget, "wall_clock_s"),
                        },
                    },
                },
            },
        },
    }


def draft_task_context(draft: DraftFile) -> TaskDecomposition:
    return TaskDecomposition.from_dict(
        {**draft.decomposed_task_context, "raw_description": draft.confirmed_task()}
    )
