"""The `/commands/{kind}` vocabulary, in `domain/` so the terminal and the ledger folds read it without the dispatcher."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, NamedTuple, Self

from pydantic import (
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from promptpotter.domain.campaign import Arm, ArmName, Compare
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.dataset_name import validate_dataset_name
from promptpotter.domain.draft_edit import EditDraftPatch, OptimizationOverrides
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.phases import (
    INNER_ARMS_REFUSAL,
    INNER_GATE_REFUSAL,
    INNER_LAUNCH_REFUSAL,
    INNER_PAUSE_REFUSAL,
    INNER_SKIP_REFUSAL,
    GateDecision,
)
from promptpotter.domain.pipeline_schema import ParamIntent
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import ConfigOverrides
from promptpotter.domain.scoring import MAX_PASS_ROWS
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import RequestBody, StrictModel, WireInt
from promptpotter.domain.verify_pass import VerifyStrategy
from promptpotter.shared.identity import (
    CAMPAIGN_BUDGET_CAP,
    CAMPAIGN_CREATE_CAP,
    CAMPAIGN_LIFECYCLE_CAP,
    CAMPAIGN_LOOKAHEAD_CAP,
    CAMPAIGN_RUN_CAP,
    CAMPAIGN_STEP_CAP,
)
from promptpotter.shared.named_rows import require_own_docstring
from promptpotter.shared.worded import Worded


class Press(NamedTuple):
    """Who may press a kind beyond the browser: the terminal verb a person presses it by, and
    whether a host assistant may press it for them. A row pressed at the browser alone has none
    (`BROWSER`)."""

    cli_verb: str
    host: bool


def anyone(verb: str) -> Press:
    """Every door: the browser, a terminal by *verb*, and a host's tool call."""
    return Press(verb, host=True)


def person(verb: str) -> Press:
    """A person's own act at the browser or a terminal by *verb*; a host never presses it for them."""
    return Press(verb, host=False)


# The browser alone: no terminal verb and no host tool.
BROWSER = None

# How a door answers a kind: inline with its applied answer, or as a task its door tracks while
# the act runs on (a launch, a paid pass, a resolver turn).
type Answering = Literal["inline", "task"]


