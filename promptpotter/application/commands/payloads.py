from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.draft_campaign import EditDraftPatch, OptimizationOverrides
from promptpotter.config.settings import DEFAULT_BACKEND_ID, DEFAULT_BACKEND_URL
from promptpotter.domain.campaign import ArmRequest
from promptpotter.domain.command_kinds import ALL_DISPATCHED_KINDS
from promptpotter.domain.launch_limits import LaunchLimits, RoundsCap
from promptpotter.domain.phases import (
    INNER_GATE_REFUSAL,
    INNER_LAUNCH_REFUSAL,
    INNER_PAUSE_REFUSAL,
    INNER_SKIP_REFUSAL,
    GateDecision,
)
from promptpotter.domain.results import VerifyStrategy
from promptpotter.domain.spend import SpendCeilings
from promptpotter.domain.strict_model import StrictModel, WireInt
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.shared.errors import PayloadInvalidError

__all__ = ["KIND_OF_PAYLOAD", "PAYLOAD_MODEL_FOR_KIND", "CommandAcceptedBody", "CommandPayload"]


class CommandPayload(StrictModel):
    """Base of every payload on the generic ``POST /commands/{kind}`` route. ``StrictModel`` forbids
    extras, so THE MODEL IS the accepted-key set — there is no list to fall out of step with it."""


class CampaignPayload(CommandPayload):
    campaign_id: str = Field(min_length=1, max_length=128)


_OUTER_OWNS_TREE = "An inner run's cycles live and die with the outer campaign."


class CyclePayload(CampaignPayload):
    """A cycle's address: the root hop, plus the ``?descend=`` tail the reads take. Every
    cycle-scoped kind is ADDRESSED alike; ``inner_refusal`` is each kind's own answer to whether
    it acts on an inner run, and ``dispatcher.py::dispatch_cycle_command`` is its one reader. A
    run verb's answer is the sentence its cycle's served admission carries
    (``domain/phases.py::RunState.inner``), so the control and the refusal say one thing."""

    cycle_id: str = Field(min_length=1, max_length=128)
    # Excluded: the dispatcher spends it resolving the leaf, so a recorded tail addresses it twice.
    descend: str | None = Field(default=None, max_length=512, exclude=True)

    # ``None`` = acts on an inner run. Undeclared here, so a new kind fails at import until it answers.
    inner_refusal: ClassVar[str | None]


class RunShape(StrictModel):
    """The SHAPE a run of an existing cycle takes — the fields of ``runner/entry.py::RunMode`` a
    caller may choose, so the terminal's resume flags and the wire are one vocabulary, read by
    ``launching.py::run_mode_of``. How far a run goes is no shape: it is a limit (``LaunchLimits``)."""

    from_round: WireInt | None = Field(
        default=None,
        ge=0,
        description="Rewind in place to this round before running; unset continues the ledger.",
    )
    no_divergence_check: bool = Field(
        default=False, description="Accept a replay that diverges from the record."
    )
    fork_on_divergence: bool = Field(
        default=False, description="Branch a sibling cycle where the replay diverges, and run it."
    )
    diag: bool = Field(
        default=False,
        description="The diagnostic shape; a `start-run` of a cycle that finished one runs a "
        "counted sibling.",
    )


class ForkCyclePayload(CyclePayload, RunShape):
    """The cut, and the shape of the fork's first run (``RunShape``) — one act, so one command.
    A parent a producer still holds is paused by the applier once the fork's run is admitted."""

    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL

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


class SkipSearchpointPayload(CyclePayload):
    inner_refusal: ClassVar[str | None] = INNER_SKIP_REFUSAL


class DeleteCyclePayload(CyclePayload):
    inner_refusal: ClassVar[str | None] = _OUTER_OWNS_TREE


class CleanupEmptyCyclesPayload(CyclePayload):
    inner_refusal: ClassVar[str | None] = _OUTER_OWNS_TREE


class PauseCyclePayload(CyclePayload):
    inner_refusal: ClassVar[str | None] = INNER_PAUSE_REFUSAL

    reason: str = Field(default="", max_length=512)


class SetSampleLookaheadPayload(CyclePayload):
    # Throughput is what an inner run answers for itself: the outer's depth is never inherited.
    inner_refusal: ClassVar[str | None] = None

    cells: WireInt = Field(ge=1, description="1 disarms.")
    auto: bool = Field(
        default=False,
        description="As deep as the stop rules allow, every round, until pressed off.",
    )


class OriginGateDecisionPayload(CyclePayload):
    inner_refusal: ClassVar[str | None] = INNER_GATE_REFUSAL

    decision: GateDecision


