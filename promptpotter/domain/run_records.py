from __future__ import annotations

import enum
import os
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, NamedTuple, Self

from pydantic import ConfigDict, Field, RootModel, TypeAdapter, field_validator, model_validator

from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.arms import ArmState, Proposal, Proposed
from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.bench import PartitionRecord
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.dashboard_rows import RunLimits
from promptpotter.domain.l4.proxies import PanelPrecision, panel_precision
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.phase_views import PhaseView, RunSpendView, ViewAnchors
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    ErrorRecord,
    GateDecision,
    GateKind,
    LaunchStage,
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.pipeline_schema import (
    CapabilityMenu,
    ManifestNodeOverlay,
    NodeSearchNarrowing,
)
from promptpotter.domain.results import Election, RoundCells, RoundOutcome, RoundResult
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import LedgerCell, WalkedCell
from promptpotter.domain.spend import (
    Budget,
    CeilingMeter,
    KeySource,
    TokenUsageKind,
    UserSpend,
    bill_or_rate_usd,
    cycle_axes_only,
)
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.domain.treatment import Treatment
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.identity import Principal
from promptpotter.shared.measurement_context import ARM_ROLES, MeasurementRole


class CheckpointKind(enum.StrEnum):
    """Subclassed by the bench's enum and each optimizer's, declared in its own package."""


class BenchCheckpointKind(CheckpointKind):
    FORK_CUT = "fork_cut"
    PANEL_COVERAGE = "panel_coverage"


class ResumeCheckpointRecord(FrozenModel):
    """``inputs_ref`` + ``outcome`` drive divergence; ``data`` is archival."""

    record_type: Literal["decision"] = "decision"
    # A `CheckpointKind` value or a plugin's string; replayed where `replayers.py::replayers` has it.
    kind: str
    # The manifest node whose member took the decision; ``None`` for the bench's own.
    node: str | None = None
    inputs_ref: dict[str, Any] = Field(default_factory=dict)
    outcome: Any = None
    data: dict[str, Any] = Field(default_factory=dict)
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class PhaseRecord(FrozenModel):
    record_type: Literal["phase"] = "phase"
    phase: str
    event: str
    round: int | None = None
    view: PhaseView | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class RunPhaseRecord(FrozenModel):
    """The runner's own declaration: every other phase is derived off the last one, never said."""

    record_type: Literal["run_phase"] = "run_phase"
    run_phase: RunPhase
    stop_reason: StopReason | None = None
    cause: PauseCause | None = None
    detail: str = ""
    spend: RunSpendView | None = None
    gate: GateKind | None = None
    # What an ``arms`` hold offers to keep, in the order `submit-arms --keep` indexes.
    drafts: list[Proposal] = Field(default_factory=list)
    timestamp: str = Field(default_factory=utcnow_iso)

    @model_validator(mode="after")
    def _paused_says_why(self) -> RunPhaseRecord:
        if self.run_phase is RunPhase.PAUSED and (self.stop_reason is None or self.cause is None):
            raise ValueError("a paused declaration names its stop reason and its cause")
        return self

    @model_validator(mode="after")
    def _gate_says_which(self) -> RunPhaseRecord:
        if (self.run_phase is RunPhase.GATE) != (self.gate is not None):
            raise ValueError("a gate declaration, and only one, names what it holds for")
        if self.drafts and self.gate != "arms":
            raise ValueError("only a hold for a round's arms offers drafts")
        return self

    @classmethod
    def stop(
        cls,
        stop_reason: StopReason,
        *,
        cause: PauseCause | None = None,
        detail: str = "",
        spend: RunSpendView | None = None,
    ) -> RunPhaseRecord:
        if STOP_REASON_INFO[stop_reason].outcome is not StopOutcome.PAUSED:
            return cls(run_phase=RunPhase.TERMINAL, stop_reason=stop_reason, spend=spend)
        if cause is None:
            raise ValueError(f"a {stop_reason.value} stop pauses the cycle and names no cause")
        return cls(
            run_phase=RunPhase.PAUSED,
            stop_reason=stop_reason,
            cause=cause,
            detail=detail,
            spend=spend,
        )


class BackendWarningRecord(FrozenModel):
    """An attempt that failed and will be retried: a transport fault or a 5xx, never a 429."""

    record_type: Literal["backend_warning"] = "backend_warning"
    kind: str
    attempt: int
    max_attempts: int
    wait_s: float
    error_class: str | None = None
    status_code: int | None = None
    final: bool = False
    query: str = ""
    detail: str = ""
    timestamp: str = Field(default_factory=utcnow_iso)


class ProviderHoldRecord(FrozenModel):
    """A 429 episode's open or strike, heard by a ledger from its own sends."""

    record_type: Literal["provider_hold"] = "provider_hold"
    sender: str
    scope: str
    strikes: int
    # Epoch: a throttle past it stops the run instead of holding it.
    gives_up_at: float
    reading: BackpressureReading
    timestamp: str = Field(default_factory=utcnow_iso)


class ProviderEasedRecord(FrozenModel):
    """The provider answers the sender again: told at once to every ledger that heard the hold."""

    record_type: Literal["provider_eased"] = "provider_eased"
    sender: str
    held_s: float
    timestamp: str = Field(default_factory=utcnow_iso)


class RoundEnteredRecord(FrozenModel):
    """Entering N displaces N and every later round; ``rewound`` discards their proposals too."""

    record_type: Literal["round_entered"] = "round_entered"
    round: int
    rewound: bool = False
    timestamp: str = Field(default_factory=utcnow_iso)