class CommandKind(StrEnum):
    """A row is `kind, handler, capability, press[, destructive[, answering]]`: the handler is
    `application/commands/{module}:{attr}`, imported by the dispatch that needs it (the API imports
    every one at app import); a `None` capability is the account's own act, open to its session;
    `press` is who may press it beyond the browser (`Press`, or `BROWSER`); `destructive` is MCP's `destructiveHint`, true for an
    apply that overwrites or removes what is there and false for one that only adds; `answering`
    is how its doors answer (`Answering`)."""

    handler: str
    capability: str | None
    press: Press | None
    destructive: bool
    answering: Answering

    def __new__(
        cls,
        kind: str,
        handler: str,
        capability: str | None,
        press: Press | None,
        destructive: bool = False,
        answering: Answering = "inline",
    ) -> Self:
        member = str.__new__(cls, kind)
        member._value_ = kind
        member.handler = handler
        member.capability = capability
        member.press = press
        member.destructive = destructive
        member.answering = answering
        return member

    @property
    def cli_verb(self) -> str | None:
        return None if self.press is None else self.press.cli_verb

    @property
    def host(self) -> bool:
        """A host assistant may press it for its person."""
        return self.press is not None and self.press.host

    @property
    def browser_only(self) -> bool:
        """Reached through the webapp alone: no terminal verb and no host tool."""
        return self.press is None

    PAUSE_CYCLE = (
        "pause-cycle",
        "loop_commands:pause_cycle",
        CAMPAIGN_STEP_CAP,
        anyone("pause"),
        True,
    )
    ORIGIN_GATE_DECISION = (
        "origin-gate-decision",
        "loop_commands:origin_gate_decision",
        CAMPAIGN_STEP_CAP,
        anyone("origin-gate"),
        True,
    )
    SUBMIT_ARMS = (
        "submit-arms",
        "loop_commands:submit_arms",
        CAMPAIGN_STEP_CAP,
        anyone("submit-arms"),
    )
    SKIP_SEARCHPOINT = (
        "skip-searchpoint",
        "loop_commands:skip_searchpoint",
        CAMPAIGN_STEP_CAP,
        anyone("skip-searchpoint"),
        True,
    )
    # Browser-only ON PURPOSE: `presentation/CLAUDE.md` § Sample look-ahead.
    SET_SAMPLE_LOOKAHEAD = (
        "set-sample-lookahead",
        "loop_commands:set_sample_lookahead",
        CAMPAIGN_LOOKAHEAD_CAP,
        BROWSER,
        True,
    )
    START_RUN = (
        "start-run",
        "launching:start_run",
        CAMPAIGN_RUN_CAP,
        anyone("resume"),
        False,
        "task",
    )
    STEP_CYCLE = (
        "step-cycle",
        "launching:step_cycle",
        CAMPAIGN_STEP_CAP,
        anyone("step-cycle"),
        False,
        "task",
    )
    # Its fork supersedes the line's head, which it pauses.
    FORK_CYCLE = (
        "fork-cycle",
        "launching:fork_cycle",
        CAMPAIGN_RUN_CAP,
        anyone("resume"),
        True,
        "task",
    )
    MINT_CAMPAIGN = (
        "mint-campaign",
        "launching:mint_campaign",
        CAMPAIGN_CREATE_CAP,
        anyone("new"),
        False,
        "task",
    )
    MINT_HEAD_TO_HEAD = (
        "mint-head-to-head",
        "launching:mint_head_to_head",
        CAMPAIGN_CREATE_CAP,
        anyone("head-to-head"),
        False,
        "task",
    )
    # It closes the draft it mints from.
    START_CHECKIN = (
        "start-checkin",
        "launching:start_checkin",
        CAMPAIGN_CREATE_CAP,
        anyone("new"),
        True,
        "task",
    )
    # A verify SPENDS on real cells, so it sits with the verbs that buy measurement, not the step verbs.
    VERIFY_CANDIDATE = (
        "verify-candidate",
        "measuring:verify_candidate",
        CAMPAIGN_RUN_CAP,
        anyone("verify"),
        False,
        "task",
    )
    # It restates the line's headline in place.
    GRADE_BENCH = (
        "grade-bench",
        "measuring:grade_bench",
        CAMPAIGN_RUN_CAP,
        anyone("bench"),
        True,
        "task",
    )
    CHANGE_RUN_LIMITS = (
        "change-run-limits",
        "limits_and_queue:change_run_limits",
        CAMPAIGN_BUDGET_CAP,
        anyone("set-limits"),
        True,
    )
    # Account-scoped, and holding the rung is not enough on the host's key: `quota.py::set_concurrent_cycles`.
    SET_CONCURRENT_CYCLES = (
        "set-concurrent-cycles",
        "limits_and_queue:set_concurrent_cycles",
        CAMPAIGN_BUDGET_CAP,
        anyone("set-concurrent-cycles"),
        True,
    )
    # A queued MINT has no cycle to address yet, and WHOSE launch it is `JobRegistry.cancel_queued` checks.
    CANCEL_QUEUED_RUN = (
        "cancel-queued-run",
        "limits_and_queue:cancel_queued_run",
        CAMPAIGN_RUN_CAP,
        anyone("cancel-queued"),
        True,
    )
    DELETE_CYCLE = (
        "delete-cycle",
        "cycle_cleanup:delete_cycle",
        CAMPAIGN_LIFECYCLE_CAP,
        anyone("delete-cycle"),
        True,
    )
    CLEANUP_EMPTY_CYCLES = (
        "cleanup-empty-cycles",
        "cycle_cleanup:cleanup_empty_cycles",
        CAMPAIGN_LIFECYCLE_CAP,
        anyone("cleanup-empty-cycles"),
        True,
    )
    ARCHIVE_CAMPAIGN = (
        "archive-campaign",
        "workspace_edits:campaign_lifecycle",
        CAMPAIGN_LIFECYCLE_CAP,
        anyone("archive"),
        True,
    )
    UNARCHIVE_CAMPAIGN = (
        "unarchive-campaign",
        "workspace_edits:campaign_lifecycle",
        CAMPAIGN_LIFECYCLE_CAP,
        anyone("unarchive"),
        True,
    )
    DELETE_CAMPAIGN = (
        "delete-campaign",
        "workspace_edits:campaign_lifecycle",
        CAMPAIGN_LIFECYCLE_CAP,
        anyone("delete"),
        True,
    )
    # The label is how every other surface addresses the campaign to a human.
    SET_CAMPAIGN_LABEL = (
        "set-campaign-label",
        "workspace_edits:set_campaign_label",
        CAMPAIGN_LIFECYCLE_CAP,
        anyone("rename"),
        True,
    )
    # Its purge step destroys paid spend.
    COMPACT_ARCHIVE = (
        "compact-archive",
        "archive_compaction:compact_archive",
        CAMPAIGN_LIFECYCLE_CAP,
        anyone("compact-archive"),
        True,
    )
    EDIT_DRAFT_CAMPAIGN = (
        "edit-draft-campaign",
        "draft_editing:edit_draft_campaign",
        CAMPAIGN_CREATE_CAP,
        anyone("new"),
        True,
    )
    # Its turn writes the draft's fields.
    RESOLVE_ORIGIN = (
        "resolve-origin",
        "origin_resolving:resolve_origin",
        CAMPAIGN_CREATE_CAP,
        anyone("new"),
        True,
        "task",
    )
    # It writes what a later campaign's `packs` may draw, so it sits with the verbs that make one.
    BANK_SNIPPET = (
        "bank-snippet",
        "workspace_edits:bank_snippet",
        CAMPAIGN_CREATE_CAP,
        anyone("bank-snippet"),
    )
    # Its apply removes the draft's ledger, so it is recorded on the workspace's.
    DISCARD_DRAFT = (
        "discard-draft",
        "draft_editing:discard_draft",
        CAMPAIGN_CREATE_CAP,
        anyone("discard-draft"),
        True,
    )
    # The three doors a check-in draft opens by; each records on the workspace's ledger, as no draft has one yet.
    INGEST_UPLOAD = (
        "ingest-upload",
        "workspace_edits:ingest_upload",
        CAMPAIGN_CREATE_CAP,
        anyone("new"),
    )
    OPEN_DATASET_DRAFT = (
        "open-dataset-draft",
        "workspace_edits:open_dataset_draft",
        CAMPAIGN_CREATE_CAP,
        anyone("open-dataset-draft"),
    )
    OPEN_ORIGIN_DRAFT = (
        "open-origin-draft",
        "workspace_edits:open_origin_draft",
        CAMPAIGN_CREATE_CAP,
        anyone("open-origin-draft"),
    )
    SET_USER_SETTINGS = (
        "set-user-settings",
        "workspace_edits:set_user_settings",
        None,
        anyone("set-user-settings"),
        True,
    )
    # A person's consent: their own act, never a host's.
    ACCEPT_TERMS = "accept-terms", "workspace_edits:accept_terms", None, person("accept-terms")
    # Browser-only ON PURPOSE: a key on a terminal's argv lands in its shell history, and the
    # terminal sends on the box's own keys.
    SET_API_KEY = "set-api-key", "workspace_edits:set_api_key", CAMPAIGN_BUDGET_CAP, BROWSER, True
    REMOVE_API_KEY = (
        "remove-api-key",
        "workspace_edits:remove_api_key",
        CAMPAIGN_BUDGET_CAP,
        BROWSER,
        True,
    )