class ChangeRunLimitsPayload(CyclePayload):
    inner_refusal: ClassVar[str | None] = (
        "An inner run spends under the outer run's ceiling. Change the outer run's limits instead."
    )

    ceiling: SpendCeilings = SpendCeilings()
    # Absent leaves the round cap alone; an explicit `null` lifts it (`rounds_cap`).
    max_rounds: WireInt | None = Field(default=None, ge=0)

    @property
    def rounds_cap(self) -> RoundsCap | None:
        if "max_rounds" not in self.model_fields_set:
            return None
        return RoundsCap(max_rounds=self.max_rounds)

    @model_validator(mode="after")
    def _at_least_one_ceiling(self) -> ChangeRunLimitsPayload:
        """The domain error, not ``ValueError``: Pydantic wraps that one, and the CLI builds this model too."""
        if self.ceiling == SpendCeilings() and self.rounds_cap is None:
            raise PayloadInvalidError(
                "change-run-limits requires at least one of ceiling.usd / ceiling.tokens / "
                "max_rounds."
            )
        return self

    @model_serializer(mode="wrap")
    def _omit_unmoved_rounds(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """The ``CommandRecord`` carries this dump: a ``null`` there records a lift nobody asked for."""
        data: dict[str, Any] = handler(self)
        if "max_rounds" not in self.model_fields_set:
            data.pop("max_rounds", None)
        return data


class StartRunPayload(CyclePayload, LaunchLimits, RunShape):
    """A run of an existing cycle: what bounds it (``LaunchLimits``) and the shape it takes
    (``RunShape``)."""

    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL


class StepCyclePayload(CyclePayload):
    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL

    rounds: WireInt = Field(default=1, ge=1, le=100)


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


class RegisterBackendPayload(CommandPayload):
    name: str = Field(min_length=1, max_length=128)
    backend_type: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=2048)
    # Auto-derived from `name` when omitted (`workspace_edits.py::_slugify_backend_id`).
    id: str | None = Field(default=None, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")


class ReplaceDatasetPayload(CommandPayload):
    slug: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _slug_is_a_dataset_name(self) -> ReplaceDatasetPayload:

        validate_dataset_name(self.slug)
        return self


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


class _CheckinPayload(CommandPayload):
    """``draft_id`` and ``campaign_id`` are the same id, re-keyed at ``create_checkin_campaign``;
    each check-in verb names it whichever way its wire schema does."""


class EditDraftCampaignPayload(_CheckinPayload):
    draft_id: str = Field(min_length=8, max_length=128)
    # Typed: a dict defers validation past the capability gate. Required: no patch records a no-op.
    patch: EditDraftPatch

    @property
    def checkin_campaign_id(self) -> str:
        return self.draft_id


class ResolveOriginPayload(_CheckinPayload):
    draft_id: str = Field(min_length=8, max_length=128)
    message: str = Field(default="", max_length=4000)

    @property
    def checkin_campaign_id(self) -> str:
        return self.draft_id


class StartCheckinPayload(_CheckinPayload, LaunchLimits):
    """The draft says what the campaign IS; these limits bound what THIS launch spends.

    No draft field holds them. Carrying neither is what made the web Start launch under a bare
    ``LaunchLimits()`` while CLI ``new <file>`` — the same three seams, one argv away — passed a
    halt target and a ceiling."""

    campaign_id: str = Field(min_length=8, max_length=128)
    diag: bool = Field(default=False, description="Run the diagnostic shape.")
    backend_url: str = Field(
        default=DEFAULT_BACKEND_URL,
        min_length=1,
        max_length=2048,
        description="Where the draft's connector reaches its backend",
    )
    backend_id: str = Field(
        default=DEFAULT_BACKEND_ID,
        min_length=1,
        max_length=64,
        description="The registry id that backend is recorded under",
    )

    @property
    def checkin_campaign_id(self) -> str:
        return self.campaign_id


CheckinPayload = EditDraftCampaignPayload | ResolveOriginPayload | StartCheckinPayload


class CancelQueuedRunPayload(CommandPayload):
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class SetConcurrentCyclesPayload(CommandPayload):
    max_concurrent_cycles: WireInt = Field(ge=1)


class MintCampaignPayload(CommandPayload, LaunchLimits):
    dataset_name: str = Field(min_length=1, max_length=64)
    optimization: OptimizationOverrides | None = Field(
        default=None,
        description="Laid over the dataset's own `optimization` before the mint freezes it; only "
        "the fields sent move anything",
    )
    arm: ArmRequest | None = Field(
        default=None,
        description="Mint as a controlled arm of this head-to-head, declared by its first arm; "
        "an arm off the declared instrument or budget is refused 409",
    )
    diag: bool = Field(
        default=False,
        description="Run the diagnostic shape. A fresh mint has no ledger to rewind or replay, so "
        "the other run-mode fields are `start-run`'s alone.",
    )
    campaign_config: CampaignConfig | None = Field(
        default=None,
        description="A campaign declaration standing in for the dataset's own `campaign.yaml`; "
        "`optimization` still lays over it",
    )
    task_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=16384,
        description="The task description to frame from, in place of the dataset's own",
    )
    backend_url: str = Field(
        default=DEFAULT_BACKEND_URL,
        min_length=1,
        max_length=2048,
        description="Where the dataset's connector reaches its backend",
    )
    backend_id: str = Field(
        default=DEFAULT_BACKEND_ID,
        min_length=1,
        max_length=64,
        description="The registry id that backend is recorded under",
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


# `replace-dataset` / `compact-archive` stay OUTSIDE: the generic door then refuses them by type.
WorkspacePayload = (
    RegisterBackendPayload
    | CancelQueuedRunPayload
    | SetConcurrentCyclesPayload
    | MintCampaignPayload
)

PAYLOAD_MODEL_FOR_KIND: dict[str, type[CommandPayload]] = {
    "fork-cycle": ForkCyclePayload,
    "skip-searchpoint": SkipSearchpointPayload,
    "delete-cycle": DeleteCyclePayload,
    "cleanup-empty-cycles": CleanupEmptyCyclesPayload,
    "pause-cycle": PauseCyclePayload,
    "set-sample-lookahead": SetSampleLookaheadPayload,
    "origin-gate-decision": OriginGateDecisionPayload,
    "change-run-limits": ChangeRunLimitsPayload,
    "start-run": StartRunPayload,
    "step-cycle": StepCyclePayload,
    "verify-candidate": VerifyCandidatePayload,
    "grade-bench": GradeBenchPayload,
    "archive-campaign": ArchiveCampaignPayload,
    "delete-campaign": DeleteCampaignPayload,
    "unarchive-campaign": UnarchiveCampaignPayload,
    "set-campaign-label": SetCampaignLabelPayload,
    "register-backend": RegisterBackendPayload,
    "mint-campaign": MintCampaignPayload,
    "cancel-queued-run": CancelQueuedRunPayload,
    "set-concurrent-cycles": SetConcurrentCyclesPayload,
    "replace-dataset": ReplaceDatasetPayload,
    "compact-archive": CompactArchivePayload,
    "edit-draft-campaign": EditDraftCampaignPayload,
    "resolve-origin": ResolveOriginPayload,
    "start-checkin": StartCheckinPayload,
}
if set(PAYLOAD_MODEL_FOR_KIND) != ALL_DISPATCHED_KINDS:
    raise RuntimeError(
        "PAYLOAD_MODEL_FOR_KIND out of sync with the dispatched command set: "
        f"{ALL_DISPATCHED_KINDS.symmetric_difference(PAYLOAD_MODEL_FOR_KIND)}"
    )

KIND_OF_PAYLOAD: dict[type[CommandPayload], str] = {
    model: kind for kind, model in PAYLOAD_MODEL_FOR_KIND.items()
}
if len(KIND_OF_PAYLOAD) != len(PAYLOAD_MODEL_FOR_KIND):
    raise RuntimeError("two command kinds share one payload type; give each its own.")
_unanswered = sorted(
    kind
    for kind, model in PAYLOAD_MODEL_FOR_KIND.items()
    if issubclass(model, CyclePayload) and not hasattr(model, "inner_refusal")
)
if _unanswered:
    raise RuntimeError(f"cycle-scoped kinds declaring no `inner_refusal`: {_unanswered}")


class DatasetReplaced(StrictModel):
    """The ``replace-dataset`` response: the subject echoed, nothing more — the counts and the
    versioned slug are on the migration's own record, and no caller reads them off the wire."""

    slug: str = Field(description="The dataset name now free for new data.")


class CommandAcceptedBody(StrictModel):
    """The 202 response shape declared in ``api-openapi.yaml``."""

    command_id: str = Field(description="Stable id of the appended `CommandRecord`.")
    correlation_id: str = Field(description="Echo of the request's `Idempotency-Key`.")
    ledger_sequence: int = Field(
        description="Offset at which the `CommandRecord` was appended.", ge=0
    )