class RoundStatement(RoundOutcome):
    """A closed round as a ledger states it: the last statement per round stands and IS the round."""

    optimizer_state: OptimizerState
    cells: RoundCells
    # Each walked cell's position in the bank the cycle drew: a cell carries its key alone.
    positions: dict[str, int]
    # Every standing round re-read on this close's one scale: the series moves with the clock.
    level_series: LevelSeries
    run_standing: RunStanding
    # Leading arm against the ORIGIN's rows: a pairing across two rounds of the closing line.
    panel_precision: PanelPrecision | None
    anchors: ViewAnchors
    timestamp: str = Field(default_factory=utcnow_iso)

    @classmethod
    def of(
        cls,
        line: Sequence[RoundResult],
        slot_of: Mapping[str, int],
        level_series: LevelSeries,
        run_standing: RunStanding,
        anchors: ViewAnchors,
        **own: Any,
    ) -> Self:
        rr = line[-1]
        cells = rr.addresses()
        leading = None if rr.election is None or not rr.round else rr.election.leading
        return cls(
            **{name: getattr(rr, name) for name in RoundOutcome.model_fields},
            cells=cells,
            positions={key: slot_of[key] for key in cells.sample_keys()},
            level_series=level_series,
            run_standing=run_standing,
            panel_precision=None
            if leading is None
            else panel_precision(rr.rows(leading).cells, line[0].results.cells),
            anchors=anchors,
            **own,
        )

    def restated(self, optimizer_state: OptimizerState) -> RoundRestatedRecord:
        """This round holding *optimizer_state*: what a member banks once the round has closed."""
        body = {name: getattr(self, name) for name in RoundStatement.model_fields}
        del body["timestamp"]
        return RoundRestatedRecord(**body | {"optimizer_state": optimizer_state})


class RoundClosedRecord(RoundStatement):
    """The run's clock: a close is its line's latest round, so its standing is the run's."""

    record_type: Literal["round_closed"] = "round_closed"


class RoundRestatedRecord(RoundStatement):
    """A closed round stated again, never a close: it moves no clock and announces nothing. It is
    the round WHOLE, so a repair's restatement makes its ledger the owner of every arm in it."""

    record_type: Literal["round_restated"] = "round_restated"


class RoundProposedRecord(FrozenModel):
    """Banked by the bench once per round; replayed on a resume unless a digest the round ``consumed`` has moved.

    ``submitted`` is the operator's answer at the arm gate: the arms the round measures."""

    record_type: Literal["round_proposed"] = "round_proposed"
    round: int
    consumed: str
    proposals: list[Proposal]
    submitted: bool = False
    timestamp: str = Field(default_factory=utcnow_iso)


class ArmRecord(FrozenModel):
    """One arm's transition, LAST wins: proposed, walking, then cut or measured."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    record_type: Literal["arm"] = "arm"
    round: int
    arm: ArmKey
    state: ArmState
    anchors: ViewAnchors = Field(default_factory=ViewAnchors)
    timestamp: str = Field(default_factory=utcnow_iso)


class _Pass(FrozenModel):
    """One cell of a pass: an ARM of the round where `role` is one of `ARM_ROLES`."""

    round: int
    individual_id: str
    role: MeasurementRole
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def arm(self) -> ArmKey | None:
        """The arm whose walk this cell continues; ``None`` for a pass no arm walks (a bench pass)."""
        return ArmKey(self.individual_id) if self.role in ARM_ROLES else None


class SampleStartedRecord(_Pass):
    record_type: Literal["sample_started"] = "sample_started"
    sample_idx: int
    sample_total: int
    position: int
    # Capped at the writer: the whole query is a dataset fact, in every measurement row.
    query_preview: str = ""
    sample_lookahead: int = 1


class SampleScoredRecord(_Pass):
    """A re-banked row has no ``sample_idx``."""

    record_type: Literal["sample_scored"] = "sample_scored"
    sample_idx: int | None = None
    sample_total: int | None = None
    # The row's slot in the bank this cycle drew, as `SampleStartedRecord` names it: the cell carries none.
    position: int
    result: LedgerCell
    running: ScoreSummary | None = None


class _Announced(NamedTuple):
    state: ArmState
    running: ScoreSummary | None


class AnnouncedArms:
    """Each announced arm's last state and running fold by ``(round, key)``; a re-read continues it."""

    def __init__(self) -> None:
        self._arms: dict[tuple[int, ArmKey], _Announced] = {}

    def __iter__(self) -> Iterator[tuple[int, ArmKey]]:
        return iter(self._arms)

    def announce(self, record: ArmRecord) -> None:
        held = self._arms.get((record.round, record.arm))
        running = None if held is None else held.running
        self._arms[record.round, record.arm] = _Announced(record.state, running)

    def take(self, record: SampleScoredRecord) -> ArmKey | None:
        """The arm *record*'s cell continues; ``None`` for a pass no arm walks."""
        if (arm := record.arm) is None:
            return None
        if (held := self._arms.get((record.round, arm))) is None:
            raise LookupError(
                f"a round {record.round} cell names arm {arm}, which its own cycle file never "
                "announced: the SSE tail and the ray fold one file, so each file announces its arms"
            )
        if record.running is not None:
            self._arms[record.round, arm] = held._replace(running=record.running)
        return arm

    def close(self, record: RoundStatement, keys: Iterable[ArmKey]) -> None:
        """The statement is each arm's last record: its fold covers the reference reads too."""
        for key in keys:
            self._arms[record.round, key] = _Announced(record.arms[key], record.folds.get(key))

    def enter(self, record: RoundEnteredRecord) -> None:
        self._arms = {at: held for at, held in self._arms.items() if at[0] < record.round}

    def state(self, round_num: int, key: ArmKey) -> ArmState:
        return self._arms[round_num, key].state

    def fold(self, round_num: int, key: ArmKey) -> ScoreSummary | None:
        held = self._arms[round_num, key]
        return None if isinstance(held.state, Proposed) else held.running


class FlightWaiting(FrozenModel):
    position: int
    since: float