# Filled as each model declares the kind it carries: the ONE kind → payload table. Keyed `str`,
# so a `CommandRecord.kind` read back off the ledger finds its row.
PAYLOAD_MODEL_FOR_KIND: dict[str, type[CommandPayload]] = {}


class CommandPayload(RequestBody):
    """Base of every payload on the generic ``POST /commands/{kind}`` route. ``StrictModel`` forbids
    extras, so THE MODEL IS the accepted-key set — there is no list to fall out of step with it.
    A payload's address base is its target ledger: ``CyclePayload`` the cycle's, ``LinePayload`` its
    line holder's, ``CampaignPayload`` and a bare one the workspace's, a ``DraftPayload`` its draft's."""

    kind: ClassVar[CommandKind]
    # What the terminal prints after the ack, where the ack alone leaves the next step unsaid; a
    # `{field}` in it reads that field of the applied answer.
    follow_up: ClassVar[str] = ""

    def __init_subclass__(cls, kind: CommandKind | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if kind is None:
            return
        require_own_docstring(cls, kind)
        if kind in PAYLOAD_MODEL_FOR_KIND:
            raise TypeError(f"{kind} already carries {PAYLOAD_MODEL_FOR_KIND[kind].__name__}")
        if issubclass(cls, CyclePayload) and not hasattr(cls, "inner_refusal"):
            raise TypeError(f"{cls.__name__} is cycle-scoped and declares no `inner_refusal`")
        cls.kind = kind
        PAYLOAD_MODEL_FOR_KIND[kind] = cls

    def recorded(self) -> tuple[dict[str, JsonValue], str]:
        """The act as its `CommandRecord` keeps it, and what an idempotency key names, so one key
        never answers two acts: that one dump and the raw value of each excluded field, each hashed
        as its bytes. A secret's text enters no stored digest: the kind carrying one replays
        nothing (`set-api-key`), so no retry is matched on it."""
        dump = self.model_dump(mode="json")
        digest = hashlib.sha256(f"{self.kind}\0".encode())
        digest.update(json.dumps(dump, ensure_ascii=False, separators=(",", ":")).encode())
        for name, info in type(self).model_fields.items():
            value = getattr(self, name)
            if not info.exclude or value is None:
                continue
            raw = value.encode() if isinstance(value, str) else value
            if not isinstance(raw, bytes):
                raise TypeError(f"{type(self).__name__}.{name} drops a {type(value).__name__}")
            digest.update(f"\0{name}\0{len(raw)}\0".encode())
            digest.update(raw)
        return dump, digest.hexdigest()


class ReasonedPayload(CommandPayload):
    reason: str = Field(default="", max_length=512, description="Recorded with the command.")


class CampaignPayload(CommandPayload):
    campaign_id: str = Field(
        min_length=1,
        max_length=128,
        description="The full id: a prefix is the terminal's to resolve, before it builds a payload.",
    )


class CyclePayload(CampaignPayload):
    """A cycle's address: the root hop, plus the ``?descend=`` tail the reads take. Every
    cycle-scoped kind is ADDRESSED alike; ``inner_refusal`` is each kind's own answer to whether
    it acts on an inner run, and ``dispatcher.py::CommandDispatcher.dispatch`` is its one reader. A
    run verb's answer is the sentence its cycle's served admission carries
    (``domain/phases.py::RunState.inner``), so the control and the refusal say one thing."""

    cycle_id: str = Field(min_length=1, max_length=128)
    # Excluded: the dispatcher spends it resolving the leaf, so a recorded tail addresses it twice.
    descend: str | None = Field(default=None, max_length=512, exclude=True)

    # ``None`` = acts on an inner run. Undeclared here, so a new kind fails at declaration until it answers.
    inner_refusal: ClassVar[str | None]
    # False: a terminal names the cycle, never defaulting to the active one.
    implied_target: ClassVar[bool] = True


class LoopPayload(CyclePayload):
    """Only a RUNNING loop takes it, off its own ledger (`ledger_scan.py::Controls`) — which is why these sit below `application/`."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # `ledger_scan.py::_LOOP_TAKEN` reads the kind table having imported this module alone.
        if cls.__module__ != __name__:
            raise TypeError(f"{cls.__name__} is a loop payload declared outside {__name__}")


class SkipSearchpointPayload(LoopPayload, kind=CommandKind.SKIP_SEARCHPOINT):
    """Cut the candidate being scored at its next sample boundary; the round carries on with the rest."""

    inner_refusal: ClassVar[str | None] = INNER_SKIP_REFUSAL
    follow_up: ClassVar[str] = "The round goes on with the rest."


class PauseCyclePayload(ReasonedPayload, LoopPayload, kind=CommandKind.PAUSE_CYCLE):
    """Ask a running cycle to stop at its next checkpoint; it resumes from there."""

    inner_refusal: ClassVar[str | None] = INNER_PAUSE_REFUSAL
    follow_up: ClassVar[str] = "The loop exits at its next checkpoint; `resume` picks it up."


class SetSampleLookaheadPayload(LoopPayload, kind=CommandKind.SET_SAMPLE_LOOKAHEAD):
    """Arm how many cells ahead the walk sends."""

    # Throughput is what an inner run answers for itself: the outer's depth is never inherited.
    inner_refusal: ClassVar[str | None] = None

    cells: WireInt = Field(ge=1, description="1 disarms.")
    auto: bool = Field(
        default=False,
        description="As deep as the stop rules allow, every round, until pressed off.",
    )


class OriginGateDecisionPayload(LoopPayload, kind=CommandKind.ORIGIN_GATE_DECISION):
    """Answer a cycle holding at the round-0 origin gate; one not holding clears it on arrival."""

    inner_refusal: ClassVar[str | None] = INNER_GATE_REFUSAL
    follow_up: ClassVar[str] = "A cycle holding at the gate acts on it within a second."

    decision: GateDecision = Field(
        description="`proceed` into L1 anyway, `rescore` the origin force-fresh, or `abort` the cycle."
    )


class SubmittedArm(StrictModel):
    """One arm an operator wrote: an EDIT of the round's parent, never a whole individual."""

    prompt_fields: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description="`{node: {field: text}}`: the prompt fields this arm rewrites, each under the "
        "prompt node that declares it; every other one is the parent's.",
    )
    shots: dict[str, list[str]] = Field(
        default_factory=dict,
        description="`{node: [sample key]}`: the demo-pool shots this arm sets on each prompt node, "
        "in render order; `[]` clears them, and a node absent keeps the parent's.",
    )
    pipeline_overlay: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="`{node: {param: value}}` laid over the parent's pipeline params.",
    )
    changes_description: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _edits_the_parent(self) -> SubmittedArm:
        if not self.prompt_fields and not self.shots and not self.pipeline_overlay:
            raise ValueError("an arm that edits nothing is the round's parent")
        return self


