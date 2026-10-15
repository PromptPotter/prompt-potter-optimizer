"""The `/commands/{kind}` vocabulary, in `domain/` so the terminal and the ledger folds read it without the dispatcher."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, ClassVar, Self

from pydantic import Field, model_validator

from promptpotter.domain.phases import (
    INNER_ARMS_REFUSAL,
    INNER_GATE_REFUSAL,
    INNER_PAUSE_REFUSAL,
    INNER_SKIP_REFUSAL,
    GateDecision,
)
from promptpotter.domain.search_point import PROMPT_STRING_FIELDS
from promptpotter.domain.strict_model import StrictModel, WireInt
from promptpotter.shared.identity import (
    CAMPAIGN_BUDGET_CAP,
    CAMPAIGN_CREATE_CAP,
    CAMPAIGN_LIFECYCLE_CAP,
    CAMPAIGN_LOOKAHEAD_CAP,
    CAMPAIGN_RUN_CAP,
    CAMPAIGN_STEP_CAP,
)

__all__ = [
    "LOOP_TAKEN",
    "CampaignPayload",
    "CommandKind",
    "CommandPayload",
    "CyclePayload",
    "LoopPayload",
    "OriginGateDecisionPayload",
    "PauseCyclePayload",
    "SetSampleLookaheadPayload",
    "SkipSearchpointPayload",
    "SubmitArmsPayload",
    "SubmittedArm",
]


class CommandKind(StrEnum):
    """A row is `kind, handler, capability, cli_verb`: the handler is `application/commands/{module}:{attr}`, imported on dispatch; a `None` verb DECLARES the kind browser-only."""

    handler: str
    capability: str
    cli_verb: str | None

    def __new__(cls, kind: str, handler: str, capability: str, cli_verb: str | None) -> Self:
        member = str.__new__(cls, kind)
        member._value_ = kind
        member.handler = handler
        member.capability = capability
        member.cli_verb = cli_verb
        return member

    @property
    def payload_name(self) -> str:
        """The kind's payload model IS named for it, so no table pairs them and no two kinds share one."""
        return "".join(part.capitalize() for part in self.value.split("-")) + "Payload"

    PAUSE_CYCLE = "pause-cycle", "loop_commands:pause_cycle", CAMPAIGN_STEP_CAP, "pause"
    ORIGIN_GATE_DECISION = (
        "origin-gate-decision",
        "loop_commands:origin_gate_decision",
        CAMPAIGN_STEP_CAP,
        "origin-gate",
    )
    SUBMIT_ARMS = "submit-arms", "loop_commands:submit_arms", CAMPAIGN_STEP_CAP, "submit-arms"
    SKIP_SEARCHPOINT = (
        "skip-searchpoint",
        "loop_commands:skip_searchpoint",
        CAMPAIGN_STEP_CAP,
        "skip-searchpoint",
    )
    # Browser-only ON PURPOSE: the absence IS the boundary (root `CLAUDE.md` § Conventions).
    SET_SAMPLE_LOOKAHEAD = (
        "set-sample-lookahead",
        "loop_commands:set_sample_lookahead",
        CAMPAIGN_LOOKAHEAD_CAP,
        None,
    )
    START_RUN = "start-run", "launching:start_run", CAMPAIGN_RUN_CAP, "resume"
    STEP_CYCLE = "step-cycle", "launching:step_cycle", CAMPAIGN_STEP_CAP, "step-cycle"
    FORK_CYCLE = "fork-cycle", "launching:fork_cycle", CAMPAIGN_RUN_CAP, "resume"
    MINT_CAMPAIGN = "mint-campaign", "launching:mint_campaign", CAMPAIGN_CREATE_CAP, "new"
    START_CHECKIN = "start-checkin", "launching:start_checkin", CAMPAIGN_RUN_CAP, "new"
    # A verify SPENDS on real cells, so it sits with the verbs that buy measurement, not the step verbs.
    VERIFY_CANDIDATE = (
        "verify-candidate",
        "measuring:verify_candidate",
        CAMPAIGN_RUN_CAP,
        "verify",
    )
    GRADE_BENCH = "grade-bench", "measuring:grade_bench", CAMPAIGN_RUN_CAP, "bench"
    CHANGE_RUN_LIMITS = (
        "change-run-limits",
        "limits_and_queue:change_run_limits",
        CAMPAIGN_BUDGET_CAP,
        "set-limits",
    )
    # Account-scoped, and holding the rung is not enough on the host's key: `quota.py::set_concurrent_cycles`.
    SET_CONCURRENT_CYCLES = (
        "set-concurrent-cycles",
        "limits_and_queue:set_concurrent_cycles",
        CAMPAIGN_BUDGET_CAP,
        "set-concurrent-cycles",
    )
    # A queued MINT has no cycle to address yet, and WHOSE launch it is `JobRegistry.cancel_queued` checks.
    CANCEL_QUEUED_RUN = (
        "cancel-queued-run",
        "limits_and_queue:cancel_queued_run",
        CAMPAIGN_RUN_CAP,
        "cancel-queued",
    )
    DELETE_CYCLE = (
        "delete-cycle",
        "cycle_cleanup:delete_cycle",
        CAMPAIGN_LIFECYCLE_CAP,
        "delete-cycle",
    )
    CLEANUP_EMPTY_CYCLES = (
        "cleanup-empty-cycles",
        "cycle_cleanup:cleanup_empty_cycles",
        CAMPAIGN_LIFECYCLE_CAP,
        "cleanup-empty-cycles",
    )
    ARCHIVE_CAMPAIGN = (
        "archive-campaign",
        "workspace_edits:campaign_lifecycle",
        CAMPAIGN_LIFECYCLE_CAP,
        "archive",
    )
    UNARCHIVE_CAMPAIGN = (
        "unarchive-campaign",
        "workspace_edits:campaign_lifecycle",
        CAMPAIGN_LIFECYCLE_CAP,
        "unarchive",
    )
    DELETE_CAMPAIGN = (
        "delete-campaign",
        "workspace_edits:campaign_lifecycle",
        CAMPAIGN_LIFECYCLE_CAP,
        "delete",
    )
    # The label is how every other surface addresses the campaign to a human.
    SET_CAMPAIGN_LABEL = (
        "set-campaign-label",
        "workspace_edits:set_campaign_label",
        CAMPAIGN_LIFECYCLE_CAP,
        "rename",
    )
    # A dataset slug is in the measurement cache key: repointing one re-addresses every campaign on it.
    REPLACE_DATASET = (
        "replace-dataset",
        "workspace_edits:replace_dataset",
        CAMPAIGN_LIFECYCLE_CAP,
        "replace-dataset",
    )
    # Its purge step destroys paid spend.
    COMPACT_ARCHIVE = (
        "compact-archive",
        "archive_compaction:compact_archive",
        CAMPAIGN_LIFECYCLE_CAP,
        "compact-archive",
    )
    EDIT_DRAFT_CAMPAIGN = (
        "edit-draft-campaign",
        "draft_editing:edit_draft_campaign",
        CAMPAIGN_CREATE_CAP,
        "new",
    )
    RESOLVE_ORIGIN = "resolve-origin", "origin_resolving:resolve_origin", CAMPAIGN_CREATE_CAP, "new"


