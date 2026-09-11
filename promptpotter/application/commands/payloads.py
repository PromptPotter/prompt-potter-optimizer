from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, model_validator

from promptpotter.application.datasets.draft_patch import EditDraftPatch
from promptpotter.application.runner.origin_gate import GateDecision
from promptpotter.domain.command_kinds import ALL_DISPATCHED_KINDS
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.shared.errors import PayloadInvalidError

__all__ = ["PAYLOAD_MODEL_FOR_KIND", "CommandAcceptedBody", "CommandPayload"]


def _reject_bool(v: object) -> object:
    """``bool`` IS an ``int`` in Python and Pydantic coerces it, so a wire ``true`` arrives as 1 —
    which reads as "disarm" on a count and as a $1 ceiling on a budget."""
    if isinstance(v, bool):
        raise ValueError("must be a number, not a boolean")
    return v


# The two wire scalar types. `strict` is what refuses `true` where an int is meant; `allow_inf_nan`
# is what refuses `+inf`, which PASSES a bare `ge=` bound and then disarms the `BudgetGate` whose
# probe is `spent >= cap`. Neither is a default — both were bought.
WireInt = Annotated[int, Field(strict=True)]
WireFloat = Annotated[float, BeforeValidator(_reject_bool), Field(allow_inf_nan=False)]


class CommandPayload(StrictModel):
    """Base of every payload on the generic ``POST /commands/{kind}`` route. ``StrictModel`` forbids
    extras, so THE MODEL IS the accepted-key set — there is no list to fall out of step with it."""


class CampaignPayload(CommandPayload):
    campaign_id: str = Field(min_length=1, max_length=128)


class CyclePayload(CampaignPayload):
    cycle_id: str = Field(min_length=1, max_length=128)


class DescendableCyclePayload(CyclePayload):
    """Carries an address that may descend into an inner sandbox. Declaring it by INHERITANCE is
    what makes "which kinds accept a descent" a type question — every other payload forbids the key
    already. Narrow on purpose: an inner cycle inherits the outer's pause
    (``runner/entry.py::_bind_run_controls``), so a second address for pause/skip would contradict a
    working channel; throughput is what an inner run answers for itself."""

    # Excluded from the dump: the router spends it resolving the leaf, after which `campaign_id` /
    # `cycle_id` ARE the inner cycle's, so recording the tail would address the record twice.
    descend: str | None = Field(default=None, max_length=512, exclude=True)


class ForkCyclePayload(CyclePayload):
    round: int = Field(default=0, ge=0)
    candidate_id: str = Field(default="", max_length=128)
    # Kept as a dict here and validated into a typed `CycleSeed` at the applier, which stamps the
    # lineage provenance the wire omits.
    seed: dict[str, Any]
    steered_by: str = Field(default="", max_length=256)
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
    pass


class DeleteCyclePayload(CyclePayload):
    pass


class CleanupEmptyCyclesPayload(CyclePayload):
    pass


class PauseCyclePayload(CyclePayload):
    reason: str = Field(default="", max_length=512)


class SetSampleLookaheadPayload(DescendableCyclePayload):
    cells: WireInt = Field(ge=1, description="1 disarms.")


class OriginGateDecisionPayload(CyclePayload):
    # `GateDecision`, not a copy of its members: the wire vocabulary and the gate's own are one set.
    decision: GateDecision


class ChangeSpendBudgetPayload(CyclePayload):
    max_usd: WireFloat | None = Field(default=None, ge=0.0)
    max_tokens: WireInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _at_least_one_ceiling(self) -> ChangeSpendBudgetPayload:
        """An ABSENT arm means "leave it untouched", so both absent is a command that asks for
        nothing and would ack ``applied`` having moved neither ceiling. Raised as the domain error
        rather than a ``ValueError``, which Pydantic would wrap — this one propagates unwrapped, so
        the CLI building the model directly gets the same 422-shaped refusal the route does."""
        if self.max_usd is None and self.max_tokens is None:
            raise PayloadInvalidError(
                "change-spend-budget requires at least one of max_usd / max_tokens."
            )
        return self


class RunLimitsPayload(CommandPayload):
    """The three launch ceilings, bounded ONCE. ``campaign_runner.main`` validates the CLI's
    ``--halt-at`` / ``--spend-budget`` / ``--token-budget`` through this before dispatch, so the
    terminal refuses what HTTP refuses — argparse types these and bounds none of them, and an
    unbounded ceiling is one that never fires or fires on round 0."""

    halt_at_accuracy: WireFloat | None = Field(default=None, ge=0.0, le=1.0)
    # Both budget arms ride: the USD one goes blind on a model with no rate on file and the token
    # one is what still holds, so serving only USD is a half-gate.
    spend_budget_usd: WireFloat | None = Field(default=None, ge=0.0)
    token_budget: WireInt | None = Field(default=None, ge=0)


class StartRunPayload(CyclePayload, RunLimitsPayload):
    kind: Literal["new", "resume"]


class StepCyclePayload(CyclePayload):
    rounds: WireInt = Field(default=1, ge=1, le=100)