class SubmitArmsPayload(LoopPayload, kind=CommandKind.SUBMIT_ARMS):
    """Answer a cycle holding for a round's arms (`optimization.arm_gate`) with arms of its own,
    drafts kept, or both."""

    inner_refusal: ClassVar[str | None] = INNER_ARMS_REFUSAL
    follow_up: ClassVar[str] = "A cycle holding for them measures them within a second."

    arms: list[SubmittedArm] = Field(
        default_factory=list,
        max_length=32,
        description="Each an edit of the round's parent: `changes_description` and at least one "
        "of `prompt_fields`, `shots`, `pipeline_overlay`.",
    )
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


_OUTER_OWNS_TREE = "An inner run's cycles live and die with the outer campaign."


class DeleteCyclePayload(CyclePayload, kind=CommandKind.DELETE_CYCLE):
    """Remove ONE stub cycle, the singular of `cleanup-empty-cycles`; refused for a cycle holding
    rounds or a live producer (pause it first)."""

    inner_refusal: ClassVar[str | None] = _OUTER_OWNS_TREE
    # An implied target would make the likeliest typo the delete.
    implied_target: ClassVar[bool] = False


class CleanupEmptyCyclesPayload(CyclePayload, kind=CommandKind.CLEANUP_EMPTY_CYCLES):
    """Remove the stub cycles a mint left behind when it never reached round 0."""

    inner_refusal: ClassVar[str | None] = _OUTER_OWNS_TREE


