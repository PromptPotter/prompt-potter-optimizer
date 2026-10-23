from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.draft_campaign import EditDraftPatch, OptimizationOverrides
from promptpotter.domain.campaign import Optimize, Purpose
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CommandKind,
    CommandPayload,
    DraftPayload,
)
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.layout import validate_dataset_name


class CompactArchivePayload(CommandPayload, kind=CommandKind.COMPACT_ARCHIVE):
    """``apply`` defaults to False on every mode, including the destructive one: a preview is what
    the operator consents on, so the write has to be asked for rather than defaulted into."""

    mode: Literal["compact", "restore", "purge-cold"]
    dataset: str | None = Field(default=None, min_length=1, max_length=64)
    apply: bool = False

    @model_validator(mode="after")
    def _dataset_is_a_dataset_name(self) -> CompactArchivePayload:

        if self.dataset is not None:
            validate_dataset_name(self.dataset)
        return self


class EditDraftCampaignPayload(DraftPayload, kind=CommandKind.EDIT_DRAFT_CAMPAIGN):
    # Typed: a dict defers validation past the capability gate. Required: no patch records a no-op.
    patch: EditDraftPatch


class ResolveOriginPayload(DraftPayload, kind=CommandKind.RESOLVE_ORIGIN):
    message: str = Field(default="", max_length=4000)


class StartCheckinPayload(DraftPayload, kind=CommandKind.START_CHECKIN):
    """The draft says what the campaign IS; ``budget`` bounds what THIS launch spends, and no
    draft field holds it. Acked at admission, so it rides the draft's ledger into the root cycle."""

    budget: Budget = Budget()
    diag: bool = Field(default=False, description="Run the diagnostic shape.")


class MintCampaignPayload(CommandPayload, kind=CommandKind.MINT_CAMPAIGN):
    dataset_name: str = Field(min_length=1, max_length=64)
    budget: Budget = Budget()
    optimization: OptimizationOverrides | None = Field(
        default=None,
        description="Laid over the dataset's own `optimization` before the mint freezes it; only "
        "the fields sent move anything",
    )
    purpose: Purpose = Field(
        default=Optimize(),
        description="`compare` mints a controlled arm of its head-to-head, declared by its first "
        "arm; an arm off the declared instrument or budget is refused 409",
    )
    diag: bool = Field(
        default=False,
        description="Run the diagnostic shape. A fresh mint has no ledger to rewind or replay, so "
        "the other run-mode fields are `start-run`'s alone.",
    )
    campaign_config: CampaignConfig | None = Field(
        default=None,
        description="A campaign declaration standing in for the dataset's own `dataset.yaml::campaign`; "
        "`optimization` still lays over it",
    )
    task_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=16384,
        description="The task description to frame from, in place of the dataset's own",
    )

    @property
    def optimization_sent(self) -> dict[str, Any]:
        opt = self.optimization
        return {} if opt is None else opt.model_dump(mode="json", exclude_unset=True)

    @model_serializer(mode="wrap")
    def _record_what_was_sent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """The ``CommandRecord`` carries this dump; a default there records a knob nobody set."""
        data: dict[str, Any] = handler(self)
        data["optimization"] = self.optimization_sent or None
        declared = self.campaign_config
        data["campaign_config"] = (
            None if declared is None else declared.model_dump(mode="json", exclude_unset=True)
        )
        return data

    @model_validator(mode="after")
    def _name_is_a_dataset_name(self) -> MintCampaignPayload:
        validate_dataset_name(self.dataset_name)
        return self


# The last payload model is declared above, so a kind carrying none fails right here.
if _unpaired := sorted(set(CommandKind) - PAYLOAD_MODEL_FOR_KIND.keys()):
    raise RuntimeError(f"command kinds carried by no payload model: {_unpaired}")


class CommandAcceptedBody(StrictModel):
    """What a command whose applied form answers nothing returns: the record it appended."""

    command_id: str = Field(description="Stable id of the appended `CommandRecord`.")