class VerifyCandidatePayload(CyclePayload):
    """``samples`` omitted is the ANSWER, not an absence: the count is derived from the
    per-candidate round budget and the rounds run since this cycle's last verification
    (``application/diagnostics/verify.py::derive_verify_samples``). A larger explicit count is
    refused, naming the budget — which is what keeps one click on a million-row dataset from being
    a million-cell bill."""

    label: str = Field(min_length=2, max_length=32, pattern=r"^C(0|\d+\.[1-9]\d*)$")
    samples: WireInt | None = Field(default=None, ge=1, le=10_000)


class LifecyclePayload(CampaignPayload):
    reason: str = Field(default="", max_length=512)
    # Only meaningful for `delete-campaign`; harmless on the other two.
    keep_results: bool = False


class SetCampaignLabelPayload(CampaignPayload):
    # Required, and `""` is the CLEAR — it restores the dataset-name fallback the display chain
    # already documents. Defaulting it too would give "clear" two spellings, omit and empty, and
    # the declared contract only ever named one.
    label: str = Field(max_length=200)


class RegisterBackendPayload(CommandPayload):
    name: str = Field(min_length=1, max_length=128)
    backend_type: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=2048)
    # Auto-derived from `name` when omitted (`dispatcher.py::_slugify_backend_id`).
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
    # TYPED, so `_validated_payload` is the whole of it — a `dict[str, Any]` defers validation into
    # the applier, past the capability gate. Required: an omitted patch is a no-op that still mints
    # a `CommandRecord` and an ack, so the ledger would carry an edit that edited nothing.
    patch: EditDraftPatch


class ResolveOriginPayload(_CheckinPayload):
    draft_id: str = Field(min_length=8, max_length=128)
    message: str = Field(default="", max_length=4000)


class StartCheckinPayload(_CheckinPayload):
    campaign_id: str = Field(min_length=8, max_length=128)


class CancelQueuedRunPayload(CommandPayload):
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class MintCampaignPayload(CommandPayload):
    dataset_name: str = Field(min_length=1, max_length=64)
    halt_at_accuracy: WireFloat | None = Field(default=None, ge=0.0, le=1.0)
    spend_budget_usd: WireFloat | None = Field(default=None, ge=0.0)
    token_budget: WireInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _name_is_a_dataset_name(self) -> MintCampaignPayload:
        """Deciding what a name IS belongs to ``validate_dataset_name`` — a pattern of its own here
        is a second rule that can disagree with the slug ingest mints off a filename."""

        validate_dataset_name(self.dataset_name)
        return self


# The generic route's kind → payload type. An enum-keyed dispatch table over TYPES, which is the
# form root `CLAUDE.md` sanctions — asking a type what it accepts, rather than asking a list of
# names, is what stops a key going silently unread when a verb grows one.
PAYLOAD_MODEL_FOR_KIND: dict[str, type[CommandPayload]] = {
    "fork-cycle": ForkCyclePayload,
    "skip-searchpoint": SkipSearchpointPayload,
    "delete-cycle": DeleteCyclePayload,
    "cleanup-empty-cycles": CleanupEmptyCyclesPayload,
    "pause-cycle": PauseCyclePayload,
    "set-sample-lookahead": SetSampleLookaheadPayload,
    "origin-gate-decision": OriginGateDecisionPayload,
    "change-spend-budget": ChangeSpendBudgetPayload,
    "start-run": StartRunPayload,
    "step-cycle": StepCyclePayload,
    "verify-candidate": VerifyCandidatePayload,
    "archive-campaign": LifecyclePayload,
    "delete-campaign": LifecyclePayload,
    "unarchive-campaign": LifecyclePayload,
    "set-campaign-label": SetCampaignLabelPayload,
    "register-backend": RegisterBackendPayload,
    "mint-campaign": MintCampaignPayload,
    "cancel-queued-run": CancelQueuedRunPayload,
    "replace-dataset": ReplaceDatasetPayload,
    "compact-archive": CompactArchivePayload,
    "edit-draft-campaign": EditDraftCampaignPayload,
    "resolve-origin": ResolveOriginPayload,
    "start-checkin": StartCheckinPayload,
}
# Total over the dispatched set, exactly as `CAP_FOR_KIND` is: a kind with no payload type is a
# kind whose wire shape nothing states, and a typed route is not an exemption from having one.
if set(PAYLOAD_MODEL_FOR_KIND) != ALL_DISPATCHED_KINDS:
    raise RuntimeError(
        "PAYLOAD_MODEL_FOR_KIND out of sync with the dispatched command set: "
        f"{ALL_DISPATCHED_KINDS.symmetric_difference(PAYLOAD_MODEL_FOR_KIND)}"
    )


class CommandAcceptedBody(StrictModel):
    """The 202 response shape declared in ``api-openapi.yaml``."""

    command_id: str = Field(description="Stable id of the appended `CommandRecord`.")
    correlation_id: str = Field(description="Echo of the request's `Idempotency-Key`.")
    ledger_sequence: int = Field(
        description="Offset at which the `CommandRecord` was appended.", ge=0
    )