class ChangeRunLimitsPayload(CyclePayload, kind=CommandKind.CHANGE_RUN_LIMITS):
    """Raise or lower an existing cycle's spend, token, round or wall-clock bound; a budget- or
    round-halted cycle continues by a higher ceiling, then `resume`. Spend is clamped against the
    account allowance. On an arm of a head-to-head it moves that arm alone, and leaves the
    comparison only when a ceiling moved."""

    inner_refusal: ClassVar[str | None] = (
        "An inner run spends under the outer run's ceiling. Change the outer run's limits instead."
    )
    follow_up: ClassVar[str] = (
        "It is clamped against your account allowance, and `resume` picks it up; an arm of a "
        "head-to-head leaves its comparison only when a ceiling moved."
    )

    # An axis it does not name stays as it stands; a named null lifts it.
    budget: Budget

    @model_validator(mode="after")
    def _names_an_axis(self) -> ChangeRunLimitsPayload:
        if not self.budget.named:
            raise ValueError(
                "a budget naming at least one of usd / tokens / rounds / wall_clock_s is required"
            )
        return self


class StepCyclePayload(CyclePayload, kind=CommandKind.STEP_CYCLE):
    """Let a paused cycle run a bounded number of rounds, then stop again."""

    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL

    step_rounds: WireInt = Field(default=1, ge=1, le=100, description="Rounds to run.")


class StartRunPayload(CyclePayload, kind=CommandKind.START_RUN):
    """A run of an existing cycle: what bounds it (``budget``, ``step_rounds``) and the shape it
    takes (``mode``)."""

    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL

    budget: Budget = Budget()
    step_rounds: WireInt | None = Field(default=None, ge=1, le=100)
    mode: RunMode = RunMode()


class ForkSeed(StrictModel):
    """A fork's `CycleSeed` as sent: the applier freezes `node_narrowing` on the forked cycle's
    own rows and stamps the `origin_source` the wire omits."""

    origin_prompts: dict[str, NodePrompt] = Field(default_factory=dict)
    pipeline_overlay: dict[str, Any] = Field(default_factory=dict)
    config_overrides: ConfigOverrides = Field(default_factory=ConfigOverrides)
    node_narrowing: dict[str, list[ParamIntent]] = Field(
        default_factory=dict,
        description="Per node, the rows as drawn; overrides the campaign's narrowing for this "
        "cycle only, and a node it does not name inherits.",
    )


class ForkCyclePayload(ReasonedPayload, CyclePayload, kind=CommandKind.FORK_CYCLE):
    """The cut, and the shape of the fork's first run (``mode``) — one act, so one command.
    A parent a producer still holds is paused by the applier once the fork's run is admitted."""

    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL

    mode: RunMode = RunMode()
    round: int = Field(default=0, ge=0)
    candidate_id: str = Field(default="", max_length=128)
    seed: ForkSeed = ForkSeed()
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

    @model_validator(mode="after")
    def _rewinds_past_its_origin(self) -> ForkCyclePayload:
        if self.keep_rounds and self.round == 0:
            raise ValueError(
                "keep_rounds at round 0 lifts no round to continue from; a run that starts over "
                "at the origin is a diag run"
            )
        if self.keep_rounds and self.candidate_id:
            raise ValueError(
                "keep_rounds re-runs round `round` from where its line stood going into it, so it "
                "names no candidate; fork without keep_rounds to start from one"
            )
        if self.keep_rounds and self.seed.origin_prompts:
            raise ValueError(
                "keep_rounds lifts the parent's round 0 as its origin, so the seed must not "
                "declare origin_prompts; fork without keep_rounds to start from an edited origin"
            )
        return self


class VerifyCandidatePayload(CyclePayload, kind=CommandKind.VERIFY_CANDIDATE):
    """``samples`` omitted is the ANSWER, not an absence: the count is derived from the
    per-candidate round budget and the rounds run since this cycle's last verification
    (``application/diagnostics/verify.py::derive_verify_samples``). A larger explicit count is
    refused, naming the budget — which is what keeps one click on a million-row dataset from being
    a million-cell bill.

    Addressed as the ``evidence`` read addresses a searchpoint — cycle, descent, ``candidate_id``
    — so an L4 inner candidate is as reachable as a top-level one."""

    inner_refusal: ClassVar[str | None] = None

    candidate_id: str = Field(min_length=1, max_length=128)
    samples: WireInt | None = Field(default=None, ge=1, le=MAX_PASS_ROWS)
    strategy: VerifyStrategy = "random"
    seed: WireInt | None = None


class LinePayload(CampaignPayload):
    """Addressed to a campaign, applied to and recorded on the cycle holding its line."""


class GradeBenchPayload(LinePayload, kind=CommandKind.GRADE_BENCH):
    """Grade the campaign's origin and its selection on the held-out bench rows."""


class ArchiveCampaignPayload(ReasonedPayload, CampaignPayload, kind=CommandKind.ARCHIVE_CAMPAIGN):
    """Flag a campaign archived: hidden from the default listing, restorable by unarchive; the tree
    does not move."""


