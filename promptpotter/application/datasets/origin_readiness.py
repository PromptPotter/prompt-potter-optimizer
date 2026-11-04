from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from pydantic import Field, computed_field

from promptpotter.application.datasets.draft_campaign import DraftCampaign, basis_json
from promptpotter.domain.command_kinds import EditDraftCampaignPayload
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from promptpotter.application.bench.task_context import OriginQuestion
    from promptpotter.application.pipeline_resolve import DraftPipeline
    from promptpotter.infrastructure.store.stores import Stores


class SetupStep(StrEnum):
    """The check-in's steps, in the order a check-in shows them: each a fold that sets one part of
    the campaign, keyed as `setup_schema` keys the steps it serves."""

    OPTIMIZER = "optimizer"
    DATA = "data"
    SCORING = "scoring"
    PIPELINE = "pipeline"
    HEAD_TO_HEAD = "head_to_head"
    RUN = "run"


SETUP_STEP_TITLES: dict[SetupStep, str] = {
    SetupStep.OPTIMIZER: "Optimizer",
    SetupStep.DATA: "Data",
    SetupStep.SCORING: "Scoring",
    SetupStep.PIPELINE: "Pipeline",
    SetupStep.HEAD_TO_HEAD: "Head-to-head",
    SetupStep.RUN: "Run bounds",
}
assert SETUP_STEP_TITLES.keys() == set(SetupStep)


class ChecklistField(NamedTuple):
    # The `EditDraftPatch` key, and `DraftCampaign` attribute, one answer writes; None where no
    # single value closes it (a node's model lives inside the whole overlay a patch replaces).
    patch_key: str | None
    # The step whose fold sets it; None where the chat answers it.
    step: SetupStep | None
    # What a proposal of a value reads as.
    proposal: str
    # Its answer is one of the upload's headers.
    of_headers: bool = False


#: THE checklist: every field the gate keys a gap by, and what a resolver may propose or ask.
CHECKLIST_FIELDS: dict[str, ChecklistField] = {
    "column.query": ChecklistField(
        "column_query", SetupStep.DATA, "Read the input from “{}”", of_headers=True
    ),
    "column.ground_truth": ChecklistField(
        "column_ground_truth", SetupStep.DATA, "Grade against “{}”", of_headers=True
    ),
    "task_description": ChecklistField("raw_task_description", None, "Set the task: “{}”"),
}
_NODE_MODEL = re.compile(r"node\.(?P<node>.+)\.model")


def checklist_field(field: str) -> ChecklistField | None:
    if node := _NODE_MODEL.fullmatch(field):
        return ChecklistField(None, SetupStep.PIPELINE, f"Run {node['node']} on “{{}}”")
    return CHECKLIST_FIELDS.get(field)


class FieldGap(StrictModel):
    """One origin field that still blocks mint — also the ``422 origin_incomplete`` body's ``details.gaps``."""

    field: str = Field(description="Checklist field id, e.g. `column.query`.")
    reason: Literal["unset", "proposed_unconfirmed", "conflict"]
    hint: str = Field(description="One operator-facing line on how to close it.")
    step: SetupStep | None = Field(
        description="The step whose fold closes it; null where the chat answers it."
    )

    @classmethod
    def of(
        cls, field: str, reason: Literal["unset", "proposed_unconfirmed", "conflict"], hint: str
    ) -> FieldGap:
        row = checklist_field(field)
        if row is None:
            raise ValueError(f"{field!r} is no checklist field (`CHECKLIST_FIELDS`)")
        return cls(field=field, reason=reason, hint=hint, step=row.step)


class OriginReadiness(StrictModel):
    """The mint gate's verdict; a surface gates Start on it and never re-derives it."""

    gaps: tuple[FieldGap, ...]

    @computed_field(description="True iff no field still blocks mint.")  # type: ignore[prop-decorator]
    @property
    def complete(self) -> bool:
        return not self.gaps