class FlightRecord(FrozenModel):
    """The whole round's calls in flight, so it names no candidate."""

    record_type: Literal["flight"] = "flight"
    round: int
    out: int
    allowed: int
    most: int
    affordable: int | None = None
    cell_usd: float | None = None
    waiting: FlightWaiting | None = None
    backpressure: BackpressureReading | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class RaceStandingRecord(FrozenModel):
    """Archive-only, not divergence-gated; ``p_best`` is about the arm ``current`` alone."""

    record_type: Literal["race_standing"] = "race_standing"
    round: int
    member: str
    current: ArmKey
    n_samples: int
    p_best: float
    paired_breakdown: dict[ArmKey, dict[str, float]]
    decision_grade: bool
    timestamp: str = Field(default_factory=utcnow_iso)


class RaceCatchUpRecord(FrozenModel):
    """The catch-up walks one just-measured cell of ``arm`` took: no pass records their cells."""

    record_type: Literal["race_catch_up"] = "race_catch_up"
    round: int
    arm: ArmKey
    member: str
    # The cell's ``Sample.key``: every address below names it.
    sample_key: str
    # Its slot in the bank this cycle drew, as a readout names it.
    position: int
    # By prior.
    cells: dict[ArmKey, WalkedCell] = Field(min_length=1)
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def prior_keys(self) -> list[ArmKey]:
        return list(self.cells)


class TokenUsageRecord(FrozenModel):
    """ONE answered send, or a replay (``cached``); a send that reported no usage writes none."""

    record_type: Literal["token_usage"] = "token_usage"
    kind: TokenUsageKind
    node: str
    model: str | None = None
    # Who BILLED: a rate belongs to the (provider, model) pair; ``None`` ⇒ no rate prices the call.
    provider: str | None = None
    # WHICH upstream host answered, where `provider` is a gateway; ``None`` where it is the host.
    served_by: str | None = None
    input_tokens: int
    output_tokens: int
    # A SUBSET of ``output_tokens``; 0 also means no breakdown was reported, never "did not think".
    reasoning_tokens: int = 0
    # A SUBSET of ``input_tokens`` the PROVIDER cached; 0 also means no breakdown. Not ``cached``.
    cache_read_tokens: int = 0
    # The part of ``input_tokens`` billed at a premium to POPULATE that cache.
    cache_write_tokens: int = 0
    duration_s: float = 0.0
    # The BILL the provider reported, the only figure called spent; never filled from a rate.
    cost_usd: float | None = None
    # OUR rate's price of an unbilled call, stamped once: it counts against ceilings, never spent.
    rate_priced_usd: float | None = None
    # Whose wallet paid: the one fact a ledger's path cannot name, since the path names the account.
    # ``None`` exactly on a replay (``cached``), which no wallet paid.
    key_source: KeySource | None
    # The `SpendHoldRecord` this call settles; ``None`` for a call nothing held (a replay).
    hold_id: str | None = None
    # A nested run's own view of its call; money is summed off the copy on the outer ledger.
    mirrored: bool = False
    cached: bool = False
    round: int | None = None
    # ``None`` outside a candidate's pass; a nested run's copy keeps ITS pass: `by_role` skips it.
    role: MeasurementRole | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def bill_or_rate_usd(self) -> float | None:
        return bill_or_rate_usd(self.cost_usd, self.rate_priced_usd)

    @property
    def spend_round(self) -> int:
        """A call carrying no round ran before any closed (init, the origin score): banks at 0."""
        return 0 if self.round is None else self.round


class SpendHoldRecord(FrozenModel):
    """Written BEFORE the send, at its bound; unclosed, it binds every ceiling and is not spent."""

    record_type: Literal["spend_hold"] = "spend_hold"
    hold_id: str
    kind: TokenUsageKind
    node: str
    model: str | None = None
    provider: str | None = None
    input_tokens: int
    # ``None`` where nothing capped the reply: such a send holds no tokens.
    output_tokens: int | None
    cost_usd: float | None = None
    # Whose wallet an unclosed hold may have drawn: its `TokenUsageRecord` never came to say.
    key_source: KeySource
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class PricedKeyRecord(FrozenModel):
    """A key the search has PRICED, so a later read of it, resumed or not, prices nothing again."""

    record_type: Literal["priced_key"] = "priced_key"
    priced_key: str
    timestamp: str = Field(default_factory=utcnow_iso)


class BankedSubject(NamedTuple):
    """What a tombstone banks: a campaign, one of its cycles, or a draft that never minted one."""

    campaign_id: str = ""
    cycle_id: str = ""
    draft_id: str = ""


def _one_subject(subject: BankedSubject) -> None:
    if bool(subject.campaign_id) is bool(subject.draft_id):
        raise ValueError("a tombstone banks a campaign (or its cycle) or a draft, exactly one")
    if subject.cycle_id and not subject.campaign_id:
        raise ValueError("a banked cycle names its campaign")


class BankedSpend(FrozenModel):
    """One wallet's `UserSpend`, as a tombstone banks it."""

    used_usd: float
    # What our rate table priced the calls no provider billed (`TokenUsageRecord.rate_priced_usd`).
    rate_priced_usd: float
    used_tokens: int
    unpriced_tokens: int
    # What its unreported sends may have cost (`SpendHoldRecord`) — banked apart, as it is read.
    unreported_usd: float
    unreported_tokens: int

    @classmethod
    def of(cls, spent: UserSpend) -> BankedSpend:
        return cls(**spent._asdict())

    @property
    def spend(self) -> UserSpend:
        return UserSpend(**self.model_dump())


