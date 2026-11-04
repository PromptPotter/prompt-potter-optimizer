"""A check-in draft's two files, `draft.json` and `resolution.json`, as their reader and their
`store/data_root.py::KINDS` rows take them."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field

from promptpotter.domain.command_kinds import EditDraftCampaignPayload
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.strict_model import FrozenModel, StrictModel


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


class DraftFile(StrictModel):
    """`draft.json`: every field of `application/datasets/draft_campaign.py::DraftCampaign` but its address."""

    owner_user_id: str
    slug: str
    n_samples: int
    sample_preview: tuple[dict[str, str], ...]
    basis: DraftBasis
    scoring_matcher: str
    scoring_dials: str
    raw_task_description: str | None
    pipeline_overlay: dict[str, Any]
    created_at: str
    updated_at: str
    headers: tuple[str, ...]
    column_query: str | None
    column_ground_truth: str | None
    column_label_sets: dict[str, tuple[str, ...]]
    first_blank_row: dict[str, int]
    field_provenance: dict[str, Provenance]
    source_file: str
    origin_prompts: dict[str, dict[str, str]]
    decomposed_task_context: dict[str, Any]
    pipeline_steps: list[str]
    optimization_overrides: dict[str, Any]
    candidate_library: tuple[str, ...]
    reused_origin_id: str
    resolver_ran: bool


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