class UnarchiveCampaignPayload(
    ReasonedPayload, CampaignPayload, kind=CommandKind.UNARCHIVE_CAMPAIGN
):
    """Restore an archived campaign to active."""


class DeleteCampaignPayload(ReasonedPayload, CampaignPayload, kind=CommandKind.DELETE_CAMPAIGN):
    """Remove a campaign for good; its measurements still cache-hit for siblings."""

    keep_results: bool = Field(
        default=False,
        description="Spare the keepsake tier (manifest, reports, the shallow loop trace); drop only "
        "the resume, audit and mirror tiers.",
    )


LifecyclePayload = ArchiveCampaignPayload | UnarchiveCampaignPayload | DeleteCampaignPayload


class SetCampaignLabelPayload(CampaignPayload, kind=CommandKind.SET_CAMPAIGN_LABEL):
    """Name a campaign wherever it is named to a human; its id still addresses it."""

    # Required, and `""` is the CLEAR: a default would give "clear" two spellings.
    label: str = Field(max_length=200, description='`""` restores the dataset-name fallback.')


class DraftAddressedPayload(CommandPayload):
    draft_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class DraftPayload(DraftAddressedPayload):
    """Recorded on the draft's own ledger, which its Start moves into the root cycle."""


class DiscardDraftPayload(DraftAddressedPayload, kind=CommandKind.DISCARD_DRAFT):
    """Close an open check-in without minting; its check-in calls stay billed to the workspace."""


class CancelQueuedRunPayload(CommandPayload, kind=CommandKind.CANCEL_QUEUED_RUN):
    """Withdraw a launch waiting for a machine slot; nothing ran and nothing was spent."""

    job_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="The queued job id, as `machine-status` lists it.",
    )


class SetConcurrentCyclesPayload(CommandPayload, kind=CommandKind.SET_CONCURRENT_CYCLES):
    """How many campaigns this account may hold at once, queued launches included; at most the
    machine's capacity, and an account on the host's key cannot move its own."""

    max_concurrent_cycles: WireInt = Field(ge=1, description="The new limit.")


class BankSnippetPayload(CommandPayload, kind=CommandKind.BANK_SNIPPET):
    """Bank an edited library snippet into this account's own block pack as the next version of
    the one it was edited from; a text the library already holds is that snippet."""

    supersedes: str = Field(
        min_length=1, max_length=64, description="The id of the snippet the text was edited from."
    )
    text: str = Field(min_length=1, max_length=16384, description="The edited text.")
    experiment: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="The campaign or check-in draft it was written in.",
    )


type ArchiveMode = Literal["compact", "restore", "purge-cold"]


class ArchiveModeInfo(NamedTuple):
    label: str
    sentence: str


#: What each archive verb does, as the operator picks one.
ARCHIVE_MODE_INFO: Worded[ArchiveMode, ArchiveModeInfo] = Worded(
    {
        "compact": ArchiveModeInfo(
            "Compact",
            "Moves the fields nothing reads out of candidate runs into a compressed store beside "
            "them. Origin and round-parent runs are never touched — they serve almost every cache "
            "replay.",
        ),
        "restore": ArchiveModeInfo(
            "Restore", "Puts every moved field back and drops the compressed copy."
        ),
        "purge-cold": ArchiveModeInfo(
            "Purge",
            "Deletes the compressed copy for good. The rows cost real money and hours to measure "
            "again, and nothing puts them back.",
        ),
    }
)


class CompactArchivePayload(CommandPayload, kind=CommandKind.COMPACT_ARCHIVE):
    """``apply`` defaults to False on every mode, including the destructive one: a preview is what
    the operator consents on, so the write has to be asked for rather than defaulted into."""

    mode: ArchiveMode
    dataset: str | None = Field(default=None, min_length=1, max_length=64)
    apply: bool = False

    @model_validator(mode="after")
    def _dataset_is_a_dataset_name(self) -> CompactArchivePayload:
        if self.dataset is not None:
            validate_dataset_name(self.dataset)
        return self


class EditDraftCampaignPayload(DraftPayload, kind=CommandKind.EDIT_DRAFT_CAMPAIGN):
    """Sparse-patch a check-in draft; answers the draft as the patch left it."""

    # Typed: a dict defers validation past the capability gate. Required: no patch records a no-op.
    patch: EditDraftPatch


class ResolveOriginPayload(DraftPayload, kind=CommandKind.RESOLVE_ORIGIN):
    """One resolver turn over the draft's open gaps; what it proposes waits on a click."""


