"""The `/commands/{kind}` vocabulary, in `domain/` so the terminal and the command log's fold read it without the dispatcher."""

from __future__ import annotations

import hashlib
import json
import re
from collections import ChainMap
from collections.abc import Collection, Iterable, Mapping
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, NamedTuple, Self, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    SecretStr,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    ValidationError,
    model_serializer,
    model_validator,
)

from promptpotter.domain.bulletin import BULLETIN_TEXT_CHARS
from promptpotter.domain.campaign import ArmName, ArmSeat
from promptpotter.domain.cycle_paths import PathComponent
from promptpotter.domain.dataset_name import DatasetName, validate_dataset_name
from promptpotter.domain.draft_edit import (
    CampaignLayer,
    EditDraftPatch,
    OptimizationOverrides,
)
from promptpotter.domain.fork import ConfigOverrides
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.phases import (
    INNER_ARMS_REFUSAL,
    INNER_LAUNCH_REFUSAL,
    INNER_PAUSE_REFUSAL,
    INNER_SKIP_REFUSAL,
    STOP_REASON_INFO,
    RunAction,
)
from promptpotter.domain.pipeline_schema import ParamIntent
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.scoring import MAX_PASS_ROWS
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import RequestBody, StrictModel, UploadBytes, WireInt
from promptpotter.domain.verification import VerifyStrategy
from promptpotter.shared.errors import NextAct
from promptpotter.shared.identity import (
    ACCOUNT_KEYS_CAP,
    ACCOUNT_SELF_CAP,
    ACCOUNT_TOKENS_CAP,
    CAMPAIGN_BUDGET_CAP,
    CAMPAIGN_CREATE_CAP,
    CAMPAIGN_LIFECYCLE_CAP,
    CAMPAIGN_LOOKAHEAD_CAP,
    CAMPAIGN_RUN_CAP,
    CAMPAIGN_STEP_CAP,
    IdentityContext,
    has_capability,
)
from promptpotter.shared.named_rows import require_own_docstring
from promptpotter.shared.worded import Worded


class Press(NamedTuple):
    """Who may press a kind: the terminal verb, whether a host assistant may, and why the browser does not."""

    cli_verb: str
    host: bool
    no_browser: str = ""


def anyone(verb: str, *, no_browser: str = "") -> Press:
    """The terminal by *verb* and a host's tool call, and the browser unless *no_browser* says why not."""
    return Press(verb, host=True, no_browser=no_browser)


def person(verb: str) -> Press:
    """A person's own act at the browser or a terminal by *verb*; a host never presses it for them."""
    return Press(verb, host=False)


# The browser alone: no terminal verb and no host tool.
BROWSER = None


class Door(StrEnum):
    """Where a call is filed, which its kind's `Press` admits or refuses (`CommandKind.pressed_at`)."""

    # The REST API: every kind it mounts, the browser's among them.
    API = "api"
    TERMINAL = "terminal"
    # A host assistant: its tools, and an embedding host's `host_dispatcher`.
    HOST = "host"


_MINTS_THROUGH_CHECKIN = "the browser mints through a check-in"
_HOST_ADMINISTERS = "the host administers from its terminal or its bot"

# How a door answers a kind: inline with its applied answer, or as a task its door tracks.
type Answering = Literal["inline", "task"]