class SpendTombstoneRecord(FrozenModel):
    """On the WORKSPACE ledger, so deleting what spent it cannot re-earn a spend ceiling."""

    record_type: Literal["spend_tombstone"] = "spend_tombstone"
    campaign_id: str = ""
    # Empty for a whole-campaign bank.
    cycle_id: str = ""
    draft_id: str = ""
    # By whose key paid it, so a deletion keeps the coupon's count as it keeps the account's.
    spent: dict[KeySource, BankedSpend]
    # What the campaign's ceiling counted of it in each meter, so a bank needs no manifest to name one.
    metered: dict[CeilingMeter, Budget]
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def subject(self) -> BankedSubject:
        return BankedSubject(self.campaign_id, self.cycle_id, self.draft_id)

    @model_validator(mode="after")
    def _one(self) -> SpendTombstoneRecord:
        _one_subject(self.subject)
        return self


class BankedCommand(FrozenModel):
    """A cycle's trail line — a command, or the run's abnormal stop — banked before the cycle went."""

    at: str
    kind: str
    # The campaign and cycle it addressed, as the trail read it before they went.
    subject: str
    user_id: str


class TrailTombstoneRecord(FrozenModel):
    """On the WORKSPACE ledger, so deleting what was commanded cannot delete that it was."""

    record_type: Literal["trail_tombstone"] = "trail_tombstone"
    campaign_id: str = ""
    # Empty for a whole-campaign bank.
    cycle_id: str = ""
    draft_id: str = ""
    commands: tuple[BankedCommand, ...]
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def subject(self) -> BankedSubject:
        return BankedSubject(self.campaign_id, self.cycle_id, self.draft_id)

    @model_validator(mode="after")
    def _one(self) -> TrailTombstoneRecord:
        _one_subject(self.subject)
        return self


class LLMCallStartRecord(FrozenModel):
    """Pairs with :class:`LLMCallRecord` via ``call_id``, so a long call does not look frozen."""

    record_type: Literal["llm_call_start"] = "llm_call_start"
    call_id: str
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    model: str | None = None
    started_at_ms: int
    prompt_chars: int = 0
    # Injection name → rendered chars. Empty for a node composing no layout and on a cache replay.
    injection_chars: dict[str, int] = Field(default_factory=dict)
    # What the node's ceiling REFUSED, per panel; `injection_chars` reports only what SURVIVED.
    injection_dropped: dict[str, int] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def refused_panels(self) -> list[str]:
        """Dropped WHOLE, nothing surviving in ``injection_chars``; a THINNED panel is not one."""
        return sorted(n for n in self.injection_dropped if n not in self.injection_chars)


class LLMCallProgressRecord(FrozenModel):
    """Heartbeat while the provider call is blocked; cache replays skip it."""

    record_type: Literal["llm_call_progress"] = "llm_call_progress"
    call_id: str
    node: str
    round: int | None = None
    elapsed_s: float
    detail: str | None = None
    # The cycle the waited-on cell runs (`Connector.cell_hop`), in THIS cycle's sandbox: the two
    # hops are the `CyclePath` the drill-in opens. ``None`` on an ordinary call.
    inner: CycleHop | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class LLMCallRecord(FrozenModel):
    """``payload_kind='synthesized'`` is a replay: its messages, response and usage are absent."""

    record_type: Literal["llm_call"] = "llm_call"
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    payload_kind: Literal["llm_call", "synthesized"] = "llm_call"
    call_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


AccountEvent = Literal[
    "account_created", "signed_in", "terms_accepted", "uploaded", "request_refused"
]

HealthState = Literal["held", "broken", "unverified"]


class HealthRecord(FrozenModel):
    """On the box's own WORKSPACE ledger: a probe's state CHANGED. ``unverified`` never is one."""

    record_type: Literal["health"] = "health"
    probe: str
    state: Literal["held", "broken"]
    said: str
    timestamp: str = Field(default_factory=utcnow_iso)


class Device(enum.StrEnum):
    PHONE = "phone"
    TABLET = "tablet"
    COMPUTER = "computer"


_TABLET_UA = re.compile(r"iPad|Tablet")
_PHONE_UA = re.compile(r"Mobi|Android|iPhone")


def device_of(mobile_hint: str | None, user_agent: str | None) -> Device | None:
    """The site's visit-log classes; ``None`` where the request sent no user-agent to read."""
    if mobile_hint == "?1":
        return Device.PHONE
    if not user_agent:
        return None
    if _TABLET_UA.search(user_agent):
        return Device.TABLET
    return Device.PHONE if _PHONE_UA.search(user_agent) else Device.COMPUTER


class AccountEventRecord(FrozenModel):
    """On the WORKSPACE ledger; ``user_id`` is the acting principal, a delegate's own id in its delegator's tenant."""

    record_type: Literal["account_event"] = "account_event"
    event: AccountEvent
    user_id: str
    # The provider of a sign-in, the version of a terms acceptance, the file an upload was kept as,
    # what a refused request asked and was told.
    subject: str = ""
    # What a sign-in or an opening came from; unset where no request carried one.
    device: Device | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


CommandAckStatus = Literal["accepted", "applied", "rejected"]


class CommandRecord(FrozenModel):
    """``CommandDispatcher`` alone writes it, to the cycle's, its root's or the workspace ledger."""

    record_type: Literal["command"] = "command"
    command_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    issued_by: Principal
    timestamp: str = Field(default_factory=utcnow_iso)


class NoEffect(FrozenModel):
    """An ack whose command names no effect beyond having happened."""


class PausedEffect(FrozenModel):
    """``pause-cycle``, taken by the loop."""

    run_phase: Literal[RunPhase.PAUSED]


class SkippedEffect(FrozenModel):
    """``skip-sample``, taken by the loop."""

    skipped: int


class GateDecidedEffect(FrozenModel):
    """``origin-gate-decision``, taken by the loop."""

    decision: GateDecision


class ArmsTakenEffect(FrozenModel):
    """``submit-arms``: how many arms the round banked."""

    arms: int


class LookaheadTakenEffect(FrozenModel):
    """``set-sample-lookahead``, spent by the round that took it."""

    cells: int
    auto: bool


