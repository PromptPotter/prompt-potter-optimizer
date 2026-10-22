from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.draft_campaign import EditDraftPatch, OptimizationOverrides
from promptpotter.domain.campaign import Optimize, Purpose
from promptpotter.domain.command_kinds import (
    PAYLOAD_IN_DOMAIN,
    CommandKind,
    CommandPayload,
    CyclePayload,
    DraftAddressedPayload,
)
from promptpotter.domain.phases import INNER_LAUNCH_REFUSAL
from promptpotter.domain.results import VerifyStrategy
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel, WireInt
from promptpotter.infrastructure.store.layout import validate_dataset_name


class ForkCyclePayload(CyclePayload):
    """The cut, and the shape of the fork's first run (``mode``) — one act, so one command.
    A parent a producer still holds is paused by the applier once the fork's run is admitted."""

    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL

    mode: RunMode = RunMode()
    round: int = Field(default=0, ge=0)
    candidate_id: str = Field(default="", max_length=128)
    # Validated into a typed `CycleSeed` at the applier, which stamps the provenance the wire omits.
    seed: dict[str, Any]
    reason: str = Field(default="", max_length=512)
    keep_rounds: bool = Field(
        default=False,
        description=(
            "False (the default) is `operator_steered`: a clean offshoot from the origin, "
            "re-scoring the edited searchpoint. True is `operator_rewind` — rounds 0..round-1 "
            "are lifted and the fork continues at `round` under the seed's overrides, which is "
            "what an 'apply this from here' press means and what the terminal spells "
            "`resume --rewind N`."
        ),
    )


class StartRunPayload(CyclePayload):
    """A run of an existing cycle: what bounds it (``budget``, ``step_rounds``) and the shape it
    takes (``mode``)."""

    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL

    budget: Budget = Budget()
    step_rounds: WireInt | None = Field(default=None, ge=1, le=100)
    mode: RunMode = RunMode()


class VerifyCandidatePayload(CyclePayload):
    """``samples`` omitted is the ANSWER, not an absence: the count is derived from the
    per-candidate round budget and the rounds run since this cycle's last verification
    (``application/diagnostics/verify.py::derive_verify_samples``). A larger explicit count is
    refused, naming the budget — which is what keeps one click on a million-row dataset from being
    a million-cell bill.

    Addressed as the ``evidence`` read addresses a searchpoint — cycle, descent, ``candidate_id``
    — so an L4 inner candidate is as reachable as a top-level one."""

    inner_refusal: ClassVar[str | None] = None

    candidate_id: str = Field(min_length=1, max_length=128)
    samples: WireInt | None = Field(default=None, ge=1, le=10_000)
    strategy: VerifyStrategy = "random"
    seed: WireInt | None = None


class GradeBenchPayload(CyclePayload):
    """The cycle holding its campaign's line: the pass grades the campaign's result, so any other
    cycle is refused."""

    inner_refusal: ClassVar[str | None] = (
        "The bench grades a campaign's line, and an inner run is a cell of its outer campaign's."
    )


class CompactArchivePayload(CommandPayload):
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


class EditDraftCampaignPayload(DraftAddressedPayload):
    # Typed: a dict defers validation past the capability gate. Required: no patch records a no-op.
    patch: EditDraftPatch


class ResolveOriginPayload(DraftAddressedPayload):
    message: str = Field(default="", max_length=4000)


class StartCheckinPayload(DraftAddressedPayload):
    """The draft says what the campaign IS; ``budget`` bounds what THIS launch spends, and no
    draft field holds it. Acked at admission, so it rides the draft's ledger into the root cycle."""

    budget: Budget = Budget()
    diag: bool = Field(default=False, description="Run the diagnostic shape.")


# Recorded on the draft's own ledger, which its Start moves into the root cycle.
DraftPayload = EditDraftCampaignPayload | ResolveOriginPayload | StartCheckinPayload


class MintCampaignPayload(CommandPayload):
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


# Resolved by NAME (`CommandKind.payload_name`): a kind whose model is missing fails right here.
KIND_OF_PAYLOAD: dict[type[CommandPayload], CommandKind] = {
    PAYLOAD_IN_DOMAIN.get(kind) or globals()[kind.payload_name]: kind for kind in CommandKind
}
PAYLOAD_MODEL_FOR_KIND: dict[str, type[CommandPayload]] = {
    kind: model for model, kind in KIND_OF_PAYLOAD.items()
}
_unanswered = sorted(
    kind
    for kind, model in PAYLOAD_MODEL_FOR_KIND.items()
    if issubclass(model, CyclePayload) and not hasattr(model, "inner_refusal")
)
if _unanswered:
    raise RuntimeError(f"cycle-scoped kinds declaring no `inner_refusal`: {_unanswered}")


class CommandAcceptedBody(StrictModel):
    """What a command whose applied form answers nothing returns: the record it appended."""

    command_id: str = Field(description="Stable id of the appended `CommandRecord`.")
