"""A check-in draft's two files, `draft.json` and `resolution.json`, as their reader and their
`store/data_root.py::KINDS` rows take them."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, PrivateAttr

from promptpotter.domain.command_kinds import EditDraftCampaignPayload
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema


class UploadBasis(FrozenModel):
    """An uploaded table, run on the connector the operator picked."""

    kind: Literal["upload"] = "upload"
    connector: str
    # The kept file's name under `TenantDatasetStore.upload_path`.
    kept: str


class DatasetBasis(FrozenModel):
    """A committed dataset, run on the backend its definition names."""

    kind: Literal["dataset"] = "dataset"
    dataset: str


type DraftBasis = Annotated[UploadBasis | DatasetBasis, Field(discriminator="kind")]


def closed_answer_format(labels: tuple[str, ...]) -> str:
    return "Choose exactly one of these labels: " + " | ".join(labels)


class DraftFile(FrozenModel):
    """`draft.json`, the check-in's draft. ``config`` is a partial `CampaignConfig`: the keys the
    draft sets, laid over its basis (`pipeline_resolve.py::draft_declared`)."""

    # Its address, beside the file: the directory `DraftStore` keeps it in, stamped by `at`.
    _draft_id: str = PrivateAttr()
    # Another user's draft is not found, as their campaign is.
    owner_user_id: str
    slug: str
    n_samples: int
    sample_preview: tuple[dict[str, str], ...]
    basis: DraftBasis
    config: dict[str, Any]
    raw_task_description: str | None
    created_at: str
    updated_at: str
    headers: tuple[str, ...]
    column_query: str | None
    column_ground_truth: str | None
    column_label_sets: dict[str, tuple[str, ...]]
    # Per header, the first row (1-based, file order) whose cell is blank: that column cuts no bank.
    first_blank_row: dict[str, int]
    field_provenance: dict[str, Provenance]
    source_file: str
    origin_prompts: dict[str, dict[str, str]]
    decomposed_task_context: dict[str, Any]
    candidate_library: tuple[str, ...]
    # Non-empty routes the check-in's Start through the ``origin_override`` seed.
    reused_origin_id: str
    # A resolver turn has run on it: an edit keeps it, so a reopen reviews, never re-resolves.
    resolver_ran: bool

    def at(self, draft_id: str) -> DraftFile:
        addressed = self.model_copy()
        addressed._draft_id = draft_id
        return addressed

    @property
    def draft_id(self) -> str:
        return self._draft_id

    @property
    def dataset_name(self) -> str:
        """The dataset it runs on: the one it was cut from, or an upload's, minted under its slug."""
        return self.basis.dataset if isinstance(self.basis, DatasetBasis) else self.slug

    def resolution_pending(self) -> bool:
        """The task is on file and no resolver turn ever set it up: a reopen resolves, while an
        origin's or the operator's authored prompts are never re-proposed over."""
        return (
            self.raw_task_description is not None
            and not self.resolver_ran
            and not self.authored_prompts()
        )

    def confirmed_task(self) -> str:
        """The task a Start commits: the gate refuses every Start before one is confirmed."""
        assert self.raw_task_description is not None, "a Start passed a gate holding no task"
        return self.raw_task_description

    def answer_space(self) -> tuple[str, ...] | None:
        if self.column_ground_truth is None:
            return None
        return self.column_label_sets.get(self.column_ground_truth)

    def authored_prompts(self) -> bool:
        # Wiped strings count as blank: emptied fields read unauthored, as an empty map does.
        return any(
            text.strip() for fields in self.origin_prompts.values() for text in fields.values()
        )

    def committed_prompts(self, chain: PipelineSchema) -> dict[str, dict[str, str]]:
        """Over the *chain* `pipeline_resolve.draft_pipeline` runs. The raw task and the label
        enumeration seed a field only on a chain of ONE prompt node."""
        prompt_nodes = chain.prompt_nodes()
        alone = len(prompt_nodes) == 1
        return {
            node.name: self._committed_fields(node.name, node.prompt_fields if alone else ())
            for node in prompt_nodes
        }

    def _committed_fields(self, step: str, seeded: tuple[str, ...]) -> dict[str, str]:
        authored = self.origin_prompts.get(step, {})
        fields = dict(authored)
        if "instruction" in seeded and not any(text.strip() for text in authored.values()):
            fields = {"instruction": self.confirmed_task()}
        # Appended even when answer_format is blank: the optimizer prompts promise the labels.
        labels = self.answer_space()
        if labels and "answer_format" in seeded:
            enumeration = closed_answer_format(labels)
            fmt = fields.get("answer_format", "").strip()
            if enumeration not in fmt:
                fields["answer_format"] = f"{fmt}\n{enumeration}" if fmt else enumeration
        return fields

    def patch(self, **changes: object) -> DraftFile:
        return self.model_copy(update={"updated_at": utcnow_iso(), **changes})

    def confirm_columns(
        self, *, query_col: str | None = None, ground_truth_col: str | None = None
    ) -> DraftFile:
        provenance = dict(self.field_provenance)
        changes: dict[str, str] = {}
        if query_col is not None:
            changes["column_query"] = query_col
            provenance["column.query"] = Provenance.CONFIRMED
        if ground_truth_col is not None:
            changes["column_ground_truth"] = ground_truth_col
            provenance["column.ground_truth"] = Provenance.CONFIRMED
        return self.patch(field_provenance=provenance, **changes)

    def apply_resolution(
        self,
        *,
        values: dict[str, object] | None = None,
        provenance: dict[str, Provenance] | None = None,
    ) -> DraftFile:
        merged = dict(self.field_provenance)
        if provenance:
            merged.update(provenance)
        return self.patch(field_provenance=merged, **(values or {}))


class SetupStep(StrEnum):
    """The check-in's steps, in the order a check-in shows them: each a fold that sets one part of
    the campaign, keyed as `setup_schema` keys the steps it serves."""

    OPTIMIZER = "optimizer"
    DATA = "data"
    SCORING = "scoring"
    PIPELINE = "pipeline"
    HEAD_TO_HEAD = "head_to_head"
    RUN = "run"


class CheckinQuestion(StrictModel):
    """A resolver question one answer closes, projected through `CHECKLIST_FIELDS`."""

    field: str
    path: list[str] = Field(description="The `EditDraftPatch` path the answer is written at.")
    step: SetupStep | None
    prompt: str
    options: list[str] = Field(description="The answers it takes; empty is free text.")


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
    """`resolution.json`: the last resolver turn; the mint gate is the draft's `readiness`,
    recomputed on every read."""

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