class ArmedCeilingEffect(FrozenModel):
    """``change-run-limits``: the ceiling a budget stop fires at once the clamped change stands."""

    armed: Budget


class OriginEditedEffect(RootModel[dict[str, dict[Literal["from", "to"], Any]]]):
    """A check-in draft edit: each origin field it moved, by key, ``from`` its old value ``to`` its new."""

    model_config = ConfigDict(frozen=True)
    root: dict[str, dict[Literal["from", "to"], Any]] = Field(min_length=1)


# Told apart by their fields, so an ack written before any tag still reads as its kind.
CommandEffect = (
    NoEffect
    | PausedEffect
    | SkippedEffect
    | GateDecidedEffect
    | ArmsTakenEffect
    | LookaheadTakenEffect
    | ArmedCeilingEffect
    | OriginEditedEffect
)


class CommandAckRecord(FrozenModel):
    """``applied`` = it HAPPENED; a loop command is ``accepted`` first, and stays so if untaken."""

    record_type: Literal["command_ack"] = "command_ack"
    command_id: str
    status: CommandAckStatus
    detail: str = ""
    effect: CommandEffect = Field(default_factory=NoEffect)
    timestamp: str = Field(default_factory=utcnow_iso)


RoundWarningKind = Literal[
    "l1_zero_candidates",
    "injection_budget_overrun",
    "layer_parse_failure",
    "layer_terminated_cycle",
    "send_refused",
    # A `terminate_proposal` carrying no reason: ignored, never silently.
    "layer_terminate_blank",
    # A `fork_proposal` dropped: no ancestor holds a level (naming what withholds θ), or the
    # launch's rebase clock is spent.
    "layer_rebase_dropped",
    # An adapter landed no round output, and the re-runs before the next walk failed: the run halts.
    "adapter_output_unavailable",
    # A critique cluster spanning fewer marked rows than a cluster is: dropped before the generator reads it.
    "critique_cluster_dropped",
    # Nothing failed: the round promoted an arm the origin panel does not back (`runner/round.py`).
    "round_not_advanced",
    # A declared child no node could make: `mating`'s pool under two, a `locus_crossover` pair on
    # under two loci, or an `l1_generate` recombination naming no two pool entries.
    "offspring_dropped",
    # A configured trace sink that failed to open at the run's start: the run traces nothing to it.
    "trace_sink_failed",
]


class RoundWarningRecord(FrozenModel):
    """A SELF-HEAL the run continued past, mid-round or at its start; never re-add a kind with no emitter."""

    record_type: Literal["round_warning"] = "round_warning"
    kind: RoundWarningKind
    # `error` = the round produced nothing usable; `warning` = degraded but progressed.
    severity: Literal["warning", "error"] = "warning"
    message: str
    round: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


class ForkTrigger(enum.StrEnum):
    """One value per caller of :func:`mint_fork`."""

    OPERATOR_DIAG = "operator_diag"
    OPERATOR_REWIND = "operator_rewind"
    OPERATOR_STEERED = "operator_steered"
    OPTIMIZER_REBASE = "optimizer_rebase"
    SCORING_DIVERGENCE = "scoring_divergence"


class ForkDirection(enum.StrEnum):
    """Which side of a cut the run CONTINUES on, the half a cut alone cannot say."""

    # The CHILD is the branch; the parent stays the line it was.
    OFFSHOOT = "offshoot"
    # The CHILD is the continuation; the parent is what was left behind.
    SUPERSEDE = "supersede"
    # BOTH continue identically. MEASURED, so no trigger implies it: it rides `ForkSpec.direction`.
    EQUIVALENT = "equivalent"


# Derived from the trigger, never stored. Exhaustiveness is checked at import below.
FORK_DIRECTION: dict[ForkTrigger, ForkDirection] = {
    ForkTrigger.OPERATOR_DIAG: ForkDirection.OFFSHOOT,
    ForkTrigger.OPERATOR_STEERED: ForkDirection.OFFSHOOT,
    ForkTrigger.OPERATOR_REWIND: ForkDirection.SUPERSEDE,
    ForkTrigger.OPTIMIZER_REBASE: ForkDirection.SUPERSEDE,
    ForkTrigger.SCORING_DIVERGENCE: ForkDirection.SUPERSEDE,
}

_undirected = [t for t in ForkTrigger if t not in FORK_DIRECTION]
if _undirected:
    raise RuntimeError(
        f"ForkTrigger members missing from FORK_DIRECTION: {_undirected}. A cut whose "
        "direction nobody declared renders as an offshoot, which is a lie half the time."
    )
del _undirected


class ConfigOverrides(FrozenModel):
    """A fork's campaign-config delta, in which an absent field inherits the parent's value."""

    # Laid axis by axis over the parent's `optimization.budget`.
    budget: Budget = Budget()
    # Laid key by key onto the parent's `optimization.nodes`; nothing here switches the manifest.
    nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    # Laid key by key over the parent's `CampaignConfig.scoring`; `{"per_cell": F}` is the `score:F` lens.
    scoring: str | dict[str, str] | None = None

    _cycle_axes = field_validator("budget")(cycle_axes_only)


class ForkRemainder(FrozenModel):
    """What a cycle's rounds cap has left, which an offshoot takes as its own. Spend is no
    remainder: the ceiling is the campaign's, and its book already counts every cycle's."""

    rounds_closed: int = Field(
        description="Rounds closed AFTER the origin — what `budget.rounds` counts"
    )
    parent_rounds: int | None = Field(description="The cycle's rounds cap; null where it has none")
    rounds: int | None = Field(description="`parent_rounds` less `rounds_closed`, never under 1")

    @classmethod
    def of(cls, *, rounds_closed: int, rounds: int | None) -> ForkRemainder:
        return cls(
            rounds_closed=rounds_closed,
            parent_rounds=rounds,
            rounds=None if rounds is None else max(1, rounds - rounds_closed),
        )

    def under(self, overrides: ConfigOverrides) -> ConfigOverrides:
        """A rounds axis the overrides NAME wins, ``0`` and a lift included."""
        left = Budget() if self.rounds is None else Budget(rounds=self.rounds)
        return overrides.model_copy(update={"budget": left.over(overrides.budget)})