class CommandPayload(StrictModel):
    """Base of every payload on the generic ``POST /commands/{kind}`` route. ``StrictModel`` forbids
    extras, so THE MODEL IS the accepted-key set — there is no list to fall out of step with it.
    A payload's address base is its target ledger: ``CyclePayload`` the cycle's, ``CampaignPayload``
    and a bare one the workspace's, a check-in's (``payloads.py::CheckinPayload``) its root cycle's."""


class CampaignPayload(CommandPayload):
    campaign_id: str = Field(min_length=1, max_length=128)


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


class LoopPayload(CyclePayload):
    """Only a RUNNING loop takes it, off its own ledger (`ledger_scan.py::Controls`) — which is why these sit below `application/`."""


class SkipSearchpointPayload(LoopPayload):
    inner_refusal: ClassVar[str | None] = INNER_SKIP_REFUSAL


class PauseCyclePayload(LoopPayload):
    inner_refusal: ClassVar[str | None] = INNER_PAUSE_REFUSAL

    reason: str = Field(default="", max_length=512)


class SetSampleLookaheadPayload(LoopPayload):
    # Throughput is what an inner run answers for itself: the outer's depth is never inherited.
    inner_refusal: ClassVar[str | None] = None

    cells: WireInt = Field(ge=1, description="1 disarms.")
    auto: bool = Field(
        default=False,
        description="As deep as the stop rules allow, every round, until pressed off.",
    )


class OriginGateDecisionPayload(LoopPayload):
    inner_refusal: ClassVar[str | None] = INNER_GATE_REFUSAL

    decision: GateDecision


class SubmittedArm(StrictModel):
    """One arm an operator wrote: an EDIT of the round's parent, never a whole individual."""

    prompt_fields: dict[str, str] = Field(
        default_factory=dict,
        description="The prompt fields this arm rewrites, by name; every other one is the "
        "parent's.",
    )
    pipeline_overlay: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="`{node: {param: value}}` laid over the parent's pipeline params.",
    )
    changes_description: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _edits_the_parent(self) -> SubmittedArm:
        if unknown := sorted(set(self.prompt_fields) - set(PROMPT_STRING_FIELDS)):
            raise ValueError(f"an arm rewrites prompt fields {PROMPT_STRING_FIELDS}, not {unknown}")
        if not self.prompt_fields and not self.pipeline_overlay:
            raise ValueError("an arm that edits nothing is the round's parent")
        return self


class SubmitArmsPayload(LoopPayload):
    inner_refusal: ClassVar[str | None] = INNER_ARMS_REFUSAL

    arms: list[SubmittedArm] = Field(default_factory=list, max_length=32)
    keep: list[Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=list,
        max_length=64,
        description="The drafts to measure as proposed, by their index at the hold.",
    )

    @model_validator(mode="after")
    def _answers_with_an_arm(self) -> SubmitArmsPayload:
        if not self.arms and not self.keep:
            raise ValueError("an answer to the arm gate carries an arm of its own or keeps a draft")
        if len(set(self.keep)) != len(self.keep):
            raise ValueError("a draft is kept once")
        return self


_KIND_NAMED = {kind.payload_name: kind for kind in CommandKind}
# Keyed `str`: a `CommandRecord.kind` read back off the ledger finds its member's row.
LOOP_TAKEN: dict[str, type[LoopPayload]] = {
    _KIND_NAMED[model.__name__]: model for model in LoopPayload.__subclasses__()
}
