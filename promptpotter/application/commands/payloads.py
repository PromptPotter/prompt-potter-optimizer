from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.draft_campaign import EditDraftPatch, OptimizationOverrides
from promptpotter.domain.campaign import Optimize, Purpose
from promptpotter.domain.command_kinds import (
    LOOP_TAKEN,
    CampaignPayload,
    CommandKind,
    CommandPayload,
    CyclePayload,
)
from promptpotter.domain.phases import INNER_LAUNCH_REFUSAL
from promptpotter.domain.results import VerifyStrategy
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel, WireInt
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.shared.errors import PayloadInvalidError

__all__ = ["KIND_OF_PAYLOAD", "PAYLOAD_MODEL_FOR_KIND", "CommandAcceptedBody"]

_OUTER_OWNS_TREE = "An inner run's cycles live and die with the outer campaign."


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


class DeleteCyclePayload(CyclePayload):
    inner_refusal: ClassVar[str | None] = _OUTER_OWNS_TREE


class CleanupEmptyCyclesPayload(CyclePayload):
    inner_refusal: ClassVar[str | None] = _OUTER_OWNS_TREE


class ChangeRunLimitsPayload(CyclePayload):
    inner_refusal: ClassVar[str | None] = (
        "An inner run spends under the outer run's ceiling. Change the outer run's limits instead."
    )

    # An axis it does not name stays as it stands; a named null lifts it.
    budget: Budget

    @model_validator(mode="after")
    def _names_an_axis(self) -> ChangeRunLimitsPayload:
        """The domain error, not ``ValueError``: Pydantic wraps that one, and the CLI builds this model too."""
        if not self.budget.named:
            raise PayloadInvalidError(
                "change-run-limits requires a budget naming at least one of usd / tokens / "
                "rounds / wall_clock_s."
            )
        return self


class StartRunPayload(CyclePayload):
    """A run of an existing cycle: what bounds it (``budget``, ``step_rounds``) and the shape it
    takes (``mode``)."""

    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL

    budget: Budget = Budget()
    step_rounds: WireInt | None = Field(default=None, ge=1, le=100)
    mode: RunMode = RunMode()


class StepCyclePayload(CyclePayload):
    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL

    step_rounds: WireInt = Field(default=1, ge=1, le=100)


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


class _LifecyclePayload(CampaignPayload):
    reason: str = Field(default="", max_length=512)


class ArchiveCampaignPayload(_LifecyclePayload):
    pass


class UnarchiveCampaignPayload(_LifecyclePayload):
    pass


class DeleteCampaignPayload(_LifecyclePayload):
    keep_results: bool = False


LifecyclePayload = ArchiveCampaignPayload | UnarchiveCampaignPayload | DeleteCampaignPayload


class SetCampaignLabelPayload(CampaignPayload):
    # Required, and `""` is the CLEAR: a default would give "clear" two spellings.
    label: str = Field(max_length=200)


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


class _OnDraft(CommandPayload):
    draft_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class EditDraftCampaignPayload(_OnDraft):
    # Typed: a dict defers validation past the capability gate. Required: no patch records a no-op.
    patch: EditDraftPatch


class ResolveOriginPayload(_OnDraft):
    message: str = Field(default="", max_length=4000)


class StartCheckinPayload(_OnDraft):
    """The draft says what the campaign IS; ``budget`` bounds what THIS launch spends, and no
    draft field holds it. Acked at admission, so it rides the draft's ledger into the root cycle."""

    budget: Budget = Budget()
    diag: bool = Field(default=False, description="Run the diagnostic shape.")


# Recorded on the draft's own ledger, which its Start moves into the root cycle.
DraftPayload = EditDraftCampaignPayload | ResolveOriginPayload | StartCheckinPayload


class DiscardDraftPayload(_OnDraft):
    """What its check-in calls cost is banked to the workspace, as a deleted campaign's is."""


class CancelQueuedRunPayload(CommandPayload):
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class SetConcurrentCyclesPayload(CommandPayload):
    max_concurrent_cycles: WireInt = Field(ge=1)


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


# `compact-archive` stays OUTSIDE: the generic door then refuses it by type.
WorkspacePayload = (
    CancelQueuedRunPayload | SetConcurrentCyclesPayload | MintCampaignPayload | DiscardDraftPayload
)

# Resolved by NAME (`CommandKind.payload_name`): a kind whose model is missing fails right here.
KIND_OF_PAYLOAD: dict[type[CommandPayload], CommandKind] = {
    LOOP_TAKEN.get(kind) or globals()[kind.payload_name]: kind for kind in CommandKind
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
    """The 202 response shape declared in ``api-openapi.yaml``."""

    command_id: str = Field(description="Stable id of the appended `CommandRecord`.")
