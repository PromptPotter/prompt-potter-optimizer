from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from pydantic import Field, computed_field

from promptpotter.domain.checkin_draft import (
    CheckinQuestion,
    DraftFile,
    OriginResolution,
    SetupStep,
)
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.worded import Worded

if TYPE_CHECKING:
    from promptpotter.application.bench.task_context import OriginQuestion
    from promptpotter.application.pipeline_resolve import DraftPipeline
    from promptpotter.infrastructure.store.stores import Stores


SETUP_STEP_TITLES: Worded[SetupStep, str] = Worded(
    {
        SetupStep.OPTIMIZER: "Optimizer",
        SetupStep.DATA: "Data",
        SetupStep.SCORING: "Scoring",
        SetupStep.PIPELINE: "Pipeline",
        SetupStep.HEAD_TO_HEAD: "Head-to-head",
        SetupStep.RUN: "Run bounds",
    }
)
assert SETUP_STEP_TITLES.keys() == set(SetupStep)


class ChecklistField(NamedTuple):
    # The `EditDraftPatch` path one answer writes: a draft attribute, or a path into its partial
    # `config`, which the server folds in by that path (a node's model).
    patch_path: tuple[str, ...]
    # The step whose fold sets it; None where the chat answers it.
    step: SetupStep | None
    # What a proposal of a value reads as.
    proposal: str
    # Its answer is one of the upload's headers.
    of_headers: bool = False


#: THE checklist: every field the gate keys a gap by, and what a resolver may propose or ask.
CHECKLIST_FIELDS: dict[str, ChecklistField] = {
    "column.query": ChecklistField(
        ("column_query",), SetupStep.DATA, "Read the input from “{}”", of_headers=True
    ),
    "column.ground_truth": ChecklistField(
        ("column_ground_truth",), SetupStep.DATA, "Grade against “{}”", of_headers=True
    ),
    "task_description": ChecklistField(("raw_task_description",), None, "Set the task: “{}”"),
}
_NODE_MODEL = re.compile(r"node\.(?P<node>.+)\.model")


def checklist_field(field: str) -> ChecklistField | None:
    if node := _NODE_MODEL.fullmatch(field):
        return ChecklistField(
            ("config", "pipeline_overlay", node["node"], "model"),
            SetupStep.PIPELINE,
            f"Run {node['node']} on “{{}}”",
        )
    return CHECKLIST_FIELDS.get(field)


def patch_at(path: tuple[str, ...], value: object) -> dict[str, object]:
    """The `EditDraftPatch` body writing *value* at *path*."""
    out: dict[str, object] = {path[-1]: value}
    for key in reversed(path[:-1]):
        out = {key: out}
    return out


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


def checkin_questions(draft: DraftFile, asked: Iterable[OriginQuestion]) -> list[CheckinQuestion]:
    """A question on no checklist field is dropped, as `raised_commands` drops a finding."""
    questions: list[CheckinQuestion] = []
    for question in asked:
        row = checklist_field(question.field)
        if row is None:
            continue
        questions.append(
            CheckinQuestion(
                field=question.field,
                path=list(row.patch_path),
                step=row.step,
                prompt=question.prompt,
                options=question.options or (list(draft.headers) if row.of_headers else []),
            )
        )
    return questions


def origin_readiness(draft: DraftFile, pipeline: DraftPipeline) -> OriginReadiness:
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


def columns_confirmed(draft: DraftFile) -> bool:
    """The gate's column clause: only past it may anything cut the upload's rows."""
    return not _column_gaps(draft)


def _column_gaps(draft: DraftFile) -> list[FieldGap]:
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
    draft: DraftFile,
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
    draft: DraftFile,
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
    configs = pipeline.params.nodes
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


def field_values(draft: DraftFile) -> dict[str, Any]:
    getters: dict[str, Callable[[DraftFile], Any]] = {
        "column.query": lambda d: d.column_query,
        "column.ground_truth": lambda d: d.column_ground_truth,
        "task_description": lambda d: d.raw_task_description,
        "answer_space": lambda d: list(d.answer_space() or ()),
    }
    return {key: getter(draft) for key, getter in getters.items()}


def save_checkin_draft(
    stores: Stores, draft: DraftFile, *, resolution: OriginResolution | None = None
) -> DraftFile:
    stores.drafts.write_draft(draft)
    stores.drafts.write_resolution(draft.draft_id, resolution or OriginResolution())
    return draft


def load_resolution(stores: Stores, draft: DraftFile) -> OriginResolution:
    return stores.drafts.load_resolution(draft.draft_id) or OriginResolution()