class CheckinQuestion(StrictModel):
    """A resolver question one answer closes, projected through `CHECKLIST_FIELDS`."""

    field: str
    path: list[str] = Field(description="The `EditDraftPatch` path the answer is written at.")
    step: SetupStep | None
    prompt: str
    options: list[str] = Field(description="The answers it takes; empty is free text.")


def checkin_questions(
    draft: DraftCampaign, asked: Iterable[OriginQuestion]
) -> list[CheckinQuestion]:
    """A question on a field no single patch closes is dropped, as `raised_commands` drops a finding."""
    questions: list[CheckinQuestion] = []
    for question in asked:
        row = checklist_field(question.field)
        if row is None or row.patch_key is None:
            continue
        questions.append(
            CheckinQuestion(
                field=question.field,
                path=[row.patch_key],
                step=row.step,
                prompt=question.prompt,
                options=question.options or (list(draft.headers) if row.of_headers else []),
            )
        )
    return questions


class OriginLastResolution(StrictModel):
    """One resolver turn's own output. Its findings ride ``OriginResolution.raised`` as commands."""

    assessment: str
    questions: list[CheckinQuestion]
    recap: str = Field(description="Set only on a `ready` turn.")


class RaisedCommand(StrictModel):
    """One proposal, already the command a click fires. The assistant offers it and never fires it."""

    kind: Literal["edit-draft-campaign"] = "edit-draft-campaign"
    payload: EditDraftCampaignPayload
    label: str = Field(description="What the proposal does, in `CHECKLIST_FIELDS`' words.")
    evidence: str = Field(description="The citation backing the proposal.")


class OriginResolution(StrictModel):
    """The last resolver turn, as the draft's ``resolution.json`` holds it; the mint gate is the
    draft's `readiness`, recomputed on every read."""

    last_resolution: OriginLastResolution | None = Field(
        default=None, description="Null until a resolver turn ran, and again after a later edit."
    )
    raised: list[RaisedCommand] = Field(
        default_factory=list, description="Proposals the last turn left unclicked."
    )
    degraded_cause: str | None = Field(
        default=None,
        description="Why the last turn came back thin or empty; null where it did not.",
    )


def origin_readiness(draft: DraftCampaign, pipeline: DraftPipeline) -> OriginReadiness:
    """Over the *pipeline* the draft's declaration resolves to. Ready is not mintable: a node's
    required ``{{template vars}}`` are checked only at mint."""
    gaps = _column_gaps(draft)
    _check_confirmed(
        draft,
        field_key="task_description",
        value=draft.raw_task_description,
        label="task framing",
        hint="Describe what the prompt should do.",
        gaps=gaps,
    )

    _check_node_models(pipeline, gaps=gaps)

    return OriginReadiness(gaps=tuple(gaps))


def columns_confirmed(draft: DraftCampaign) -> bool:
    """The gate's column clause: only past it may anything cut the upload's rows."""
    return not _column_gaps(draft)


def _column_gaps(draft: DraftCampaign) -> list[FieldGap]:
    gaps: list[FieldGap] = []
    for field_key, value, label in (
        ("column.query", draft.column_query, "input"),
        ("column.ground_truth", draft.column_ground_truth, "target"),
    ):
        column = _check_column(draft, field_key=field_key, value=value, label=label, gaps=gaps)
        if column is not None and (row := draft.first_blank_row.get(column)) is not None:
            gaps.append(
                FieldGap.of(
                    field_key,
                    "conflict",
                    f"Row {row} has an empty “{column}” cell, and every row needs both columns. "
                    "Pick another column, or fill the cell and upload the file again.",
                )
            )
    if draft.column_query is not None and draft.column_query == draft.column_ground_truth:
        gaps.append(
            FieldGap.of(
                "column.ground_truth",
                "conflict",
                "Input and target are the same column, so every answer trivially matches. "
                "Pick two different columns.",
            )
        )
    return gaps