class OriginSource(enum.StrEnum):
    FORK_SEED = "fork_seed"
    CAMPAIGN_ORIGIN = "campaign_origin"
    # The seed carries no origin: an L2/L3 rebase replays its own C0, which has none to stamp.
    REPLAYED = ""


OPERATOR_ORIGIN_SOURCES = frozenset(OriginSource) - {OriginSource.REPLAYED}


class CycleSeed(FrozenModel):
    """``pipeline_overlay`` merges ON TOP of the dataset overlay for this cycle only."""

    origin_prompts: dict[str, NodePrompt] = Field(
        default_factory=dict,
        description="C0's prompt loci, per prompt node; empty when the seed carries no origin.",
    )
    pipeline_overlay: dict[str, Any] = Field(default_factory=dict)
    optimizer_narrowing: dict[str, NodeSearchNarrowing] = Field(
        default_factory=dict,
        description="Per-fork search-space lock edits (param-key subset + "
        "allowed-values) — overrides the campaign's mint-time narrowing for this "
        "cycle only, the cycle-level peer of the campaign-wide "
        "`CampaignConfig.optimizer_narrowing`. Empty for an unedited fork or a "
        "campaign-from-origin seed.",
    )
    config_overrides: ConfigOverrides = Field(default_factory=ConfigOverrides)
    origin_source: OriginSource = Field(
        default=OriginSource.REPLAYED,
        description=(
            "Which act seeded C0 — 'fork_seed' | 'campaign_origin', naming its lineage's "
            "`changes_description`; empty when the seed carries no origin (an L2/L3 rebase "
            "replays its own)."
        ),
    )

    @model_validator(mode="after")
    def _origin_needs_provenance(self) -> CycleSeed:
        if self.origin_prompts and not self.origin_source:
            raise ValueError("origin_prompts set without an origin_source stamp")
        return self


class WallClock(FrozenModel):
    """CLOCK legs sum to ``elapsed_s``; ``worked_s`` is CALL time, so concurrency pushes it past."""

    # ``None`` where either endpoint is unparseable; every share below is then unanswerable too.
    elapsed_s: float | None
    # By ``CampaignPhase`` value; a bracket that never fired or never closed is ABSENT, never 0.0.
    phase_s: dict[str, float] = Field(default_factory=dict)
    # ``TokenUsageKind`` → node → summed call seconds; cached calls excluded.
    worked_s: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Same keys, in CLOCK: seconds held while no bracket and no gate was open.
    unbracketed_call_s: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Round (JSON key) → seconds to its FIRST close: the last is a ruler restamp or a rewind's re-run.
    round_ended_s: dict[str, float] = Field(default_factory=dict)
    # HUMAN time at a gate, never folded into a machine leg.
    gate_s: float = 0.0
    # ``elapsed_s`` minus every CLOCK leg.
    unattributed_s: float | None = None
    # ``None`` = no fresh cell was measured; 0.0 = the measured cells waited for nothing.
    unworked_s: float | None = None


class CycleFinal(FrozenModel):
    started_at: str
    wall_clock: WallClock
    rounds_to_separable: int | None
    rounds_to_improved: int | None
    rounds_to_ceiling: int | None
    accuracy_ceiling: float | None
    prompt_hashes: dict[str, str]
    # On the origin's OWN samples — never round 1's matched floor, a different sample basis.
    origin_composite_fitness: float | None
    scorer_id: str
    mode: Literal["diag", "full"]
    # The round the optimizer's DECLARED pick was selected in.
    result_round: int
    result_prompt_fields: dict[str, dict[str, str]]
    result_pipeline_params: dict[str, Any] | None


class ElectionRecord(Election):
    """A round's election, the moment it is held: the close carries the same one."""

    record_type: Literal["election"] = "election"
    round: int
    timestamp: str = Field(default_factory=utcnow_iso)


class StandingBudgetRecord(FrozenModel):
    """A cycle's standing budget, LAST wins; read PHYSICALLY, so a fork never inherits one."""

    record_type: Literal["standing_budget"] = "standing_budget"
    # The cycle axes an operator named; the money axes are the campaign's (`Campaign.standing_budget`).
    budget: Budget = Budget()
    # RUN-scoped: the closed-round count at which the launch in flight pauses (``step-cycle``).
    pause_at_round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    _cycle_axes = field_validator("budget")(cycle_axes_only)


class CycleShareRecord(FrozenModel):
    """The cycle's share of its campaign's ceiling, LAST wins and never inherited: its ONE store, which
    the book spending on the cycle and every sibling's admission read. Every move lands through
    `jobs/quota.py::hold_moved`; the account's reserve is the job's (`Job.reserve`)."""

    record_type: Literal["cycle_share"] = "cycle_share"
    # The most the cycle may have metered of its campaign's ceiling, in all.
    allotment: Budget
    timestamp: str = Field(default_factory=utcnow_iso)


class DefaultsPin(FrozenModel):
    """What a declaration is a delta over, in two halves: the bench's defaults, which every arm of
    a head-to-head shares, and its optimizer's whole treatment — manifest, source and prompts —
    which differs by arm by design."""

    bench: str = Field(min_length=1)
    treatment: Treatment


