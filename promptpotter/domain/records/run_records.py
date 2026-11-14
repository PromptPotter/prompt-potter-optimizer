from __future__ import annotations

import enum
import os
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, ClassVar, Literal, NamedTuple, Self

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    SerializeAsAny,
    field_validator,
    model_validator,
)

from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.arms import ArmRole, ArmState, Proposal, Proposed, arm_name
from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.crowning_bar import lift_prior
from promptpotter.domain.cycle_paths import CycleHop, Subject
from promptpotter.domain.dashboard_rows import (
    DashboardCandidate,
    RoundHealth,
    RoundSummary,
    RunLimits,
)
from promptpotter.domain.job import UNRESOLVED_HOP, Job
from promptpotter.domain.l4.proxies import PanelPrecision, panel_precision
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.optimizer_state import STATE_KINDS, OptimizerState, StatePart
from promptpotter.domain.paired_reading import ArmKey, ArmName, PriorStanding, RoundArm
from promptpotter.domain.pass_kinds import PASSES
from promptpotter.domain.phase_views import PhaseView, RunSpendView, ViewAnchors
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    GateKind,
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.pipeline_params import (
    NodeCall,
    PipelineParams,
    laid_calls,
    lay_nodes,
)
from promptpotter.domain.pipeline_schema import (
    CapabilityMenu,
    KnobLayer,
    ManifestNodeOverlay,
    NodeSearchNarrowing,
)
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.results import Election, RoundCells, RoundOutcome, RoundResult
from promptpotter.domain.ruler import LevelSeries, RulerReach
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import LedgerCell, WalkedCell
from promptpotter.domain.spend import (
    Budget,
    CeilingMeter,
    KeySource,
    TokenAccount,
    TokenUsageKind,
    UserSpend,
    bill_or_rate_usd,
    cycle_axes_only,
    money_axes_only,
)
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.domain.treatment import Treatment
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.identity import Principal
from promptpotter.shared.measurement_context import MeasurementRole
from promptpotter.shared.worded import Worded


class CheckpointInputs(StatePart, kind=None):
    """One kind of decision, declared in the package taking it: everything its writer banks and its replay reads."""