def _check_column(
    draft: DraftCampaign,
    *,
    field_key: str,
    value: str | None,
    label: str,
    gaps: list[FieldGap],
) -> str | None:
    """The header confirmed as the column; None where its gap is appended instead."""
    provenance = draft.field_provenance.get(field_key, Provenance.UNSET)
    if provenance is Provenance.CONFIRMED and value is not None and value in draft.headers:
        return value
    if provenance is Provenance.PROPOSED:
        gaps.append(
            FieldGap.of(
                field_key,
                "proposed_unconfirmed",
                f"Confirm the {label} column (proposed {value!r}).",
            )
        )
        return None
    headers = ", ".join(draft.headers) or "<none>"
    gaps.append(
        FieldGap.of(
            field_key,
            "unset",
            f"Pick which uploaded column is the {label}. Available: {headers}.",
        )
    )
    return None


def _check_confirmed(
    draft: DraftCampaign,
    *,
    field_key: str,
    value: str | None,
    label: str,
    hint: str,
    gaps: list[FieldGap],
) -> None:
    provenance = draft.field_provenance.get(field_key, Provenance.UNSET)
    if provenance is Provenance.CONFIRMED and value is not None:
        return
    proposed = provenance is Provenance.PROPOSED
    extra = " (proposed — confirm or correct)." if proposed else ""
    gaps.append(
        FieldGap.of(
            field_key,
            "proposed_unconfirmed" if proposed else "unset",
            f"{hint} [{label}]{extra}",
        )
    )


def _check_node_models(pipeline: DraftPipeline, *, gaps: list[FieldGap]) -> None:
    configs = dict(node_config_items(pipeline.params))
    for node in pipeline.chain.nodes:
        if node.is_llm and not configs.get(node.name, {}).get("model"):
            gaps.append(
                FieldGap.of(
                    f"node.{node.name}.model",
                    "unset",
                    f"Set the model for LLM node {node.name!r} in the "
                    f"{SETUP_STEP_TITLES[SetupStep.PIPELINE]} step.",
                )
            )


def field_values(draft: DraftCampaign) -> dict[str, Any]:
    getters: dict[str, Callable[[DraftCampaign], Any]] = {
        "column.query": lambda d: d.column_query,
        "column.ground_truth": lambda d: d.column_ground_truth,
        "task_description": lambda d: d.raw_task_description,
        "answer_space": lambda d: list(d.answer_space() or ()),
    }
    return {key: getter(draft) for key, getter in getters.items()}


def origin_projection(draft: DraftCampaign) -> dict[str, Any]:
    """The authored prompts, not the committed origin: what a commit derives from the task and the
    labels moves with fields this projection already holds."""
    projection: dict[str, Any] = {
        "slug": draft.slug,
        "basis": basis_json(draft.basis),
        "scoring_matcher": draft.scoring_matcher,
        "pipeline_steps": list(draft.pipeline_steps),
        "pipeline_overlay": draft.pipeline_overlay,
        "optimization_overrides": draft.optimization_overrides,
        "candidate_library": list(draft.candidate_library),
        "reused_origin_id": draft.reused_origin_id,
    }
    for field_key, value in (
        ("column.query", draft.column_query),
        ("column.ground_truth", draft.column_ground_truth),
        ("task_description", draft.raw_task_description),
    ):
        if draft.field_provenance.get(field_key) is Provenance.CONFIRMED:
            projection[field_key] = value
    for step, fields in draft.origin_prompts.items():
        for name, text in fields.items():
            projection[f"prompt.{step}.{name}"] = text
    return projection


def origin_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {"from": before.get(key), "to": after.get(key)}
        for key in before.keys() | after.keys()
        if before.get(key) != after.get(key)
    }


def save_checkin_draft(
    stores: Stores, draft: DraftCampaign, *, resolution: OriginResolution | None = None
) -> DraftCampaign:
    stores.drafts.write_draft(draft.draft_id, draft.to_disk())
    block = resolution or OriginResolution()
    stores.drafts.write_resolution(draft.draft_id, block.model_dump(mode="json"))
    return draft


def load_resolution(stores: Stores, draft: DraftCampaign) -> OriginResolution:
    block = stores.drafts.load_resolution(draft.draft_id)
    return OriginResolution.model_validate(block) if block else OriginResolution()