class DefaultsDrift(FrozenModel):
    """Something the run's declaration is a delta over moved since it was declared, so the launch
    runs today's value under the same declaration."""

    declared: DefaultsPin
    running: DefaultsPin

    def moved(self) -> list[str]:
        """``bench`` and each part of the treatment that moved."""
        declared, running = self.declared, self.running
        return [
            *(["bench"] if declared.bench != running.bench else []),
            *(
                part
                for part in Treatment.model_fields
                if getattr(declared.treatment, part) != getattr(running.treatment, part)
            ),
        ]


class RunWiringRecord(FrozenModel):
    """LAST wins, over the whole chain: a fork stands on its parent's until its own lands."""

    record_type: Literal["run_wiring"] = "run_wiring"
    round_cells_ceiling: int
    # DISPLAY only: seeds a surface's metric toggle; the selector decides on its own objective.
    display_metric: DisplayMetric
    elects_on: DisplayMetric
    max_cells_in_flight: int
    measured_unit: MeasuredUnit
    # ``None`` when Langfuse is disabled, and on a mint's record: no launch has opened a trace.
    langfuse_trace_url: str | None
    run_limits: RunLimits
    # `None` on a mint's record: nothing has moved since the declaration was made.
    defaults_drift: DefaultsDrift | None
    timestamp: str = Field(default_factory=utcnow_iso)


class ScoringLockedRecord(FrozenModel):
    """Appended once, as the origin is scored under dials; a config already locked appends none."""

    record_type: Literal["scoring_locked"] = "scoring_locked"
    declared: dict[str, str]
    locked: dict[str, str]
    timestamp: str = Field(default_factory=utcnow_iso)


class CycleSeedRecord(FrozenModel):
    """A fork inherits its parent's seed VIRTUALLY yet appends its own, which one scan returns."""

    record_type: Literal["cycle_seed"] = "cycle_seed"
    seed: CycleSeed
    timestamp: str = Field(default_factory=utcnow_iso)


class ResolvedRunRecord(FrozenModel):
    """What a run init resolved, read over the chain: the declaration LAST wins, the roster FIRST."""

    record_type: Literal["resolved_run"] = "resolved_run"
    # The backend's axes with every overlay laid on; empty where the run resolved none.
    declaration: dict[str, Any]
    # The roster a connector only NAMES can move under its name; the one every round measured stands.
    experiment: dict[str, Any] | None
    # The menu the run's schema answered with, its reasoning floors included; empty is UNKNOWN.
    model_capabilities: CapabilityMenu = Field(default_factory=dict)
    # `None` where the connector checks no revision or the backend reported none.
    backend_revision: str | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class ForkSpec(FrozenModel):
    trigger: ForkTrigger
    reason: str
    issued_by: Principal
    # The parent arm the fork branches from, resolved at the mint (`fork_siblings.py::fork_edge`):
    # the one its caller named, else the pick the parent's line stood on going into the cut round.
    from_candidate: RoundArm
    seed: CycleSeed | None = None
    # The MEASURED direction; `None` ⇒ the trigger implies it (`FORK_DIRECTION`).
    direction: ForkDirection | None = None

    @property
    def resolved_direction(self) -> ForkDirection:
        return self.direction or FORK_DIRECTION[self.trigger]


class SpawnedBy(StrictModel):
    """The outer work-item an L4 inner cycle was spawned to measure. Where `role` is an arm's,
    `cycle_id`, `round` and `individual_id` are that arm's address, which places the run under
    the candidate that asked for it with no search."""

    round: int | None = Field(
        default=None,
        description="Outer round; 0 is the origin (C0). Null when the spawn came from outside any round (the noise-floor diagnostic).",
    )
    individual_id: str | None = Field(
        default=None,
        description="The outer individual's `OptSearchPoint.id` — stable across rounds, and the "
        "outer ARM's key where `role` is an arm's. Null outside a measured pass.",
    )
    role: MeasurementRole | None = Field(
        default=None,
        description="WHY this cell ran. A backfill is spawned outside the round's shared order to fill a paired comparison, and reading one as the individual's own panel cell makes a repaired round unreproducible. Null outside a measured pass.",
    )
    cycle_id: str | None = Field(
        default=None,
        description="The outer cycle whose pass spawned it — the asker, which a fork moves off "
        "the cycle owning the sandbox. Null outside a measured pass.",
    )
    task: str = Field(
        description="The panel cell this run measured — the outer query, e.g. `justlogic/d234/seed-0` (`dataset.yaml::panel.tasks[].id`). The individual does NOT identify a run: every task runs for every individual, so one individual's spawns are as many as the panel has cells and are told apart only by this.",
    )

    @property
    def arm(self) -> RoundArm | None:
        """The outer arm that asked, where an arm's pass did; any other pass names none."""
        if self.role not in ARM_ROLES or self.round is None or self.individual_id is None:
            return None
        return RoundArm(round=self.round, arm=ArmKey(self.individual_id))


class CycleMintedRecord(FrozenModel):
    """One per cycle's own ledger, after only the lines it adopted; a chain is walked off it alone."""

    record_type: Literal["cycle_minted"] = "cycle_minted"
    # The parent's cycle dir relative to this one's, POSIX-separated (`parent_address`): `../<id>`
    # for a sibling in this campaign, a longer path for a cycle in another tree.
    parent: str | None = None
    forked_at_offset: int | None = None
    # The first round the fork runs itself: every round below it is its parent's line's.
    cut_round: int | None = None
    fork: ForkSpec | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @model_validator(mode="after")
    def _fork_is_whole(self) -> CycleMintedRecord:
        parts = (self.parent, self.forked_at_offset, self.cut_round, self.fork)
        if any(p is None for p in parts) and any(p is not None for p in parts):
            raise ValueError(
                "a fork carries its parent, forked_at_offset, cut_round and fork together"
            )
        return self

    def parent_cycle_dir(self, cycle_dir: Path) -> Path | None:
        """The ONE resolution of the parent this record names, from the dir of the cycle it mints."""
        return None if self.parent is None else Path(os.path.normpath(cycle_dir / self.parent))