class ResumeCheckpointRecord(CycleRecord):
    """``inputs_ref`` + ``outcome`` drive divergence; parsed by kind, so a record its writer never writes refuses at the read."""

    record_type: Literal["decision"] = "decision"
    kind: str
    # The manifest node whose member took the decision; ``None`` for the bench's own.
    node: str | None = None
    inputs_ref: SerializeAsAny[CheckpointInputs]
    outcome: Any = None
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @model_validator(mode="before")
    @classmethod
    def _read_by_kind(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        inputs = STATE_KINDS.parse(raw["kind"], raw["inputs_ref"])
        if not isinstance(inputs, CheckpointInputs):
            raise ValueError(f"{raw['kind']!r} is a state kind, never a decision's")
        return {**raw, "inputs_ref": inputs}


class PhaseRecord(CycleRecord):
    record_type: Literal["phase"] = "phase"
    phase: str
    event: str
    round: int | None = None
    view: PhaseView | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class RunPhaseRecord(CycleRecord):
    """The runner's own declaration: every other phase is derived off the last one, never said."""

    forks_inherit: ClassVar[bool] = False
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


class BackendWarningRecord(CycleRecord):
    """An attempt that failed and will be retried: a transport fault or a 5xx, never a 429."""

    record_type: Literal["backend_warning"] = "backend_warning"
    kind: str
    attempt: int
    max_attempts: int
    wait_s: float
    error_class: str | None = None
    status_code: int | None = None
    final: bool = False
    query: Annotated[str, AfterValidator(lambda q: q[:80])] = ""
    detail: Annotated[str, AfterValidator(lambda d: d[:400])] = ""
    timestamp: str = Field(default_factory=utcnow_iso)


class ProviderHoldRecord(CycleRecord):
    """A 429 episode's open or strike, heard by a ledger from its own sends."""

    record_type: Literal["provider_hold"] = "provider_hold"
    sender: str
    scope: str
    strikes: int
    # Epoch: a throttle past it stops the run instead of holding it.
    gives_up_at: float
    reading: BackpressureReading
    timestamp: str = Field(default_factory=utcnow_iso)


class ProviderEasedRecord(CycleRecord):
    """The provider answers the sender again: told at once to every ledger that heard the hold."""

    record_type: Literal["provider_eased"] = "provider_eased"
    sender: str
    held_s: float
    timestamp: str = Field(default_factory=utcnow_iso)


class RoundEnteredRecord(CycleRecord):
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

    def summary(self) -> RoundSummary:
        """This round as the dashboard banks it."""
        arms = self.cells.arms
        longest = arms[max(arms, key=lambda k: (len(arms[k]), k))] if arms else []
        election = self.election

        def name(key: ArmKey) -> ArmName:
            return arm_name(key, self.arms[key], self.round)

        return RoundSummary(
            round=self.round,
            leading=None
            if election is None or election.leading is None
            else name(election.leading),
            selected=None
            if election is None or election.selected is None
            else name(election.selected),
            improved=None if self.round == 0 else self.improved,
            verdict_reason=None if self.round == 0 or election is None else election.verdict_reason,
            candidates=[
                DashboardCandidate(reading=reading)
                for reading in self.arm_readings()
                if reading.role != MeasurementRole.PARENT
            ],
            selection=list(dict.fromkeys(self.positions[key] for key, _, _ in longest)),
            health=RoundHealth.of(self.round, self.health),
            overlap=self.overlap,
            panel_precision=self.panel_precision,
            optimizer_facts=self.optimizer_facts,
            lift_prior=None
            if election is None
            else lift_prior(f.lift for f in election.fit.values() if f.lift is not None),
        )

    def restated(self, optimizer_state: OptimizerState) -> RoundRestatedRecord:
        """This round holding *optimizer_state*: what a member banks once the round has closed."""
        body = {name: getattr(self, name) for name in RoundStatement.model_fields}
        del body["timestamp"]
        return RoundRestatedRecord(**body | {"optimizer_state": optimizer_state})


class RoundClosedRecord(RoundStatement, CycleRecord):
    """The run's clock: a close is its line's latest round, so its standing is the run's."""

    record_type: Literal["round_closed"] = "round_closed"


class RoundRestatedRecord(RoundStatement, CycleRecord):
    """A closed round stated again, never a close: it moves no clock and announces nothing. It is
    the round WHOLE, so a repair's restatement makes its ledger the owner of every arm in it."""

    record_type: Literal["round_restated"] = "round_restated"


class WalkRead(FrozenModel):
    """An arm a walk read, on the cells it asked in the order its rows came: a replayed walk reads
    it again off the archive, so the round's later reads of that arm continue the walk they did."""

    individual_id: str
    cells: list[str]
    role: ArmRole


class RoundProposedRecord(CycleRecord):
    """Banked by the bench once per round; replayed on a resume unless a digest the round ``consumed`` has moved."""

    record_type: Literal["round_proposed"] = "round_proposed"
    round: int
    consumed: str
    proposals: list[Proposal]
    walked: OptimizerState
    reads: list[WalkRead]
    # The ruler lines the round opened on: a re-entry draws on them, never on a fold since.
    opened_on: RulerReach
    submitted: bool = False
    timestamp: str = Field(default_factory=utcnow_iso)


class ArmRecord(CycleRecord):
    """One arm's transition, LAST wins: proposed, walking, then cut or measured."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    record_type: Literal["arm"] = "arm"
    round: int
    arm: ArmKey
    state: ArmState
    anchors: ViewAnchors = Field(default_factory=ViewAnchors)
    timestamp: str = Field(default_factory=utcnow_iso)


class _Pass(CycleRecord):
    """One cell of a pass: an ARM of the round where its `role`'s row says so."""

    round: int
    individual_id: str
    role: MeasurementRole
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def arm(self) -> ArmKey | None:
        """The arm whose walk this cell continues; ``None`` for a pass no arm walks (a bench pass)."""
        return ArmKey(self.individual_id) if PASSES[self.role].arm else None


class SampleStartedRecord(_Pass):
    record_type: Literal["sample_started"] = "sample_started"
    sample_idx: int
    sample_total: int
    position: int
    # Capped at the writer: the whole query is a dataset fact, in every measurement row.
    query_preview: Annotated[str, AfterValidator(lambda q: q[:120])] = ""
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

    def enter(self, round_num: int) -> None:
        self._arms = {at: held for at, held in self._arms.items() if at[0] < round_num}

    def state(self, round_num: int, key: ArmKey) -> ArmState:
        return self._arms[round_num, key].state

    def fold(self, round_num: int, key: ArmKey) -> ScoreSummary | None:
        held = self._arms[round_num, key]
        return None if isinstance(held.state, Proposed) else held.running


class FlightWaiting(FrozenModel):
    position: int
    since: float


class FlightRecord(CycleRecord):
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


class RaceStandingRecord(CycleRecord):
    """Archive-only, not divergence-gated; ``p_best`` is about the arm ``current`` alone."""

    record_type: Literal["race_standing"] = "race_standing"
    round: int
    member: str
    current: ArmKey
    n_samples: int
    p_best: float
    paired_breakdown: dict[ArmKey, PriorStanding]
    timestamp: str = Field(default_factory=utcnow_iso)


class RaceCatchUpRecord(CycleRecord):
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


class CallPrice(FrozenModel):
    """What one call cost, the one carrier of a call's price: on a send's charge, or a replay's own."""

    kind: TokenUsageKind
    # ``None`` where neither the send nor its reply named one, as on a backend cell.
    model: str | None
    # Who BILLED: a rate belongs to the (provider, model) pair; ``None`` ⇒ no rate prices the call.
    provider: str | None
    # The BILL the provider reported, never filled from a rate; ``None`` on a hold, whose money is its bound.
    cost_usd: float | None
    # OUR rate's price of an unbilled call, stamped once: it counts against ceilings, never spent.
    rate_priced_usd: float | None

    @property
    def bill_or_rate_usd(self) -> float | None:
        return bill_or_rate_usd(self.cost_usd, self.rate_priced_usd)


class Hold(CycleRecord):
    """A hold, filed on the book of what it holds: on the account's charge book a run's or a call's
    money, open until a bill or a release closes it (a later hold of the same id restates its
    bound); on a cycle's ledger a job's claim of that cycle, until its run declares itself or the
    job dies (`infrastructure/holders.py`)."""

    record_type: Literal["hold"] = "hold"
    hold_id: str
    # The hold it draws on: a call's its run's, an inner run's its enclosing run's; ``None`` draws
    # on the account alone.
    parent: str | None
    key_source: KeySource
    # The most it and every hold under it may draw; ``None`` on an axis nothing bounds.
    bound: Budget
    # The kind a call's or a cell's hold binds, as its run's share files it; ``None`` on a job's.
    resource: TokenUsageKind | None
    # The node a call or a cell sends for; ``None`` on a job's.
    node: str | None
    # The ledger a call's sends land on, relative to the account; ``None`` on a job's or a cell's.
    ledger: str | None
    timestamp: str = Field(default_factory=utcnow_iso)

    @classmethod
    def of_job(cls, job_id: str, bound: Budget, *, parent: str | None) -> Hold:
        """A job's own hold, its id the job's: its sends draw on the box's key unless a call's says."""
        return cls(
            hold_id=job_id,
            parent=parent,
            key_source="host",
            bound=bound,
            resource=None,
            node=None,
            ledger=None,
        )


class Bill(FrozenModel):
    """One answered part of a call, which closes the call's hold."""

    record_type: Literal["bill"] = "bill"
    hold_id: str
    key_source: KeySource
    # Both money fields ``None``: unpriced.
    price: CallPrice
    tokens: int
    timestamp: str = Field(default_factory=utcnow_iso)


class Release(FrozenModel):
    """A hold closed with nothing more billed on it: a send never made, a run ended."""

    record_type: Literal["release"] = "release"
    hold_id: str
    timestamp: str = Field(default_factory=utcnow_iso)


class Lapse(FrozenModel):
    """A send that may have billed and reported nothing: it closes its call's hold, and *bound* is
    what it may have cost, drawn on every hold above it and unreported at once."""

    record_type: Literal["lapse"] = "lapse"
    hold_id: str
    bound: Budget
    timestamp: str = Field(default_factory=utcnow_iso)


# One line of the account's charge book (`store/wallet_store.py::ChargeBook`).
ChargeLine = Annotated[Hold | Bill | Release | Lapse, Field(discriminator="record_type")]


class TokenUsageRecord(CycleRecord):
    """ONE answered send, or a replay; a send that reported no usage writes none."""

    record_type: Literal["token_usage"] = "token_usage"
    node: str
    # WHICH upstream host answered, where the price's provider is a gateway; ``None`` where it is.
    served_by: str | None = None
    input_tokens: int
    output_tokens: int
    # A SUBSET of ``output_tokens``; 0 also means no breakdown was reported, never "did not think".
    reasoning_tokens: int = 0
    # A SUBSET of ``input_tokens`` the PROVIDER cached; 0 also means no breakdown. Not a replay.
    cache_read_tokens: int = 0
    # The part of ``input_tokens`` billed at a premium to POPULATE that cache.
    cache_write_tokens: int = 0
    duration_s: float = 0.0
    # A send's bill; a replay's recorded price alone, which no wallet paid.
    money: Bill | CallPrice
    # A nested run's own view of its call; money is summed off the copy on the outer ledger.
    mirrored: bool = False
    round: int | None = None
    # ``None`` outside a candidate's pass; a nested run's copy keeps ITS pass: `by_role` skips it.
    role: MeasurementRole | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def bill(self) -> Bill | None:
        """``None`` exactly on a replay."""
        return self.money if isinstance(self.money, Bill) else None

    @property
    def price(self) -> CallPrice:
        return self.money.price if isinstance(self.money, Bill) else self.money

    def filed_as(self, kind: TokenUsageKind) -> TokenUsageRecord:
        price = self.price.model_copy(update={"kind": kind})
        money = price if self.bill is None else self.money.model_copy(update={"price": price})
        return self.model_copy(update={"money": money})

    @property
    def spend_round(self) -> int:
        """A call carrying no round ran before any closed (init, the origin score): banks at 0."""
        return 0 if self.round is None else self.round


class PricedKeyRecord(CycleRecord):
    """A key the search has PRICED, so a later read of it, resumed or not, prices nothing again."""

    record_type: Literal["priced_key"] = "priced_key"
    priced_key: str
    timestamp: str = Field(default_factory=utcnow_iso)


class BankedSpend(FrozenModel):
    """One wallet's `UserSpend`, as a tombstone banks it."""

    used_usd: float
    # What our rate table priced the calls no provider billed (`CallPrice.rate_priced_usd`).
    rate_priced_usd: float
    used_tokens: int
    unpriced_tokens: int

    @classmethod
    def of(cls, spent: UserSpend) -> BankedSpend:
        """Bills alone: an unreported send's cost stays on the account (`Lapse`), past any delete."""
        return cls(
            used_usd=spent.used_usd,
            rate_priced_usd=spent.rate_priced_usd,
            used_tokens=spent.used_tokens,
            unpriced_tokens=spent.unpriced_tokens,
        )

    @property
    def spend(self) -> UserSpend:
        return UserSpend(**self.model_dump())


class SpendTombstoneRecord(CycleRecord):
    """On the WORKSPACE ledger, so deleting a campaign's cycle cannot re-earn its ceiling; the
    account's charges are `store/wallet_store.py::ChargeBook`'s, which no delete reaches."""

    record_type: Literal["spend_tombstone"] = "spend_tombstone"
    campaign_id: str
    # Empty for a whole-campaign bank.
    cycle_id: str = ""
    # By whose key paid it, so the campaign's row keeps what its deleted cycles cost.
    spent: dict[KeySource, BankedSpend]
    # What the campaign's ceiling counted of it in each meter, so a bank needs no manifest to name one.
    metered: dict[CeilingMeter, Budget]
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def subject(self) -> Subject:
        return Subject(self.campaign_id, self.cycle_id)


class BankedStop(FrozenModel):
    """A cycle's abnormal run stop, banked before the cycle went; its commands are the account log's."""

    at: str
    # The campaign and cycle it stopped, as the trail read it before they went, then why.
    subject: str


class TrailTombstoneRecord(CycleRecord):
    """On the WORKSPACE ledger, so deleting a cycle cannot delete that its run stopped."""

    record_type: Literal["trail_tombstone"] = "trail_tombstone"
    campaign_id: str
    # Empty for a whole-campaign bank.
    cycle_id: str = ""
    stops: tuple[BankedStop, ...]
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def subject(self) -> Subject:
        return Subject(self.campaign_id, self.cycle_id)


class LLMCallStartRecord(CycleRecord):
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


class LLMCallProgressRecord(CycleRecord):
    """Heartbeat while the provider call is blocked; cache replays skip it."""

    record_type: Literal["llm_call_progress"] = "llm_call_progress"
    call_id: str
    node: str
    round: int | None = None
    elapsed_s: float
    detail: str | None = None
    # The cycle the waited-on cell runs (`Connector.cell_hop`), in THIS cycle's sandbox; ``None`` on an ordinary call.
    inner: CycleHop | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class LLMCallRecord(CycleRecord):
    record_type: Literal["llm_call"] = "llm_call"
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    call_id: str = ""
    usage: TokenAccount | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


AccountEvent = Literal["uploaded", "request_refused"]

HealthState = Literal["held", "broken", "unverified"]
# Who read a standing: its probe, or the admin bot's poll, whose refusal stands until a poll answers.
HealthReader = Literal["probe", "poll"]


class HealthRecord(CycleRecord):
    """On the box's own WORKSPACE ledger: a probe's state CHANGED. ``unverified`` never is one."""

    record_type: Literal["health"] = "health"
    probe: str
    state: Literal["held", "broken"]
    said: str
    by: HealthReader = "probe"
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


class AccountEventRecord(CycleRecord):
    """On the WORKSPACE ledger; ``user_id`` is the acting principal, a delegate's own id in its delegator's tenant."""

    record_type: Literal["account_event"] = "account_event"
    event: AccountEvent
    user_id: str
    # The file an upload was kept as, what a refused request asked and was told.
    subject: str = ""
    timestamp: str = Field(default_factory=utcnow_iso)


class NoEffect(FrozenModel):
    """An ack whose command names no effect beyond having happened."""

    kind: Literal["none"] = "none"


class StandingEffect(FrozenModel):
    """A loop command accepted for the hold it was admitted against: the job holding its cycle,
    ``None`` where the cycle lock alone holds it."""

    kind: Literal["standing"] = "standing"
    hold: str | None


class DraftOpenedEffect(FrozenModel):
    """A check-in draft opened by upload, dataset or origin: the draft a retry under its key answers."""

    kind: Literal["draft_opened"] = "draft_opened"
    draft_id: str


class LaunchedJob(FrozenModel):
    """What a launch answers of one job it launched: the job, the cycle it holds or will (``None``
    where a queued mint names none yet) and the words every surface names it by."""

    job_id: str
    hop: CycleHop | None
    what: str

    @classmethod
    def of(cls, job: Job, hop: CycleHop | None = None) -> LaunchedJob:
        """*hop* the cycle its hold took; unsaid, the one the job already knows."""
        if hop is None and job.hop != UNRESOLVED_HOP:
            hop = job.hop
        named = (
            job if hop is None else replace(job, campaign_id=hop.campaign_id, cycle_id=hop.cycle_id)
        )
        return cls(job_id=job.job_id, hop=hop, what=named.what)


class LaunchedEffect(FrozenModel):
    """A launch, of any kind: each job it launched, in launch order, which a retry under its key
    answers — after a job's record is reaped too."""

    kind: Literal["launched"] = "launched"
    launches: list[LaunchedJob]


class LaunchAnswer(FrozenModel):
    """What every door answers of a launch, a retry under its key included: the command it filed,
    which its task and its `cancel` name, and each job it launched, in launch order (a
    head-to-head's in its arms' order)."""

    command_id: str
    launches: list[LaunchedJob]


class VerifySaturated(FrozenModel):
    """A verify that bought nothing: every sample of the candidate's search split was already
    measured for its config: its answer, kept on its end as a graded one is."""

    kind: Literal["verify_saturated"] = "verify_saturated"
    dataset_name: str
    already_measured: int


VerifyWord = Literal["saturated"]
# What a `VerifySaturated` says, filled from its own fields: every surface reads it here.
VERIFY_WORDS: Worded[VerifyWord, str] = Worded(
    {
        "saturated": "Every sample in the {dataset_name} search split is already measured for "
        "this config ({already_measured} total). Nothing to add.",
    }
)


# A halt is its `StopReason`, and an outcome the loop decided is a field of the record of its step.
RoundWarningKind = Literal[
    # An L2/L3 reply that never parsed: its refinement is discarded, the prior framing kept.
    "layer_parse_failure",
    # A state panel over its runaway backstop: the items past it never reach the LLM.
    "injection_budget_overrun",
    # A projection that could not write its derived file: the line landed, the file is stale until its next write.
    "derived_file_stale",
]
# `error` = the step produced nothing usable; `warning` = degraded but progressed.
RoundWarningSeverity = Literal["warning", "error"]


class RoundWarningRecord(CycleRecord):
    """A SELF-HEAL the run continued past, mid-round or at its start; never re-add a kind with no emitter."""

    record_type: Literal["round_warning"] = "round_warning"
    kind: RoundWarningKind
    severity: RoundWarningSeverity = "warning"
    message: str
    round: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


def stale_file_warning(projection: str, offset: int, reason: str) -> RoundWarningRecord:
    return RoundWarningRecord(
        kind="derived_file_stale",
        message=f"{projection} could not project line {offset}; its file is stale until its next write",
        detail={"projection": projection, "offset": offset, "reason": reason},
    )


class ForkTrigger(enum.StrEnum):
    """One value per caller of :func:`cut_fork`."""

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

    def past_cut(self, cut_round: int) -> ForkRemainder:
        """A superseding fork's cap re-counts the rounds it lifts below *cut_round*."""
        if self.rounds is None:
            return self
        return self.model_copy(update={"rounds": max(cut_round - 1, 0) + self.rounds})

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
    """One cycle's own delta, laid over its parent's laid seeds (`CycleConfig.laid`)."""

    origin_prompts: dict[str, NodePrompt] = Field(
        default_factory=dict,
        description="C0's prompt loci, per prompt node; empty when the seed carries no origin.",
    )
    origin_params: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="The params twin of `origin_prompts`: a fork off a candidate runs that "
        "candidate's record (`resolved_params`), laid under `pipeline_overlay`, the operator's "
        "edit. None on a seed off no candidate.",
    )
    pipeline_overlay: dict[str, NodeCall] = Field(default_factory=dict)
    optimizer_narrowing: dict[str, NodeSearchNarrowing] = Field(
        default_factory=dict,
        description="Per-fork search-space lock edits, laid on its parent's by part "
        "(`NodeSearchNarrowing`'s fields): `param_keys` subsets, `prompt_fields_held` joins, "
        "`param_allowed_values` replaces per param. Empty for an unedited fork or a "
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

    def runs(self) -> dict[str, dict[str, Any]]:
        """Each node's config the fork runs: its candidate's record under the operator's edit."""
        return lay_nodes(self.origin_params or {}, laid_calls(self.pipeline_overlay))


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
    fitness_ceiling: float | None
    prompt_hashes: dict[str, str]
    # On the origin's OWN samples — never round 1's matched floor, a different sample basis.
    origin_composite_fitness: float | None
    scorer_id: str
    # The round the optimizer's DECLARED pick was selected in.
    result_round: int
    result_prompt_fields: dict[str, dict[str, str]]
    result_pipeline_params: PipelineParams | None


class ElectionRecord(Election, CycleRecord):
    """A round's election, the moment it is held: the close carries the same one."""

    record_type: Literal["election"] = "election"
    round: int
    timestamp: str = Field(default_factory=utcnow_iso)


class StandingBudgetRecord(CycleRecord):
    """A cycle's standing budget, LAST wins; read PHYSICALLY, so a fork never inherits one."""

    forks_inherit: ClassVar[bool] = False
    record_type: Literal["standing_budget"] = "standing_budget"
    # The cycle axes an operator named; the money axes are the campaign's (`CampaignCeilingRecord`).
    budget: Budget = Budget()
    # RUN-scoped: the closed-round count at which the launch in flight pauses (``start-run {step_rounds}``).
    pause_at_round: int | None = None
    # On: a call holds its node's mean billed cost in the run, never its worst case (`SpendBook.expected`).
    overshoot: bool = False
    timestamp: str = Field(default_factory=utcnow_iso)

    _cycle_axes = field_validator("budget")(cycle_axes_only)


class CampaignCeilingRecord(CycleRecord):
    """The money axes an operator named for the whole campaign (`set-limits`), LAST wins on its ROOT's
    ledger, so every cycle of it reads one; every move lands through `jobs/quota.py::move_campaign_money`."""

    record_type: Literal["campaign_ceiling"] = "campaign_ceiling"
    budget: Budget
    timestamp: str = Field(default_factory=utcnow_iso)

    _money_axes = field_validator("budget")(money_axes_only)


class CycleShareRecord(CycleRecord):
    """The cycle's share of its campaign's ceiling, LAST wins and never inherited; every move lands through `hold_moved`."""

    forks_inherit: ClassVar[bool] = False
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


class BackendRevision(FrozenModel):
    """What a launch read of the backend's revision, beside the one its connector was written against."""

    expected: str
    # `None`: the backend answered with none, or could not be asked (`unread`).
    reported: str | None
    # Why it could not be asked: the failure's class alone, since its message names the backend's address.
    unread: str | None

    def drift(self) -> str | None:
        """``None`` where the backend runs the revision expected; else what differs, in a sentence."""
        if self.reported == self.expected:
            return None
        if self.unread is not None:
            said = f"its revision could not be read ({self.unread})"
        else:
            said = f"it reports revision {self.reported or 'none'}"
        return f"the backend's connector expects revision {self.expected}, and {said}"


class RunWiringRecord(CycleRecord):
    """LAST wins, over the whole chain: a fork stands on its parent's until its own lands."""

    record_type: Literal["run_wiring"] = "run_wiring"
    round_cells_ceiling: int
    # DISPLAY only: seeds a surface's metric toggle; the selector decides on its own objective.
    display_metric: DisplayMetric
    elects_on: DisplayMetric
    max_cells_in_flight: int
    measured_unit: MeasuredUnit
    # The optimizer's gateway node: every scoring pass of the run, the origin's included, runs as it.
    measurement_node: str
    run_limits: RunLimits
    # `None` on a mint's record: nothing has moved since the declaration was made.
    defaults_drift: DefaultsDrift | None
    # `None` where the connector checks no revision; a drift is served, never refused (`wiring.py`).
    backend_revision: BackendRevision | None
    timestamp: str = Field(default_factory=utcnow_iso)


class ScoringLockedRecord(CycleRecord):
    """Appended once, as the origin is scored under dials; a config already locked appends none."""

    record_type: Literal["scoring_locked"] = "scoring_locked"
    declared: dict[str, str]
    locked: dict[str, str]
    timestamp: str = Field(default_factory=utcnow_iso)


class PipelineRead(FrozenModel):
    """What run init read: the served pipeline with the dataset's definition laid on."""

    declaration: dict[str, Any]
    # The definition's half of that read, which a campaign outliving its dataset dir still folds.
    dataset_overlay: dict[str, NodeCall]
    # The free tier's launch defaults its mint laid (`free_tier.py::free_tier_overlay`).
    free_tier_overlay: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Per optimizer-node knob the declaration holds, the last layer that set it (`laid_config`).
    knob_layers: dict[str, dict[str, KnobLayer]] = Field(default_factory=dict)


class ResolvedRunRecord(CycleRecord):
    """What a run init resolved, read over the chain: the read LAST wins, the roster FIRST."""

    record_type: Literal["resolved_run"] = "resolved_run"
    read: PipelineRead
    # The roster a connector only NAMES can move under its name; the one every round measured stands.
    experiment: dict[str, Any] | None
    # The menu the run's schema answered with, its reasoning floors included; empty is UNKNOWN.
    model_capabilities: CapabilityMenu
    timestamp: str = Field(default_factory=utcnow_iso)


class ForkSpec(FrozenModel):
    trigger: ForkTrigger
    reason: str
    issued_by: Principal
    # The parent arm the fork branches from: the one its caller named, else the pick the parent's line stood on.
    from_candidate: RoundArm
    # The MEASURED direction; `None` ⇒ the trigger implies it (`FORK_DIRECTION`).
    direction: ForkDirection | None = None

    @property
    def resolved_direction(self) -> ForkDirection:
        return self.direction or FORK_DIRECTION[self.trigger]


class ForkCutFrom(CheckpointInputs, kind="fork_cut"):
    from_round: int
    fork: ForkSpec


class PanelCoverage(CheckpointInputs, kind="panel_coverage"):
    # Every arm of the round's panel, holed or not.
    holes_by_arm: dict[ArmKey, int]


class OriginGatePass(CheckpointInputs, kind="origin_gate_pass"):
    """The operator's pass past the origin the gate stopped: round 0's, standing while it does."""

    by: str


class SpawnedBy(StrictModel):
    """The outer work-item an L4 inner cycle was spawned to measure; an arm's address places the run under its candidate."""

    round: int = Field(description="Outer round; 0 is the origin (C0).")
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
        if self.role is None or not PASSES[self.role].arm:
            return None
        if self.individual_id is None:
            return None
        return RoundArm(round=self.round, arm=ArmKey(self.individual_id))


class CycleMintedRecord(CycleRecord):
    """One per cycle's own ledger, after only the lines it adopted, and its first own fact: a chain
    is walked off it alone. A cycle runs every seed its chain's mints hold, root first."""

    record_type: Literal["cycle_minted"] = "cycle_minted"
    # The parent's cycle dir relative to this one's, POSIX-separated: `../<id>` for a sibling.
    parent: str | None = None
    forked_at_offset: int | None = None
    # The first round the fork runs itself: every round below it is its parent's line's.
    cut_round: int | None = None
    fork: ForkSpec | None = None
    seed: CycleSeed | None = None
    # decisions.md § 0005: its seed steers outside the permitted narrowing, read as a `BABYSAT` act.
    babysat: bool = False
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
    """The parent's cycle id where it is a sibling in the same campaign; ``None`` for a root or a cycle cut from another tree."""
    if parent is None:
        return None
    parts = PurePosixPath(parent).parts
    return parts[1] if len(parts) == 2 and parts[0] == ".." else None


class CycleFinalRecord(CycleRecord):
    """Banked only where the cycle's own runner stops it; a later ``running`` retires it."""

    record_type: Literal["cycle_final"] = "cycle_final"
    final: CycleFinal
    # The round the stop left unclosed; ``None`` for a stop at a boundary.
    interrupted_round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class LaunchClockRecord(CycleRecord):
    """Banked by the cycle holding the campaign's line, at every launch end, a pause's included."""

    record_type: Literal["launch_clock"] = "launch_clock"
    started_at: str
    finished_at: str
    # Folded across every cycle of the line; only the runner knows a launch's endpoints.
    clock: WallClock
    timestamp: str = Field(default_factory=utcnow_iso)


class CycleSupersededRecord(CycleRecord):
    """On the LEFT-BEHIND side of a supersede cut; a later ``running`` here takes the line back."""

    record_type: Literal["cycle_superseded"] = "cycle_superseded"
    successor_cycle_id: str
    timestamp: str = Field(default_factory=utcnow_iso)


class ForkGradedRecord(CycleRecord):
    """A cut's MEASURED direction, on the branch: it overrides the one its trigger implies."""

    record_type: Literal["fork_graded"] = "fork_graded"
    direction: ForkDirection
    timestamp: str = Field(default_factory=utcnow_iso)


class InterventionRecord(CycleRecord):
    """One is enough to make the cycle babysat for good; appended the moment it happens."""

    record_type: Literal["intervention"] = "intervention"
    kind: str
    timestamp: str = Field(default_factory=utcnow_iso)


# The act a babysat mint (`CycleMintedRecord.babysat`) reads as.
BABYSAT = "disallowed_model_override"


class ComparisonExit(enum.StrEnum):
    """An act that runs an arm off the comparison its head-to-head declared: admitted like on any
    campaign, its event is the arm's exit (decisions.md § 0009)."""

    REWIND = "rewind"
    FORK_ON_DIVERGENCE = "fork_on_divergence"
    NO_DIVERGENCE_CHECK = "no_divergence_check"
    FORK = "fork"
    SUBMIT_ARMS = "submit_arms"
    SKIP = "skip"
    SET_LIMITS = "set_limits"


class LeftComparisonRecord(CycleRecord):
    """Appended on an arm's cycle as the act runs: from it on, the arm's line reads uncontrolled."""

    record_type: Literal["left_comparison"] = "left_comparison"
    act: ComparisonExit
    timestamp: str = Field(default_factory=utcnow_iso)


class SpawnedRecord(CycleRecord):
    """Appended at every open, so the last names the work-item the cycle last measured for."""

    record_type: Literal["spawned"] = "spawned"
    spawned_by: SpawnedBy
    timestamp: str = Field(default_factory=utcnow_iso)


class RebaseRequest(FrozenModel):
    """A policy change and a rewind are ONE move: the new axis is searched only on the sibling."""

    # The cut: the first round the fork runs, every round below it lifted.
    fork_from_round: int = Field(ge=1)
    trigger: ForkTrigger
    reason: str
    issued_by: Principal
    config_overrides: ConfigOverrides | None = None
    # The round the fork hangs from, re-elected to the arm a layer named; ``None`` keeps the round's own pick.
    hangs_from: RoundStatement | None = None

    @model_validator(mode="after")
    def _hangs_from_its_branch(self) -> Self:
        if self.hangs_from is not None and self.hangs_from.round != self.fork_from_round - 1:
            raise ValueError(
                f"a fork cut at round {self.fork_from_round} hangs from round "
                f"{self.fork_from_round - 1}, not the round {self.hangs_from.round} it re-elects"
            )
        return self


class StopLoop(Exception):  # noqa: N818 — control-flow signal, not an error
    def __init__(
        self,
        reason: StopReason,
        *,
        unmeasured: int | None = None,
        fork: RebaseRequest | None = None,
    ) -> None:
        if (reason is StopReason.REBASED) != (fork is not None):
            raise ValueError("a REBASED stop, and only one, carries the fork it asks for")
        self.reason = reason
        # Cells of the walk this stop left unmeasured, where the raiser is a walk.
        self.unmeasured = unmeasured
        self.fork = fork
        super().__init__(reason.value)