class ArmRequest(StrictModel):
    """One arm as asked: its key, and the optimizer it runs with that optimizer's knobs."""

    arm_key: ArmName
    optimization: OptimizationOverrides = Field(
        description="Laid over the dataset's own `optimization`: `optimizer` is required, and "
        "`budget` and `halt_at_accuracy` are refused, since every arm runs the head-to-head's "
        "budget to its end."
    )

    @property
    def optimization_sent(self) -> dict[str, JsonValue]:
        return self.optimization.model_dump(mode="json", exclude_unset=True)

    @model_serializer(mode="wrap")
    def _record_what_was_sent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """The ``CommandRecord`` carries this dump; a default there records a knob nobody set."""
        data: dict[str, JsonValue] = handler(self)
        data["optimization"] = self.optimization_sent
        return data

    @model_validator(mode="after")
    def _runs_the_head_to_heads_budget(self) -> ArmRequest:
        sent = self.optimization.model_fields_set
        if "optimizer" not in sent:
            raise ValueError("an arm names the optimizer it runs")
        if refused := sorted(sent & {"budget", "halt_at_accuracy"}):
            raise ValueError(
                f"an arm sets no {', '.join(refused)}: every arm runs the head-to-head's budget to "
                "its end"
            )
        return self


class HeadToHeadRequest(StrictModel):
    """Arms of one head-to-head asked in one act: all minted and admitted, or none."""

    head_to_head_id: ArmName
    arms: list[ArmRequest] = Field(
        min_length=1,
        max_length=16,
        description="An id already declared takes these as further arms, each held to its record.",
    )
    budget: Budget = Field(
        default_factory=Budget,
        description="Every arm's own: each campaign spends up to it on its own book. An axis left "
        "out is the dataset's.",
    )

    @model_validator(mode="after")
    def _keys_distinct(self) -> HeadToHeadRequest:
        keys = [arm.arm_key for arm in self.arms]
        if len(set(keys)) != len(keys):
            raise ValueError("an arm key names one arm")
        return self

    def declared_optimization(self, arm: ArmRequest) -> dict[str, JsonValue]:
        budget = self.budget.model_dump(mode="json", exclude_unset=True)
        return {**arm.optimization_sent, **({"budget": budget} if budget else {})}

    def purpose(self, arm: ArmRequest) -> Compare:
        """Replicate 1 as asked: the mint numbers it on the arms standing under its record lock
        (`jobs/mint.py::_numbered`)."""
        return Compare(
            arm=Arm(head_to_head_id=self.head_to_head_id, arm_key=arm.arm_key, replicate=1)
        )


class StartCheckinPayload(DraftPayload, kind=CommandKind.START_CHECKIN):
    """The draft says what the campaign IS; ``budget`` bounds what THIS launch spends, and no
    draft field holds it. Acked at admission, so it rides the draft's ledger into the root cycle."""

    budget: Budget = Budget()
    diag: bool = Field(default=False, description="Run the diagnostic shape.")
    head_to_head: HeadToHeadRequest | None = Field(
        default=None,
        description="Start the draft as these arms of one head-to-head, its origin every arm's, "
        "in place of one campaign. Its own `budget` binds each arm, so `budget` and `diag` are "
        "refused beside it.",
    )

    @model_validator(mode="after")
    def _arms_run_their_own_budget(self) -> StartCheckinPayload:
        if self.head_to_head is not None and (self.budget.named or self.diag):
            raise ValueError("a head-to-head's arms run its budget and the full shape")
        return self


class MintHeadToHeadPayload(CommandPayload, kind=CommandKind.MINT_HEAD_TO_HEAD):
    """Mint arms of one head-to-head on a dataset in one act and launch each — every arm planned,
    priced and admitted before any is minted, so a refused arm leaves none behind."""

    dataset_name: str = Field(min_length=1, max_length=64)
    head_to_head: HeadToHeadRequest

    @model_validator(mode="after")
    def _name_is_a_dataset_name(self) -> MintHeadToHeadPayload:
        validate_dataset_name(self.dataset_name)
        return self