def parent_address(cycle_dir: Path, parent_dir: Path) -> str:
    """What a mint record names its parent by: a path, so a cycle in another tree is one too."""
    return PurePosixPath(*Path(os.path.relpath(parent_dir, cycle_dir)).parts).as_posix()


def sibling_cycle_id(parent: str | None) -> str | None:
    """The parent's cycle id where it is a sibling in the same campaign; ``None`` for a root or a
    cycle cut from another tree, which no reader of this campaign's cycles can reach."""
    if parent is None:
        return None
    parts = PurePosixPath(parent).parts
    return parts[1] if len(parts) == 2 and parts[0] == ".." else None


class CycleFinalRecord(FrozenModel):
    """Banked only where the cycle's own runner stops it; a later ``running`` retires it."""

    record_type: Literal["cycle_final"] = "cycle_final"
    final: CycleFinal
    # The round the stop left unclosed; ``None`` for a stop at a boundary.
    interrupted_round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class LaunchClockRecord(FrozenModel):
    """Banked by the cycle holding the campaign's line, at every launch end, a pause's included."""

    record_type: Literal["launch_clock"] = "launch_clock"
    started_at: str
    finished_at: str
    # Folded across every cycle of the line; only the runner knows a launch's endpoints.
    clock: WallClock
    timestamp: str = Field(default_factory=utcnow_iso)


class CycleSupersededRecord(FrozenModel):
    """On the LEFT-BEHIND side of a supersede cut; a later ``running`` here takes the line back."""

    record_type: Literal["cycle_superseded"] = "cycle_superseded"
    successor_cycle_id: str
    timestamp: str = Field(default_factory=utcnow_iso)


class ForkGradedRecord(FrozenModel):
    """A cut's MEASURED direction, on the branch: it overrides the one its trigger implies."""

    record_type: Literal["fork_graded"] = "fork_graded"
    direction: ForkDirection
    timestamp: str = Field(default_factory=utcnow_iso)


class InterventionRecord(FrozenModel):
    """One is enough to make the cycle babysat for good; appended the moment it happens."""

    record_type: Literal["intervention"] = "intervention"
    kind: str
    timestamp: str = Field(default_factory=utcnow_iso)


class ComparisonExit(enum.StrEnum):
    """A launch act that runs an arm off the comparison its head-to-head declared."""

    REWIND = "rewind"
    FORK_ON_DIVERGENCE = "fork_on_divergence"
    NO_DIVERGENCE_CHECK = "no_divergence_check"


class LeftComparisonRecord(FrozenModel):
    """Appended on an arm's cycle as the act runs: from it on, the arm's line reads uncontrolled."""

    record_type: Literal["left_comparison"] = "left_comparison"
    act: ComparisonExit
    timestamp: str = Field(default_factory=utcnow_iso)


class LaunchClaimRecord(FrozenModel):
    """Stands only while its process lives: ``claimant_lock`` is the OS lock that process holds."""

    record_type: Literal["launch_claim"] = "launch_claim"
    stage: LaunchStage
    job_id: str
    claimant_lock: str
    timestamp: str = Field(default_factory=utcnow_iso)


class LaunchReleasedRecord(FrozenModel):
    """Ends that launch's :class:`LaunchClaimRecord` with no run, and without ending the cycle."""

    record_type: Literal["launch_released"] = "launch_released"
    job_id: str
    detail: str
    timestamp: str = Field(default_factory=utcnow_iso)


class SpawnedRecord(FrozenModel):
    """Appended at every open, so the last names the work-item the cycle last measured for."""

    record_type: Literal["spawned"] = "spawned"
    spawned_by: SpawnedBy
    timestamp: str = Field(default_factory=utcnow_iso)


CycleRecord = Annotated[
    ResumeCheckpointRecord
    | AccountEventRecord
    | ArmRecord
    | BackendWarningRecord
    | FlightRecord
    | PricedKeyRecord
    | CommandAckRecord
    | CommandRecord
    | CycleFinalRecord
    | CycleMintedRecord
    | CycleSeedRecord
    | CycleShareRecord
    | CycleSupersededRecord
    | ElectionRecord
    | ErrorRecord
    | ForkGradedRecord
    | HealthRecord
    | InterventionRecord
    | LaunchClaimRecord
    | LaunchClockRecord
    | LaunchReleasedRecord
    | LeftComparisonRecord
    | LLMCallProgressRecord
    | LLMCallRecord
    | LLMCallStartRecord
    | PartitionRecord
    | PhaseRecord
    | ProviderEasedRecord
    | ProviderHoldRecord
    | RaceCatchUpRecord
    | RaceStandingRecord
    | ResolvedRunRecord
    | RoundClosedRecord
    | RoundEnteredRecord
    | RoundProposedRecord
    | RoundRestatedRecord
    | RoundWarningRecord
    | StandingBudgetRecord
    | RunPhaseRecord
    | RunWiringRecord
    | SampleScoredRecord
    | SampleStartedRecord
    | ScoringLockedRecord
    | SpawnedRecord
    | SpendHoldRecord
    | SpendTombstoneRecord
    | TrailTombstoneRecord
    | TokenUsageRecord,
    Field(discriminator="record_type"),
]

RECORD_ADAPTER: TypeAdapter[CycleRecord] = TypeAdapter(
    CycleRecord, config=ConfigDict(defer_build=True)
)


class RebaseRequest(FrozenModel):
    """A policy change and a rewind are ONE move: the new axis is searched only on the sibling."""

    fork_from_round: int
    trigger: ForkTrigger
    reason: str
    issued_by: Principal
    config_overrides: ConfigOverrides | None = None


# PER LEVEL: an inner campaign gets its own, so a `fork_proposal` on every fire cannot spiral.
MAX_AUTO_REBASES: Annotated[int, shapes_optimizer_prompt] = 10