class CommandKind(StrEnum):
    """A row is `kind, handler, capability, press[, destructive[, answering]]`: the handler is
    `application/commands/{module}:{attr}`; every kind names its capability;
    `destructive` is MCP's `destructiveHint`, true for an apply that overwrites or removes."""

    handler: str
    capability: str
    press: Press | None
    destructive: bool
    answering: Answering

    def __new__(
        cls,
        kind: str,
        handler: str,
        capability: str,
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
    def browser(self) -> bool:
        """The webapp presses it."""
        return self.press is None or not self.press.no_browser

    def pressed_at(self, door: Door) -> bool:
        match door:
            case Door.API:
                return True
            case Door.TERMINAL:
                return self.cli_verb is not None
            case Door.HOST:
                return self.host

    PAUSE = (
        "pause",
        "loop_commands:pause_cycle",
        CAMPAIGN_STEP_CAP,
        anyone("pause"),
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
    SET_SAMPLE_LOOKAHEAD = (
        "set-sample-lookahead",
        "loop_commands:set_sample_lookahead",
        CAMPAIGN_LOOKAHEAD_CAP,
        anyone("set-sample-lookahead"),
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
        anyone("new", no_browser=_MINTS_THROUGH_CHECKIN),
        False,
        "task",
    )
    HEAD_TO_HEAD = (
        "head-to-head",
        "launching:mint_head_to_head",
        CAMPAIGN_CREATE_CAP,
        anyone("head-to-head", no_browser=_MINTS_THROUGH_CHECKIN),
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
    HELD_OUT = (
        "held-out",
        "measuring:held_out",
        CAMPAIGN_RUN_CAP,
        anyone("held-out"),
        True,
        "task",
    )
    # Its act is asking: the pass it names reads it as its pause (`run_phase_control.py::RunControl`).
    # Acts under the rung of the command it stops (`dispatcher.py::_rung`); its own, for none filed.
    CANCEL = "cancel", "cancelling:cancel", CAMPAIGN_RUN_CAP, anyone("cancel"), True
    SET_LIMITS = (
        "set-limits",
        "limits_and_queue:change_run_limits",
        CAMPAIGN_BUDGET_CAP,
        anyone("set-limits"),
        True,
    )
    # Account-scoped, and set only by whoever runs this server: `quota.py::set_concurrent_cycles`.
    SET_CONCURRENT_CYCLES = (
        "set-concurrent-cycles",
        "limits_and_queue:set_concurrent_cycles",
        CAMPAIGN_BUDGET_CAP,
        anyone("set-concurrent-cycles"),
        True,
    )
    # A queued MINT has no cycle to address yet, and WHOSE launch it is `cancel_queued_run` checks.
    CANCEL_QUEUED = (
        "cancel-queued",
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
        anyone("archive-campaign"),
        True,
    )
    UNARCHIVE_CAMPAIGN = (
        "unarchive-campaign",
        "workspace_edits:campaign_lifecycle",
        CAMPAIGN_LIFECYCLE_CAP,
        anyone("unarchive-campaign"),
        True,
    )
    DELETE_CAMPAIGN = (
        "delete-campaign",
        "workspace_edits:campaign_lifecycle",
        CAMPAIGN_LIFECYCLE_CAP,
        anyone("delete-campaign"),
        True,
    )
    # The label is how every other surface addresses the campaign to a human.
    RENAME_CAMPAIGN = (
        "rename-campaign",
        "workspace_edits:set_campaign_label",
        CAMPAIGN_LIFECYCLE_CAP,
        anyone("rename-campaign"),
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
    # Filed on the workspace: the draft it discards goes with its apply.
    DISCARD_DRAFT = (
        "discard-draft",
        "draft_editing:discard_draft",
        CAMPAIGN_CREATE_CAP,
        anyone("discard-draft"),
        True,
    )
    # The three doors a check-in draft opens by; each is filed on the workspace, as no draft is open yet.
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
        ACCOUNT_SELF_CAP,
        anyone("set-user-settings"),
        True,
    )
    # A person's consent: their own act, never a host's.
    ACCEPT_TERMS = (
        "accept-terms",
        "workspace_edits:accept_terms",
        ACCOUNT_SELF_CAP,
        person("accept-terms"),
    )
    # The host's word to every account: its terminal and its admin bot post (`bulletins.py::post_bulletin`).
    POST_BULLETIN = (
        "post-bulletin",
        "bulletin_acts:post_bulletin",
        ACCOUNT_SELF_CAP,
        Press(
            "post-bulletin", host=False, no_browser="the host posts from its terminal or its bot"
        ),
    )
    # The host's word taken back, from the doors that posted it; its dismissals go with it.
    WITHDRAW_BULLETIN = (
        "withdraw-bulletin",
        "bulletin_acts:withdraw_bulletin",
        ACCOUNT_SELF_CAP,
        Press(
            "withdraw-bulletin",
            host=False,
            no_browser="the host withdraws from its terminal or its bot",
        ),
    )
    # Its account's own act: a host never dismisses one for its person.
    DISMISS_BULLETIN = (
        "dismiss-bulletin",
        "bulletin_acts:dismiss_bulletin",
        ACCOUNT_SELF_CAP,
        person("dismiss-bulletin"),
    )
    # The host's administration of who may sign in and what each account holds: its terminal and its
    # admin bot file them, refused to any other (`account.py::require_host`), decisions § 0004.
    BLOCK_EMAIL = (
        "block-email",
        "operator_acts:block_email",
        ACCOUNT_SELF_CAP,
        Press("block-email", host=False, no_browser=_HOST_ADMINISTERS),
    )
    UNBLOCK_EMAIL = (
        "unblock-email",
        "operator_acts:unblock_email",
        ACCOUNT_SELF_CAP,
        Press("unblock-email", host=False, no_browser=_HOST_ADMINISTERS),
    )
    GRANT_DELEGATION = (
        "grant-delegation",
        "operator_acts:grant_delegation",
        ACCOUNT_SELF_CAP,
        Press("grant-delegation", host=False, no_browser=_HOST_ADMINISTERS),
    )
    REVOKE_DELEGATION = (
        "revoke-delegation",
        "operator_acts:revoke_delegation",
        ACCOUNT_SELF_CAP,
        Press("revoke-delegation", host=False, no_browser=_HOST_ADMINISTERS),
        True,
    )
    SET_COUPON = (
        "set-coupon",
        "operator_acts:set_coupon",
        ACCOUNT_SELF_CAP,
        Press("set-coupon", host=False, no_browser=_HOST_ADMINISTERS),
    )
    DELETE_ACCOUNT = (
        "delete-account",
        "operator_acts:delete_account",
        ACCOUNT_SELF_CAP,
        Press("delete-account", host=False, no_browser=_HOST_ADMINISTERS),
        True,
    )
    # Browser-only ON PURPOSE: a key on a terminal's argv lands in its shell history, and the
    # terminal sends on the box's own keys.
    SET_API_KEY = "set-api-key", "workspace_edits:set_api_key", ACCOUNT_KEYS_CAP, BROWSER, True
    REMOVE_API_KEY = (
        "remove-api-key",
        "workspace_edits:remove_api_key",
        ACCOUNT_KEYS_CAP,
        BROWSER,
        True,
    )
    # Browser-only ON PURPOSE: the terminal is the host's own machine, and the host is refused on
    # the bearer door; its capability is never a token's, so no bearer reaches these three.
    MINT_API_TOKEN = "mint-api-token", "workspace_edits:mint_api_token", ACCOUNT_TOKENS_CAP, BROWSER
    LIST_API_TOKENS = (
        "list-api-tokens",
        "workspace_edits:list_api_tokens",
        ACCOUNT_TOKENS_CAP,
        BROWSER,
    )
    REVOKE_API_TOKEN = (
        "revoke-api-token",
        "workspace_edits:revoke_api_token",
        ACCOUNT_TOKENS_CAP,
        BROWSER,
        True,
    )


def filable(identity: IdentityContext) -> tuple[CommandKind, ...]:
    """The map from a kind's `capability` to *identity*'s: the kinds whose capability it holds,
    which every served offer is built from. Outside it stand the dispatcher's two other refusals,
    a cancel's rung (`dispatcher.py::_rung`, the capability of the command it stops) and a kind's
    door (`CommandKind.pressed_at`): an offer is a kind its reader holds, never a door it reached."""
    return tuple(k for k in CommandKind if has_capability(identity, k.capability))


# The kind each run verb files (`RunAdmission.offers`): a resume is a start of a paused cycle.
RUN_VERB_KIND: dict[RunAction, CommandKind] = {
    "pause": CommandKind.PAUSE,
    "resume": CommandKind.START_RUN,
    "start": CommandKind.START_RUN,
}


def acts_filed(acts: Iterable[NextAct], kinds: Collection[CommandKind]) -> tuple[NextAct, ...]:
    """*acts* a reader filing *kinds* may take: a kind among them, or a field or a file."""
    return tuple(act for act in acts if not act.kind or act.kind in kinds)


# The longest command id a door sends or names; `CommandCall` mints a uuid's 36.
COMMAND_ID_CHARS = 64

# Filled as each model declares the kind it carries: the ONE kind → payload table, keyed `str`.
PAYLOAD_MODEL_FOR_KIND: dict[str, type[CommandPayload]] = {}


class CommandPayload(RequestBody):
    """Base of every payload on the generic ``POST /commands/{kind}`` route; THE MODEL IS the accepted-key set.
    A payload's address base is the subject it is filed on."""

    kind: ClassVar[CommandKind]
    # A secret's text enters no digest, so a retry under a key acts again; declared by each payload holding a `SecretStr`.
    unmatched: ClassVar[bool] = False
    # What the terminal prints after the ack; a `{field}` in it reads that field of the applied answer.
    follow_up: ClassVar[str] = ""
    # A field's terminal spelling by its dotted path, a flag or (no dash) a positional, where it is
    # not the default `cli_spelling` gives; each model up the line declares its own. The terminal
    # builds its arguments from `cli_spelling` and spells an act's.
    cli_spellings: ClassVar[Mapping[str, str]] = {
        "budget.usd": "--max-usd",
        "budget.tokens": "--max-tokens",
        "budget.rounds": "--max-rounds",
        "budget.wall_clock_s": "--max-wall-clock",
    }

    @classmethod
    def cli_spelling(cls, path: str) -> str:
        """The terminal's spelling of the field at dotted *path*: as declared, else a required
        field's own name as a positional, else `--` its dashed leaf."""
        said: ChainMap[str, str] = ChainMap(
            *(vars(m).get("cli_spellings", {}) for m in cls.__mro__)
        )
        if spelled := said.get(path):
            return spelled
        held = cls.model_fields.get(path)
        return (
            path
            if held is not None and held.is_required()
            else f"--{path.rpartition('.')[2].replace('_', '-')}"
        )

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

    def act_digest(self) -> str:
        """What an idempotency key names: the act as its `CommandRecord` keeps it, and the bytes of
        each field it leaves out; a secret's text enters no stored digest."""
        dump = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(f"{self.kind}\0".encode())
        digest.update(dump.encode())
        for name, info in type(self).model_fields.items():
            value = getattr(self, name)
            if not info.exclude or value is None:
                continue
            raw = value.encode() if isinstance(value, str) else value
            if not isinstance(raw, bytes):
                raise TypeError(f"{type(self).__name__}.{name} drops a {type(value).__name__}")
            digest.update(f"\0{name}\0{len(raw)}\0".encode())
            digest.update(raw)
        return digest.hexdigest()


class ReasonedPayload(CommandPayload):
    reason: str = Field(default="", max_length=512, description="Recorded with the command.")


class CampaignPayload(CommandPayload):
    campaign_id: str = Field(
        min_length=1,
        max_length=128,
        description="The campaign acted on, by its id or a prefix naming one, resolved by the "
        "dispatcher for every door; the root of a `descend` chain is named by its full id.",
    )


class CyclePayload(CampaignPayload):
    """A cycle's address: the root hop, plus the ``?descend=`` tail the reads take; ``inner_refusal`` is each
    kind's own answer to whether it acts on an inner run."""

    cycle_id: str = Field(min_length=1, max_length=128)
    # Excluded: the dispatcher spends it resolving the leaf, so a recorded tail addresses it twice.
    descend: str | None = Field(default=None, max_length=512, exclude=True)

    # ``None`` = acts on an inner run. Undeclared here, so a new kind fails at declaration until it answers.
    inner_refusal: ClassVar[str | None]
    # False: a terminal names the cycle, never defaulting to the active one.
    implied_target: ClassVar[bool] = True
    cli_spellings: ClassVar[Mapping[str, str]] = {
        "campaign_id": "--campaign",
        "cycle_id": "--cycle",
    }


class LoopPayload(CyclePayload):
    """Only a RUNNING loop takes it, off its cycle's inbox (`command_log.py::Controls`) — which is why these sit below `application/`."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # `command_log.py::LOOP_KINDS` reads the kind table having imported this module alone.
        if cls.__module__ != __name__:
            raise TypeError(f"{cls.__name__} is a loop payload declared outside {__name__}")


class SkipSearchpointPayload(LoopPayload, kind=CommandKind.SKIP_SEARCHPOINT):
    """Cut the candidate being scored at its next sample boundary; the round carries on with the rest."""

    inner_refusal: ClassVar[str | None] = INNER_SKIP_REFUSAL
    follow_up: ClassVar[str] = "The round goes on with the rest."


class PauseCyclePayload(ReasonedPayload, LoopPayload, kind=CommandKind.PAUSE):
    """Ask a running cycle to stop at its next checkpoint; it resumes from there."""

    inner_refusal: ClassVar[str | None] = INNER_PAUSE_REFUSAL
    follow_up: ClassVar[str] = "The loop exits at its next checkpoint; `resume` picks it up."


# `max` names the backend's ceiling, resolved at each cell, so it moves with the ceiling.
type LookaheadCells = Annotated[WireInt, Field(ge=1)] | Literal["max"]
# `all` names every candidate still walking, however many the round has left.
type LookaheadAbreast = Annotated[WireInt, Field(ge=1)] | Literal["all"]


class LookaheadPress(StrictModel):
    """How many cells ahead each candidate sends, how many candidates send at once, and whether
    that stands every round."""

    cells: LookaheadCells = Field(
        description="Samples per candidate: 1 is one at a time; `max` holds as many as the backend "
        "admits, wherever its ceiling moves."
    )
    abreast: LookaheadAbreast = Field(
        default=1,
        description="Candidates at once, counted from the one deciding in walk order: 1 sends "
        "ahead for that one alone; `all` for every candidate still walking.",
    )
    auto: bool = Field(
        default=False,
        description="This press stands every round, until pressed off.",
    )

    @property
    def armed(self) -> bool:
        """False on a disarm: one at a time on both axes, this round only."""
        return self.auto or self.cells != 1 or self.abreast != 1

    @property
    def max_speed(self) -> bool:
        return self.cells == "max" and self.abreast == "all"


UNARMED = LookaheadPress(cells=1)
MAX_SPEED = LookaheadPress(cells="max", abreast="all")


class SetSampleLookaheadPayload(LookaheadPress, LoopPayload, kind=CommandKind.SET_SAMPLE_LOOKAHEAD):
    """Arm how many cells ahead each candidate sends and how many candidates send at once."""

    # Pressed on an inner run, it is that run's own; unpressed, an inner run follows its
    # enclosing run's Max speed (`CommandBook.lookahead`).
    inner_refusal: ClassVar[str | None] = None
    follow_up: ClassVar[str] = (
        "The walk takes it at its next cell; `dashboard` reads the depth in force."
    )
    cli_spellings: ClassVar[Mapping[str, str]] = {"auto": "--every-round"}


# A launch's step: its run pauses `stepped` past this many rounds, 0 before round 1 closes.
StepRounds = Annotated[WireInt, Field(ge=0, le=100)] | None


class LaunchPayload(CommandPayload):
    """A launch kind: its run may start at a look-ahead, filed as `set-sample-lookahead` on its
    cycle once the run holds it, before the walk's first cell."""

    cli_spellings: ClassVar[Mapping[str, str]] = {
        "lookahead.cells": "--lookahead",
        "lookahead.abreast": "--abreast",
        "lookahead.auto": "--every-round",
    }

    lookahead: LookaheadPress | None = Field(
        default=None,
        description="The look-ahead this launch's walk starts at, pressed as "
        "`set-sample-lookahead`.",
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
    """Remove ONE stub cycle, the singular of the empty-cycle sweep; refused for a cycle holding
    rounds or a live producer (pause it first)."""

    inner_refusal: ClassVar[str | None] = _OUTER_OWNS_TREE
    # An implied target would make the likeliest typo the delete.
    implied_target: ClassVar[bool] = False


class CleanupEmptyCyclesPayload(CyclePayload, kind=CommandKind.CLEANUP_EMPTY_CYCLES):
    """Remove the stub cycles a mint left behind when it never reached round 0."""

    inner_refusal: ClassVar[str | None] = _OUTER_OWNS_TREE


class ChangeRunLimitsPayload(CyclePayload, kind=CommandKind.SET_LIMITS):
    """Raise or lower an existing cycle's spend, token, round or wall-clock bound, or switch its
    overshoot; a budget- or round-halted cycle continues by a higher ceiling, then a `start-run`.
    Spend is clamped against the account allowance. On an arm of a head-to-head it moves that arm
    alone, and leaves the comparison only when a ceiling or the switch moved."""

    inner_refusal: ClassVar[str | None] = (
        "An inner run spends under the outer run's ceiling. Change the outer run's limits instead."
    )
    follow_up: ClassVar[str] = (
        "`resume` picks it up; an arm of a head-to-head leaves its comparison only when a ceiling "
        "moved. Armed now:\n{said}"
    )

    # An axis it does not name stays as it stands; ``unbounded`` lifts it.
    budget: Budget = Budget()
    overshoot: bool | None = Field(
        default=None,
        description="on: a call holds its node's mean billed cost in this run once one has "
        "billed, a bill past it drawn on the run's hold and reported; off (the default): every "
        "call holds its worst case, so no ceiling is ever passed. Omit to leave it as it stands.",
    )

    @model_validator(mode="after")
    def _names_an_axis(self) -> ChangeRunLimitsPayload:
        if not self.budget.named and self.overshoot is None:
            raise ValueError(
                "a budget naming at least one of usd / tokens / rounds / wall_clock_s, or "
                "`overshoot`, is required"
            )
        return self


class StartRunPayload(LaunchPayload, CyclePayload, kind=CommandKind.START_RUN):
    """A run of an existing cycle: what bounds it (``budget``, ``step_rounds``) and the shape it
    takes (``mode``)."""

    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL
    cli_spellings: ClassVar[Mapping[str, str]] = {"mode.from_round": "--from"}

    budget: Budget = Budget()
    step_rounds: StepRounds = None
    mode: RunMode = RunMode()


class ForkStart(StrictModel):
    """A fork's `CycleStart` as sent: the applier freezes `node_narrowing` on the forked cycle's
    own rows and stamps the `origin_source` the wire omits."""

    origin_prompts: dict[str, NodePrompt] = Field(default_factory=dict)
    pipeline_overlay: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per node, its keys laid over the forked candidate's: `null` drops the "
        "candidate's own value, so the layer below shows through.",
    )
    config_overrides: ConfigOverrides = Field(default_factory=ConfigOverrides)
    node_narrowing: dict[str, list[ParamIntent]] = Field(
        default_factory=dict,
        description="Per node, the rows as drawn; overrides the campaign's narrowing for this "
        "cycle only, and a node it does not name inherits.",
    )

    @model_validator(mode="after")
    def _budget_rides_the_payload(self) -> ForkStart:
        if self.config_overrides.budget.named:
            raise ValueError("a fork's budget is the payload's own `budget`, not the seed's")
        return self

    @property
    def cleared(self) -> dict[str, dict[str, None]]:
        """The keys the seed clears, as a patch of the forked candidate's node document."""
        return {
            node: kept
            for node, keys in self.pipeline_overlay.items()
            if (kept := {k: v for k, v in keys.items() if v is None})
        }

    @property
    def laid(self) -> dict[str, dict[str, object]]:
        """The values the seed sets, its clears apart."""
        return {
            node: kept
            for node, keys in self.pipeline_overlay.items()
            if (kept := {k: v for k, v in keys.items() if v is not None})
        }


class ForkCyclePayload(ReasonedPayload, LaunchPayload, CyclePayload, kind=CommandKind.FORK_CYCLE):
    """The cut, and the shape of the fork's first run (``mode``) — one act, so one command.
    A parent a producer still holds is paused by the applier once the fork's run is admitted."""

    inner_refusal: ClassVar[str | None] = INNER_LAUNCH_REFUSAL
    cli_spellings: ClassVar[Mapping[str, str]] = {"round": "--rewind", "reason": "--rewind-reason"}

    budget: Budget = Field(
        default_factory=Budget,
        description="Bounds the fork's first launch, as a start-run's; an axis left out is the "
        "fork's config's, inherited from its parent.",
    )
    mode: RunMode = RunMode()
    round: int = Field(default=0, ge=0)
    candidate_id: str = Field(default="", max_length=128)
    start: ForkStart = ForkStart()
    keep_rounds: bool = Field(
        default=False,
        description=(
            "False (the default) is `operator_steered`: a clean offshoot from the origin, "
            "re-scoring the edited searchpoint. True is `operator_rewind` — rounds 0..round-1 "
            "are lifted and the fork continues at `round` under the start's overrides, which is "
            "what an 'apply this from here' press means and what the terminal spells "
            "`resume --rewind N`."
        ),
    )

    @model_validator(mode="after")
    def _rewinds_past_its_origin(self) -> ForkCyclePayload:
        if self.mode.from_round is not None:
            raise ValueError(
                "a fork's cut is `round`; `mode.from_round` would rewind its parent in place too, "
                "so it is refused here: cut at the round instead"
            )
        if self.keep_rounds and self.round == 0:
            raise ValueError(
                "keep_rounds at round 0 lifts no round to continue from; fork without "
                "keep_rounds to start over at the origin"
            )
        if self.keep_rounds and self.candidate_id:
            raise ValueError(
                "keep_rounds re-runs round `round` from where its line stood going into it, so it "
                "names no candidate; fork without keep_rounds to start from one"
            )
        if self.keep_rounds and self.start.origin_prompts:
            raise ValueError(
                "keep_rounds lifts the parent's round 0 as its origin, so the start must not "
                "declare origin_prompts; fork without keep_rounds to start from an edited origin"
            )
        return self


class VerifyCandidatePayload(CyclePayload, kind=CommandKind.VERIFY_CANDIDATE):
    """``samples`` omitted is the ANSWER, not an absence: the count is derived from the
    per-candidate round budget and the rounds run since this cycle's last verification. A larger
    explicit count is
    refused, naming the budget — which is what keeps one click on a million-row dataset from being
    a million-cell bill.

    Addressed as the ``evidence`` read addresses a searchpoint — cycle, descent, ``candidate_id``
    — so an L4 inner candidate is as reachable as a top-level one."""

    inner_refusal: ClassVar[str | None] = None

    candidate_id: str = Field(min_length=1, max_length=128)
    samples: WireInt | None = Field(default=None, ge=1, le=MAX_PASS_ROWS)
    strategy: VerifyStrategy = "random"
    seed: WireInt | None = None


class LiveCyclePayload(CampaignPayload):
    """Addressed to a campaign, applied to and recorded on the cycle holding its line."""


class HeldOutPayload(LiveCyclePayload, kind=CommandKind.HELD_OUT):
    """Grade the campaign's origin and its selection on the held-out bench rows."""


class ArchiveCampaignPayload(ReasonedPayload, CampaignPayload, kind=CommandKind.ARCHIVE_CAMPAIGN):
    """Flag a campaign archived: hidden from the default listing, restorable by unarchive; the tree
    does not move."""


class UnarchiveCampaignPayload(
    ReasonedPayload, CampaignPayload, kind=CommandKind.UNARCHIVE_CAMPAIGN
):
    """Restore an archived campaign to active."""


class DeleteCampaignPayload(ReasonedPayload, CampaignPayload, kind=CommandKind.DELETE_CAMPAIGN):
    """Delete a campaign for good; other campaigns still reuse its cached measurements."""

    keep_results: bool = Field(
        default=False,
        description="Keep its manifest, reports and summary trace; delete only what resuming, "
        "auditing and mirroring it needs.",
    )


LifecyclePayload = ArchiveCampaignPayload | UnarchiveCampaignPayload | DeleteCampaignPayload


class SetCampaignLabelPayload(CampaignPayload, kind=CommandKind.RENAME_CAMPAIGN):
    """Name a campaign wherever it is named to a human; its id still addresses it."""

    # Required, and `""` is the clear: a default would give "clear" two spellings.
    label: str = Field(max_length=200, description='`""` restores the dataset-name fallback.')


class DraftAddressedPayload(CommandPayload):
    draft_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class DraftPayload(DraftAddressedPayload):
    """Filed on the draft, whose Start lands what it was commanded on the root cycle."""


class DiscardDraftPayload(DraftAddressedPayload, kind=CommandKind.DISCARD_DRAFT):
    """Discard an open check-in for good; what its calls cost stays billed to the workspace."""


class CancelQueuedRunPayload(CommandPayload, kind=CommandKind.CANCEL_QUEUED):
    """Withdraw a launch waiting for a machine slot; nothing ran and nothing was spent."""

    job_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="The queued job id, as `machine-status` lists it.",
    )


class CancelPayload(CommandPayload, kind=CommandKind.CANCEL):
    """Stop a command still acting, by its id: a verify or bench pass stops at its next cell and
    answers over what it measured. One that ended is left as it ended."""

    command_id: str = Field(
        min_length=1,
        max_length=COMMAND_ID_CHARS,
        description="The command's id, as its door answered it or was sent it.",
    )


class SetConcurrentCyclesPayload(CommandPayload, kind=CommandKind.SET_CONCURRENT_CYCLES):
    """How many campaigns this account may hold at once, queued launches included; at most the
    machine's capacity, and set only by whoever runs this server."""

    max_concurrent_cycles: WireInt = Field(ge=1, description="The new limit.")


class BankSnippetPayload(CommandPayload, kind=CommandKind.BANK_SNIPPET):
    """Bank an edited library snippet into this account's own block pack as the next version of
    the one it was edited from; a text the library already holds is that snippet."""

    supersedes: str = Field(
        min_length=1, max_length=64, description="The id of the snippet the text was edited from."
    )
    text: str = Field(min_length=1, max_length=16384, description="The edited text.")
    experiment: PathComponent = Field(
        min_length=1,
        max_length=128,
        description="The campaign or check-in draft it was written in.",
    )


type ArchiveMode = Literal["compact", "restore", "purge-cold", "reclaim"]


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
        "reclaim": ArchiveModeInfo(
            "Reclaim",
            "Deletes every answer one dataset measured, and names the runs that lose them. "
            "Nothing puts them back.",
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
    """One resolver turn over the draft's open gaps; what it proposes waits on a click. The turn
    is bought only where one is owed (no turn ever set the draft up, and its prompts are not
    authored), whichever door sends it: any other is refused `resolver_not_owed`, unless it is the
    operator's re-run."""

    rerun: bool = Field(
        default=False,
        description="The operator asked for this turn again (the check-in's re-run): bought "
        "whether or not one is owed. An answer that names no task sends it without.",
    )


class ArmRequest(StrictModel):
    """One arm as asked: its key, and the optimizer it runs with that optimizer's knobs."""

    arm_key: ArmName
    optimization: OptimizationOverrides = Field(
        description="Laid over the dataset's own `optimization`: `optimizer` is required, and "
        "`halt_at_fitness` is refused, since every arm runs the head-to-head's budget to its end."
    )

    @property
    def optimization_sent(self) -> dict[str, JsonValue]:
        sent: dict[str, JsonValue] = self.optimization.model_dump(mode="json", exclude_unset=True)
        return sent

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
        if "halt_at_fitness" in sent:
            raise ValueError(
                "an arm sets no halt_at_fitness: every arm runs the head-to-head's budget to its end"
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

    def seat(self, arm: ArmRequest) -> ArmSeat:
        """The mint numbers its replicate on the arms standing under its record lock."""
        return ArmSeat(head_to_head_id=self.head_to_head_id, arm_key=arm.arm_key)


class StartCheckinPayload(LaunchPayload, DraftPayload, kind=CommandKind.START_CHECKIN):
    """The draft says what the campaign IS; ``budget`` bounds what THIS launch spends, and no
    draft field holds it. Acked at admission, so it lands with the draft on the root cycle."""

    cli_spellings: ClassVar[Mapping[str, str]] = {"draft_id": "--draft"}
    budget: Budget = Budget()
    head_to_head: HeadToHeadRequest | None = Field(
        default=None,
        description="Start the draft as these arms of one head-to-head, its origin every arm's, "
        "in place of one campaign. Its own `budget` binds each arm, so `budget` is refused "
        "beside it.",
    )

    @model_validator(mode="after")
    def _arms_run_their_own_budget(self) -> StartCheckinPayload:
        if self.head_to_head is not None and self.budget.named:
            raise ValueError("a head-to-head's arms run its budget")
        if self.head_to_head is not None and self.lookahead is not None:
            raise ValueError("a head-to-head's arms start one at a time; press each arm's own")
        return self


# The CommandRecord carries what was sent: a default there records a knob nobody set.
StatedCampaign = Annotated[
    CampaignLayer,
    PlainSerializer(
        lambda stated: stated.model_dump(mode="json", exclude_unset=True),
        return_type=dict[str, JsonValue],
    ),
]
_STATED = (
    "A campaign declaration standing in for the dataset's own `dataset.yaml::campaign`, laid "
    "over the defaults"
)


class MintHeadToHeadPayload(CommandPayload, kind=CommandKind.HEAD_TO_HEAD):
    """Mint arms of one head-to-head on a dataset in one act and launch each — every arm planned,
    priced and admitted before any is minted, so a refused arm leaves none behind."""

    dataset_name: str = Field(min_length=1, max_length=64)
    head_to_head: HeadToHeadRequest
    campaign_config: StatedCampaign | None = Field(
        default=None, description=f"{_STATED}, every arm's"
    )
    cli_spellings: ClassVar[Mapping[str, str]] = {
        "dataset_name": "dataset",
        "head_to_head.head_to_head_id": "--head-to-head",
        "head_to_head.arms": "--arm",
    }

    @model_validator(mode="after")
    def _name_is_a_dataset_name(self) -> MintHeadToHeadPayload:
        validate_dataset_name(self.dataset_name)
        return self


class MintCampaignPayload(LaunchPayload, kind=CommandKind.MINT_CAMPAIGN):
    """Mint a campaign on an authored dataset and launch its root cycle."""

    cli_spellings: ClassVar[Mapping[str, str]] = {"dataset_name": "dataset"}
    dataset_name: str = Field(min_length=1, max_length=64)
    budget: Budget = Budget()
    step_rounds: StepRounds = None
    optimization: OptimizationOverrides | None = Field(
        default=None,
        description="Laid over the dataset's own `optimization` before the mint freezes it; only "
        "the fields sent move anything",
    )
    campaign_config: StatedCampaign | None = Field(
        default=None, description=f"{_STATED}; `optimization` still lays over it"
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
        return data

    @model_validator(mode="after")
    def _name_is_a_dataset_name(self) -> MintCampaignPayload:
        validate_dataset_name(self.dataset_name)
        return self


class IngestUploadPayload(CommandPayload, kind=CommandKind.INGEST_UPLOAD):
    """Parse an uploaded tabular file into a durable check-in draft; ``start-checkin`` mints its campaign."""

    # A JSON door (a host's tool call) sends the bytes base64; a form or a terminal hands them raw.
    model_config = ConfigDict(val_json_bytes="base64", ser_json_bytes="base64")

    filename: str = Field(default="", max_length=256)
    slug: DatasetName | None = Field(
        default=None, max_length=64, description="Optional slug override."
    )
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

    # A switch: its absence is off.
    cli_spellings: ClassVar[Mapping[str, str]] = {"demo_mode_enabled": "--demo-mode-enabled"}
    demo_mode_enabled: bool


class AcceptTermsPayload(CommandPayload, kind=CommandKind.ACCEPT_TERMS):
    """Record the caller's acceptance of the Terms, server-stamped; a version other than the live one is refused."""

    version: str = Field(min_length=1, max_length=64)


class PostBulletinPayload(CommandPayload, kind=CommandKind.POST_BULLETIN):
    """Post a bulletin to every account on this box; only its host may."""

    text: str = Field(min_length=1, max_length=BULLETIN_TEXT_CHARS)
    pops_up: bool = Field(
        default=False,
        description="Open it at sign-in and on each page load until the account dismisses it.",
    )


class WithdrawBulletinPayload(CommandPayload, kind=CommandKind.WITHDRAW_BULLETIN):
    """Take a bulletin back from every account on this box; only its host may."""

    bulletin_id: str = Field(min_length=1, max_length=64)


class DismissBulletinPayload(CommandPayload, kind=CommandKind.DISMISS_BULLETIN):
    """Dismiss one bulletin for the caller's account: it pops up no more, and the list marks it."""

    bulletin_id: str = Field(min_length=1, max_length=64)


class BlockEmailPayload(CommandPayload, kind=CommandKind.BLOCK_EMAIL):
    """Refuse every sign-in of an email on this box; only its host may."""

    email: str = Field(min_length=1, max_length=320)


class UnblockEmailPayload(CommandPayload, kind=CommandKind.UNBLOCK_EMAIL):
    """Lift an email's block on this box; only its host may."""

    email: str = Field(min_length=1, max_length=320)


class GrantDelegationPayload(CommandPayload, kind=CommandKind.GRANT_DELEGATION):
    """Delegate capabilities, named by a comma-separated list, from the host's account to another; only its host may."""

    user_id: str = Field(min_length=1, max_length=128)
    capabilities: str = Field(min_length=1, max_length=512)


class RevokeDelegationPayload(CommandPayload, kind=CommandKind.REVOKE_DELEGATION):
    """Take back an account's delegation; only the host may."""

    user_id: str = Field(min_length=1, max_length=128)


class SetCouponPayload(CommandPayload, kind=CommandKind.SET_COUPON):
    """Set an account's coupon face: dollars and tokens, `unbounded` leaving a unit unbounded; `afresh` starts its draw now. Only the host may."""

    cli_spellings: ClassVar[Mapping[str, str]] = {"afresh": "--afresh"}
    user_id: str = Field(min_length=1, max_length=128)
    budget: Budget
    afresh: bool = False


class DeleteAccountPayload(CommandPayload, kind=CommandKind.DELETE_ACCOUNT):
    """Delete an account: its tree, its tokens, grants, sign-ins and bulletin dismissals; refused while it runs anything. Only the host may."""

    user_id: str = Field(min_length=1, max_length=128)


class SetApiKeyPayload(CommandPayload, kind=CommandKind.SET_API_KEY):
    """Hold the caller's own key for a provider: its sends there go out on it, past the coupon.
    Never served back, and recorded masked."""

    unmatched: ClassVar[bool] = True

    provider: str = Field(min_length=1, max_length=64)
    secret: SecretStr


class RemoveApiKeyPayload(CommandPayload, kind=CommandKind.REMOVE_API_KEY):
    """Drop the caller's key for a provider; its next send there draws the coupon again."""

    provider: str = Field(min_length=1, max_length=64)


class MintApiTokenPayload(CommandPayload, kind=CommandKind.MINT_API_TOKEN):
    """Mint a named bearer token of the caller's account, scoped to what it may command; its text
    is served by this answer alone."""

    # Its answer holds the one copy of the text, so a retry mints again rather than replay it.
    unmatched: ClassVar[bool] = True

    name: str = Field(min_length=1, max_length=64)
    scopes: list[str] = Field(
        min_length=1,
        max_length=16,
        description="The `/grant` names it may command; reads need none.",
    )


class ListApiTokensPayload(CommandPayload, kind=CommandKind.LIST_API_TOKENS):
    """List the caller's tokens, live and revoked, never their text."""


class RevokeApiTokenPayload(CommandPayload, kind=CommandKind.REVOKE_API_TOKEN):
    """Revoke one of the caller's tokens: its next request is refused, and its open streams end."""

    token_id: str = Field(min_length=1, max_length=64)


# The last payload model is declared above, so a kind carrying none fails right here.
if _unpaired := sorted(set(CommandKind) - PAYLOAD_MODEL_FOR_KIND.keys()):
    raise RuntimeError(f"command kinds carried by no payload model: {_unpaired}")
# A tool's description is its model's docstring: it names kinds and fields, never a path or a verb.
_TICKED = re.compile(r"`+([^`]+)`+")
_VERBS = frozenset(k.cli_verb for k in CommandKind if k.cli_verb)
if _worded := sorted(
    f"{k}: `{ticked}`"
    for k in CommandKind
    if k.host
    for ticked in _TICKED.findall(vars(PAYLOAD_MODEL_FOR_KIND[k]).get("__doc__") or "")
    if ".py::" in ticked or ticked.split()[0] in _VERBS
):
    raise RuntimeError(f"tool descriptions naming a path or a terminal verb: {_worded}")


def _fills(model: object, dotted: str) -> bool:
    for name in dotted.split("."):
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            return False
        if (held := model.model_fields.get(name)) is None:
            return False
        model = held.annotation
    return True


def takes_act(act: NextAct) -> bool:
    """A kind every door presses, filling a field its payload has with what it sends, as its model
    validates the wire; or, kind-less, a field set or a file read, never both and never neither; a
    stop's checked below, a refusal's at the gate."""
    if not act.kind:
        return bool(act.field) != bool(act.reads)
    model = PAYLOAD_MODEL_FOR_KIND.get(act.kind)
    if model is None or act.reads or (act.field and not _fills(model, act.field)):
        return False
    try:
        for head, sent in act.payload.items():
            TypeAdapter(model.model_fields[head].annotation).validate_json(json.dumps(sent))
    except ValidationError:
        return False
    return True


if _unacted := sorted(
    f"{reason}: {act.kind} {act.field} {act.reads}"
    for reason, info in STOP_REASON_INFO.items()
    for act in info.next_acts
    if not takes_act(act)
):
    raise RuntimeError(f"STOP_REASON_INFO next acts no kind takes: {_unacted}")
# A secret no digest names would match two keys as one act.
if _secretive := sorted(
    k
    for k, m in PAYLOAD_MODEL_FOR_KIND.items()
    if not m.unmatched
    and any(SecretStr in (f.annotation, *get_args(f.annotation)) for f in m.model_fields.values())
):
    raise RuntimeError(f"payloads holding a secret yet matched on a retry: {_secretive}")
# A payload keeping an undeclared key carries a steer no handler owns, an endpoint among them.
if _lax := sorted(
    k for k, m in PAYLOAD_MODEL_FOR_KIND.items() if m.model_config["extra"] != "forbid"
):
    raise RuntimeError(f"command payloads accepting undeclared keys: {_lax}")


class CommandAcceptedBody(StrictModel):
    """What a command whose applied form answers nothing returns: the record it appended."""

    command_id: str = Field(description="Stable id of the appended `CommandRecord`.")
    subject: str = Field(
        description="The address the dispatcher resolved and filed it on: empty for the "
        "workspace, `draft:<id>` for a draft, else its campaign or cycle as a subject spells it "
        "(`campaign:<id>`, `cycle:<id>/<cycle>`, `;in=` its sandbox chain)."
    )


class ReadKind(StrEnum):
    """The read catalogue beside `CommandKind`: a row's wire inputs and answer are its `ReadParams` model."""

    ME = "me"
    BULLETINS = "bulletins"
    USER_SETTINGS = "user-settings"
    QUOTA_STATUS = "quota-status"
    COUPON = "coupon"
    ACTIVITY = "activity"
    ACTIVE_SESSION = "active-session"
    MACHINE_STATUS = "machine-status"
    COMMAND = "command"
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
    CYCLE_FORK_PREVIEW = "cycle-fork-preview"
    CONFIG_MAP = "config-map"
    CAMPAIGN_STORAGE = "campaign-storage"
    WORKSPACE_STORAGE = "workspace-storage"
    ARCHIVE_DATASETS = "archive-datasets"
    DATASETS = "datasets"
    DATASET_PIPELINE = "dataset-pipeline"
    DATASET_CELLS = "dataset-cells"
    ANSWER = "answer"
    ORIGINS = "origins"
    FEED = "feed"