class MintCampaignPayload(CommandPayload, kind=CommandKind.MINT_CAMPAIGN):
    """Mint a campaign on an authored dataset and launch its root cycle."""

    dataset_name: str = Field(min_length=1, max_length=64)
    budget: Budget = Budget()
    optimization: OptimizationOverrides | None = Field(
        default=None,
        description="Laid over the dataset's own `optimization` before the mint freezes it; only "
        "the fields sent move anything",
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
    def optimization_sent(self) -> dict[str, JsonValue]:
        opt = self.optimization
        return {} if opt is None else opt.model_dump(mode="json", exclude_unset=True)

    @model_serializer(mode="wrap")
    def _record_what_was_sent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        """The ``CommandRecord`` carries this dump; a default there records a knob nobody set."""
        data: dict[str, JsonValue] = handler(self)
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


# One file's bound at every door: each transport reads one byte past it, and the bytes' own
# validation refuses that byte (`IngestUploadPayload.blob`, the candidate-library upload).
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UploadBytes = Annotated[bytes, Field(max_length=MAX_UPLOAD_BYTES)]


class IngestUploadPayload(CommandPayload, kind=CommandKind.INGEST_UPLOAD):
    """Parse an uploaded tabular file into a durable check-in draft; ``start-checkin`` mints its campaign."""

    # A JSON door (a host's tool call) sends the bytes base64; a form or a terminal hands them raw.
    model_config = ConfigDict(val_json_bytes="base64", ser_json_bytes="base64")

    filename: str = Field(default="", max_length=256)
    slug: str | None = Field(default=None, max_length=64, description="Optional slug override.")
    # The bytes ride the act, never its record: the upload store keeps them (`record_upload`).
    blob: UploadBytes = Field(
        exclude=True,
        repr=False,
        description="The file's bytes, base64: any tabular file (delimited text in any encoding, "
        "JSON, JSONL, XLSX, XLS, ODS); any columns.",
    )


_DRAFT_OPENED = (
    "Check-in {draft_id} is open: finish it in the browser, or drop it with `discard-draft`."
)


class OpenDatasetDraftPayload(CommandPayload, kind=CommandKind.OPEN_DATASET_DRAFT):
    """Open an authored dataset's files as a prefilled check-in draft; nothing runs until ``start-checkin``."""

    follow_up: ClassVar[str] = _DRAFT_OPENED

    dataset_name: str = Field(min_length=1, max_length=64)


class OpenOriginDraftPayload(CommandPayload, kind=CommandKind.OPEN_ORIGIN_DRAFT):
    """Open a prior origin as a prefilled check-in draft, its prompts verbatim; nothing runs until ``start-checkin``."""

    follow_up: ClassVar[str] = _DRAFT_OPENED

    origin_id: str = Field(min_length=1, max_length=128)


class SetUserSettingsPayload(CommandPayload, kind=CommandKind.SET_USER_SETTINGS):
    """Persist the caller's per-user preferences."""

    demo_mode_enabled: bool


class AcceptTermsPayload(CommandPayload, kind=CommandKind.ACCEPT_TERMS):
    """Record the caller's acceptance of the Terms, server-stamped; a version other than the live one is refused."""

    version: str = Field(min_length=1, max_length=64)


class SetApiKeyPayload(CommandPayload, kind=CommandKind.SET_API_KEY):
    """Hold the caller's own key for a provider: its sends there go out on it, past the coupon.
    Never served back, and recorded masked."""

    provider: str = Field(min_length=1, max_length=64)
    secret: SecretStr


class RemoveApiKeyPayload(CommandPayload, kind=CommandKind.REMOVE_API_KEY):
    """Drop the caller's key for a provider; its next send there draws the coupon again."""

    provider: str = Field(min_length=1, max_length=64)


# The last payload model is declared above, so a kind carrying none fails right here.
if _unpaired := sorted(set(CommandKind) - PAYLOAD_MODEL_FOR_KIND.keys()):
    raise RuntimeError(f"command kinds carried by no payload model: {_unpaired}")
# A payload keeping an undeclared key carries a steer no handler owns, an endpoint among them.
if _lax := sorted(
    k for k, m in PAYLOAD_MODEL_FOR_KIND.items() if m.model_config["extra"] != "forbid"
):
    raise RuntimeError(f"command payloads accepting undeclared keys: {_lax}")


class CommandAcceptedBody(StrictModel):
    """What a command whose applied form answers nothing returns: the record it appended."""

    command_id: str = Field(description="Stable id of the appended `CommandRecord`.")


class ReadKind(StrEnum):
    """The read catalogue beside `CommandKind`. A row's wire inputs and its answer are the
    `application/read_params.py::ReadParams` model that declares it, as a payload declares its kind.
    The route serving a row is NAMED by its kind, and `presentation/api/catalogue.py::bind_surface`
    refuses at import a session route no row names, a row no route serves, and an answer whose
    return is not its route's model."""

    ME = "me"
    USER_SETTINGS = "user-settings"
    QUOTA_STATUS = "quota-status"
    COUPON = "coupon"
    ACTIVITY = "activity"
    ACTIVE_SESSION = "active-session"
    MACHINE_STATUS = "machine-status"
    OPTIMIZERS = "optimizers"
    LIBRARY = "library"
    BACKENDS = "backends"
    BACKEND_HEALTH = "backend-health"
    HIERARCHY = "hierarchy"
    HIERARCHY_STEPS = "hierarchy-steps"
    HIERARCHY_SUBTREE = "hierarchy-subtree"
    CYCLE_DASHBOARD = "cycle-dashboard"
    CYCLE_ROUND = "cycle-round"
    CYCLE_ROUND_AUDIT = "cycle-round-audit"
    CYCLE_EXPORT = "cycle-export"
    CYCLE_EVENTS = "cycle-events"
    CYCLE_FILES = "cycle-files"
    CYCLE_FILE = "cycle-file"
    EVIDENCE = "evidence"
    # A read on a POST: no LLM call is made and nothing is minted.
    HEAD_TO_HEAD_PRICE = "head-to-head-price"
    DRAFT = "draft"
    CAMPAIGN_PIPELINE = "campaign-pipeline"
    # A read on a POST: nothing is written.
    FORK_PREVIEW = "fork-preview"
    CONFIG_MAP = "config-map"
    CAMPAIGN_STORAGE = "campaign-storage"
    WORKSPACE_STORAGE = "workspace-storage"
    DATASETS = "datasets"
    DATASET_PIPELINE = "dataset-pipeline"
    DATASET_CELLS = "dataset-cells"
    DATASET_CELL = "dataset-cell"
    ORIGINS = "origins"
