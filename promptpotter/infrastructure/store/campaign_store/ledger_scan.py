from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Any, ClassVar, NamedTuple, cast, get_args

from promptpotter.domain.arms import ArmState, Cut, Measured, Skipped
from promptpotter.domain.bench import BenchPasses, BenchReading, PartitionRecord
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    LoopPayload,
    OriginGateDecisionPayload,
    PauseCyclePayload,
    SetSampleLookaheadPayload,
    SkipSearchpointPayload,
    SubmitArmsPayload,
)
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phase_views import (
    BenchGradedView,
    InitEnterView,
    InitExitView,
    VerifyGradedView,
)
from promptpotter.domain.phases import CampaignPhase, ErrorRecord, RunPhase
from promptpotter.domain.pipeline_schema import CapabilityMenu
from promptpotter.domain.results import RoundCells
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.run_records import (
    AnnouncedArms,
    ArmRecord,
    CommandAckRecord,
    CommandRecord,
    ComparisonExit,
    CycleFinalRecord,
    CycleMintedRecord,
    CycleRecord,
    CycleSeedRecord,
    CycleShareRecord,
    CycleSupersededRecord,
    ElectionRecord,
    ForkDirection,
    ForkGradedRecord,
    ForkSpec,
    InterventionRecord,
    LaunchClaimRecord,
    LaunchClockRecord,
    LaunchReleasedRecord,
    LeftComparisonRecord,
    PhaseRecord,
    PricedKeyRecord,
    RaceCatchUpRecord,
    ResolvedRunRecord,
    ResumeCheckpointRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundProposedRecord,
    RoundRestatedRecord,
    RoundStatement,
    RunPhaseRecord,
    RunWiringRecord,
    SampleScoredRecord,
    ScoringLockedRecord,
    SpawnedBy,
    SpawnedRecord,
    StandingBudgetRecord,
    TokenUsageRecord,
    WallClock,
)
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import WalkedCell
from promptpotter.domain.spend import (
    Budget,
    CeilingMeter,
    SpendRollup,
    metered,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.verify_pass import VerifyPass, VerifyReading
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import (
    LedgerFold,
    LedgerIndex,
    LedgerSpan,
    LineIndex,
    RecordClasses,
)
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.identity import Principal
from promptpotter.shared.measurement_context import IndividualWalk, MeasurementRole


class _Last:
    records: ClassVar[RecordClasses]

    def __init__(self) -> None:
        self._found: StrictModel | None = None

    def feed(self, record: StrictModel) -> None:
        self._found = record

    def value(self) -> StrictModel | None:
        return self._found


@cache
def _last(record: type[StrictModel]) -> type[_Last]:
    return type(f"_Last{record.__name__}", (_Last,), {"records": (record,)})


@dataclass(frozen=True)
class ResolvedRun:
    declaration: dict[str, Any] | None = None
    experiment: dict[str, Any] | None = None
    # The cycle whose ledger landed the roster that stands.
    experiment_cycle_id: str | None = None
    model_capabilities: CapabilityMenu = field(default_factory=dict)
    backend_revision: str | None = None

    def then(self, later: ResolvedRun) -> ResolvedRun:
        first = self if self.experiment else later
        return ResolvedRun(
            declaration=later.declaration or self.declaration,
            experiment=first.experiment,
            experiment_cycle_id=first.experiment_cycle_id,
            model_capabilities=later.model_capabilities or self.model_capabilities,
            backend_revision=later.backend_revision or self.backend_revision,
        )


class _ResolvedRun:
    records: ClassVar[RecordClasses] = (ResolvedRunRecord,)

    def __init__(self) -> None:
        self._found = ResolvedRun()

    def feed(self, record: ResolvedRunRecord) -> None:
        self._found = self._found.then(
            ResolvedRun(
                record.declaration,
                record.experiment,
                model_capabilities=record.model_capabilities,
                backend_revision=record.backend_revision,
            )
        )

    def value(self) -> ResolvedRun:
        return self._found


class _LeftComparison:
    records: ClassVar[RecordClasses] = (LeftComparisonRecord,)

    def __init__(self) -> None:
        self._found: list[ComparisonExit] = []

    def feed(self, record: LeftComparisonRecord) -> None:
        self._found.append(record.act)

    def value(self) -> list[ComparisonExit]:
        return list(self._found)


class _LaunchClocks:
    records: ClassVar[RecordClasses] = (LaunchClockRecord,)

    def __init__(self) -> None:
        self._found: list[LaunchClockRecord] = []

    def feed(self, record: LaunchClockRecord) -> None:
        self._found.append(record)

    def value(self) -> list[LaunchClockRecord]:
        return list(self._found)


class CycleFacts(NamedTuple):
    minted: CycleMintedRecord | None = None
    ended: RunPhaseRecord | None = None
    paused: RunPhaseRecord | None = None
    final: CycleFinalRecord | None = None
    superseded_by: str | None = None
    crash_traceback: str | None = None
    direction: ForkDirection | None = None
    interventions: tuple[InterventionRecord, ...] = ()
    spawned_by: SpawnedBy | None = None
    formula: str | None = None
    updated_at: str = ""

    @property
    def fork(self) -> ForkSpec | None:
        spec = None if self.minted is None else self.minted.fork
        if spec is None or self.direction is None:
            return spec
        return spec.model_copy(update={"direction": self.direction})


_CycleFact = (
    CycleMintedRecord
    | CycleFinalRecord
    | CycleSupersededRecord
    | ForkGradedRecord
    | InterventionRecord
    | SpawnedRecord
    | RunPhaseRecord
    | PhaseRecord
    | RoundClosedRecord
    | ErrorRecord
)


def _init_formula(record: PhaseRecord) -> str | None:
    match record:
        case PhaseRecord(
            phase=CampaignPhase.INIT,
            view=InitEnterView(composite_fitness_formula=str() as formula)
            | InitExitView(composite_fitness_formula=str() as formula),
        ):
            return formula
    return None


class _CycleFacts:
    records: ClassVar[RecordClasses] = get_args(_CycleFact)

    def __init__(self) -> None:
        self._held = CycleFacts()

    def feed(self, record: _CycleFact) -> None:
        held = self._held
        match record:
            case CycleMintedRecord():
                held = held._replace(minted=record)
            case RunPhaseRecord(run_phase=RunPhase.TERMINAL):
                held = held._replace(ended=record, paused=None)
            case RunPhaseRecord():
                held = held._replace(
                    ended=None,
                    paused=record if record.run_phase is RunPhase.PAUSED else None,
                    final=None,
                    superseded_by=None,
                    crash_traceback=None,
                )
            case ErrorRecord():
                held = held._replace(crash_traceback=record.traceback)
            case CycleFinalRecord():
                held = held._replace(final=record)
            case CycleSupersededRecord():
                held = held._replace(superseded_by=record.successor_cycle_id)
            case ForkGradedRecord():
                held = held._replace(direction=record.direction)
            case InterventionRecord():
                held = held._replace(interventions=(*held.interventions, record))
            case SpawnedRecord():
                held = held._replace(spawned_by=record.spawned_by)
            case PhaseRecord():
                if (formula := _init_formula(record)) is None:
                    return
                held = held._replace(formula=formula)
            case RoundClosedRecord():
                pass
        self._held = held._replace(updated_at=record.timestamp)

    def value(self) -> CycleFacts:
        return self._held


class PauseAsk(NamedTuple):
    command_id: str
    issued_by: Principal
    at: str


class LookaheadAsk(NamedTuple):
    command_id: str
    cells: int
    auto: bool
    taken: bool = False


class Controls(NamedTuple):
    pause: PauseAsk | None = None
    skips: tuple[str, ...] = ()
    lookahead: LookaheadAsk | None = None
    gate_decisions: tuple[tuple[str, str], ...] = ()
    submissions: tuple[tuple[str, SubmitArmsPayload], ...] = ()


_LOOP_TAKEN = frozenset(
    kind for kind, model in PAYLOAD_MODEL_FOR_KIND.items() if issubclass(model, LoopPayload)
)


class _Controls:
    records: ClassVar[RecordClasses] = (CommandRecord, CommandAckRecord, RunPhaseRecord)

    def __init__(self) -> None:
        self._held = Controls()
        self._asked: dict[str, CommandRecord] = {}
        self._kinds: dict[str, type[LoopPayload]] = {}
        self._declared: RunPhase | None = None

    def feed(self, record: CommandRecord | CommandAckRecord | RunPhaseRecord) -> None:
        match record:
            case CommandRecord():
                if record.kind in _LOOP_TAKEN:
                    self._asked[record.command_id] = record
            case CommandAckRecord(command_id=command_id, status="accepted"):
                if asked := self._asked.pop(command_id, None):
                    self._arm(asked)
            case CommandAckRecord(command_id=command_id, status="rejected"):
                self._asked.pop(command_id, None)
            case CommandAckRecord(command_id=command_id):
                if asked := self._asked.pop(command_id, None):
                    # Applied with no accept before it: a look-ahead DISARM, nothing to take.
                    self._arm(asked)
                elif kind := self._kinds.pop(command_id, None):
                    self._take(command_id, kind)
            case RunPhaseRecord(run_phase=declared):
                launched = declared is RunPhase.RUNNING and self._declared is not RunPhase.GATE
                if launched or declared in (RunPhase.PAUSED, RunPhase.TERMINAL):
                    # A stop or a new launch empties the inbox; an ``auto`` look-ahead is a mode.
                    kept = self._held.lookahead
                    kept = kept if kept is not None and kept.auto else None
                    self._held = Controls(lookahead=kept)
                    self._kinds = (
                        {} if kept is None else {kept.command_id: SetSampleLookaheadPayload}
                    )
                self._declared = declared

    def _arm(self, asked: CommandRecord) -> None:
        command_id, held = asked.command_id, self._held
        kind = PAYLOAD_MODEL_FOR_KIND[asked.kind]
        assert issubclass(kind, LoopPayload), "only a kind in `_LOOP_TAKEN` is asked"
        match kind.model_validate(asked.payload):
            case PauseCyclePayload():
                if held.pause is not None:
                    return
                held = held._replace(pause=PauseAsk(command_id, asked.issued_by, asked.timestamp))
            case SkipSearchpointPayload():
                held = held._replace(skips=(*held.skips, command_id))
            case OriginGateDecisionPayload(decision=decision):
                held = held._replace(gate_decisions=(*held.gate_decisions, (command_id, decision)))
            case SubmitArmsPayload() as submitted:
                held = held._replace(submissions=(*held.submissions, (command_id, submitted)))
            case SetSampleLookaheadPayload(cells=cells, auto=auto):
                if held.lookahead is not None:
                    self._kinds.pop(held.lookahead.command_id, None)
                armed = auto or cells > 1
                held = held._replace(
                    lookahead=LookaheadAsk(command_id, cells, auto) if armed else None
                )
                if not armed:
                    self._held = held
                    return
        self._kinds[command_id] = kind
        self._held = held

    def _take(self, command_id: str, kind: type[LoopPayload]) -> None:
        held = self._held
        if kind is PauseCyclePayload:
            held = held._replace(pause=None)
        elif kind is SkipSearchpointPayload:
            held = held._replace(skips=tuple(s for s in held.skips if s != command_id))
        elif kind is OriginGateDecisionPayload:
            held = held._replace(
                gate_decisions=tuple(d for d in held.gate_decisions if d[0] != command_id)
            )
        elif kind is SubmitArmsPayload:
            held = held._replace(
                submissions=tuple(s for s in held.submissions if s[0] != command_id)
            )
        else:
            ask = held.lookahead
            if ask is not None and ask.auto:
                held = held._replace(lookahead=ask._replace(taken=True))
            else:
                held = held._replace(lookahead=None)
        self._held = held

    def value(self) -> Controls:
        return self._held


class _LaunchClaim:
    records: ClassVar[RecordClasses] = (LaunchClaimRecord, LaunchReleasedRecord, RunPhaseRecord)

    def __init__(self) -> None:
        self._found: LaunchClaimRecord | None = None

    def feed(self, record: LaunchClaimRecord | LaunchReleasedRecord | RunPhaseRecord) -> None:
        match record:
            case RunPhaseRecord():
                self._found = None
            case LaunchClaimRecord():
                self._found = record
            case LaunchReleasedRecord(job_id=job_id):
                if self._found is not None and job_id == self._found.job_id:
                    self._found = None

    def value(self) -> LaunchClaimRecord | None:
        return self._found


class Progress(NamedTuple):
    progressed_at: float | None = None
    waiting_since: float | None = None


class _Progress:
    def __init__(self) -> None:
        self._held = Progress()

    def feed(self, line: dict[str, Any]) -> None:
        kind = line.get("record_type")
        # A heartbeat proves the process alive, not that it progressed.
        if kind == "llm_call_progress":
            return
        held = self._held
        if kind == "flight":
            waiting = line.get("waiting")
            since = waiting.get("since") if isinstance(waiting, dict) else None
            held = held._replace(
                waiting_since=float(since) if isinstance(since, int | float) else None
            )
        elif kind == "run_phase":
            held = held._replace(waiting_since=None)
        at = epoch_seconds(line.get("timestamp"))
        self._held = held if at is None else held._replace(progressed_at=at)

    def value(self) -> Progress:
        return self._held


class StandingArm(NamedTuple):
    """One arm of one round as its records left it; ``fold`` is `AnnouncedArms.fold`."""

    state: ArmState
    cells: dict[str, WalkedCell]
    fold: ScoreSummary | None


class StandingRound(NamedTuple):
    close: RoundStatement
    at_offset: int


class StandingProposal(NamedTuple):
    record: RoundProposedRecord
    # Where the generation landed, never where its phase opened: everything it read, a critique
    # restated late included, was on the ledger by then.
    at_offset: int
    # Where its round was entered on the same ledger: the boundary a replay of the round is cut at.
    entered_at: int | None = None


_Pass = tuple[int, str, MeasurementRole]


def _before[V](held: Mapping[Any, V], rnd: int | None) -> dict[Any, V]:
    if rnd is None:
        return dict(held)
    return {k: v for k, v in held.items() if (k if isinstance(k, int) else k[0]) < rnd}


class StandingRounds(NamedTuple):
    rounds: dict[int, StandingRound]
    proposals: dict[int, StandingProposal]
    # The operator's answer at the arm gate, beside the drafts it answered and never over them.
    submitted: dict[int, RoundProposedRecord]
    # An individual is an arm of every round that walked it: ``(round, key)`` is the join.
    arms: dict[tuple[int, ArmKey], StandingArm]
    # Every pass no arm walks: a bench pass, an overlap top-up.
    passes: dict[_Pass, dict[str, WalkedCell]]
    # No entry = never elected; elected-and-crowned-nobody is an entry with no selected arm.
    elections: dict[int, ElectionRecord]
    # Keyed on the record's round STAMP, never ledger position: a round can close more than once.
    decisions: dict[int, list[ResumeCheckpointRecord]]
    # The cells an operator's skip left each arm at: it stands as its proposal does, past a re-entry.
    skipped: dict[tuple[int, ArmKey], int]
    entered: int | None = None
    rewound: int | None = None

    @classmethod
    def none(cls) -> StandingRounds:
        return cls({}, {}, {}, {}, {}, {}, {}, {})

    def displaced(self, entered: int | None, rewound: int | None) -> StandingRounds:
        return self._replace(
            rounds=_before(self.rounds, entered),
            proposals=_before(self.proposals, rewound),
            submitted=_before(self.submitted, rewound),
            arms=_before(self.arms, entered),
            passes=_before(self.passes, entered),
            elections=_before(self.elections, entered),
            decisions=_before(self.decisions, entered),
            skipped=_before(self.skipped, rewound),
        )

    def then(self, later: StandingRounds) -> StandingRounds:
        prefix = self.displaced(later.entered, later.rewound)
        for n, made in later.decisions.items():
            prefix.decisions[n] = [*prefix.decisions.get(n, []), *made]
        return StandingRounds(
            rounds=dict(sorted({**prefix.rounds, **later.rounds}.items())),
            proposals={**prefix.proposals, **later.proposals},
            submitted={**prefix.submitted, **later.submitted},
            arms={**prefix.arms, **later.arms},
            passes={**prefix.passes, **later.passes},
            elections={**prefix.elections, **later.elections},
            decisions=prefix.decisions,
            skipped={**prefix.skipped, **later.skipped},
            entered=later.entered if self.entered is None else self.entered,
            rewound=later.rewound if self.rewound is None else self.rewound,
        )

    @property
    def last_closed(self) -> int | None:
        return max(self.rounds) if self.rounds else None

    @property
    def standing(self) -> RunStanding | None:
        """The LATEST round's last statement, never the last statement's: the live dashboard's rule."""
        return self.rounds[max(self.rounds)].close.run_standing if self.rounds else None

    @property
    def level_series(self) -> LevelSeries | None:
        return self.rounds[max(self.rounds)].close.level_series if self.rounds else None

    @property
    def crowns(self) -> dict[int, ArmKey]:
        return {
            n: key
            for n, election in self.elections.items()
            if (key := election.selected) is not None
        }


def _walked_cell(scored: SampleScoredRecord) -> WalkedCell | None:
    return scored.result.address if scored.result.facts.sample_key else None


def _earliest(held: int | None, rnd: int | None) -> int | None:
    if held is None or rnd is None:
        return rnd if held is None else held
    return min(held, rnd)


def _take(taken: dict[str, WalkedCell], cell: WalkedCell) -> None:
    # Popped first: the cell moves to where the walk last read it.
    taken.pop(cell[0], None)
    taken[cell[0]] = cell


_RoundStep = (
    RoundEnteredRecord
    | ElectionRecord
    | ResumeCheckpointRecord
    | RoundClosedRecord
    | RoundProposedRecord
    | RoundRestatedRecord
    | ArmRecord
    | SampleScoredRecord
)


class _Standing:
    records: ClassVar[RecordClasses] = get_args(_RoundStep)

    def __init__(self) -> None:
        self.at_offset = -1
        self._held = StandingRounds.none()
        self._entered_at: dict[int, int] = {}
        self._arms = AnnouncedArms()
        self._cells: dict[tuple[int, ArmKey], dict[str, WalkedCell]] = {}

    def feed(self, record: _RoundStep) -> None:
        rnd = record.round
        if rnd is None:
            return
        held, offset = self._held, self.at_offset
        match record:
            case RoundEnteredRecord():
                self._entered_at[rnd] = offset
                rewound = rnd if record.rewound else None
                self._held = held.displaced(rnd, rewound)._replace(
                    entered=_earliest(held.entered, rnd),
                    rewound=_earliest(held.rewound, rewound),
                )
                self._arms.enter(record)
                self._cells = _before(self._cells, rnd)
            case ElectionRecord():
                held.elections[rnd] = record
            case ResumeCheckpointRecord():
                held.decisions.setdefault(rnd, []).append(record)
            case RoundClosedRecord():
                self._arms.close(record, record.arms)
                for arm_key in record.arms:
                    self._cells.setdefault((rnd, arm_key), {})
                held.rounds[rnd] = StandingRound(record, offset)
            case RoundRestatedRecord():
                # A restatement is its round WHOLE, so the ledger holding it owns every arm.
                self._arms.close(record, record.arms)
                for arm_key in record.arms:
                    self._cells[rnd, arm_key] = {
                        c[0]: c for c in record.cells.arms.get(arm_key, ())
                    }
                held.rounds[rnd] = StandingRound(record, offset)
            case RoundProposedRecord() if record.submitted:
                held.submitted[rnd] = record
            case RoundProposedRecord():
                held.proposals[rnd] = StandingProposal(record, offset, self._entered_at.get(rnd))
            case ArmRecord():
                self._arms.announce(record)
                self._cells.setdefault((rnd, record.arm), {})
                match record.state:
                    case Cut(reason=Skipped()):
                        fold = self._arms.fold(rnd, record.arm)
                        held.skipped[rnd, record.arm] = 0 if fold is None else fold.walked
                    case Cut() | Measured():
                        held.skipped.pop((rnd, record.arm), None)
            case SampleScoredRecord():
                walking = self._arms.take(record)
                if (cell := _walked_cell(record)) is None:
                    return
                if walking is not None:
                    _take(self._cells[rnd, walking], cell)
                else:
                    _take(
                        held.passes.setdefault((rnd, record.individual_id, record.role), {}), cell
                    )

    def value(self) -> StandingRounds:
        held = self._held
        arms = self._arms
        return held._replace(
            rounds=dict(held.rounds),
            proposals=dict(held.proposals),
            submitted=dict(held.submitted),
            arms={
                (rnd, key): StandingArm(
                    arms.state(rnd, key), dict(self._cells[rnd, key]), arms.fold(rnd, key)
                )
                for rnd, key in arms
            },
            passes={k: dict(taken) for k, taken in held.passes.items()},
            elections=dict(held.elections),
            decisions={n: list(made) for n, made in held.decisions.items()},
            skipped=dict(held.skipped),
        )


_MOVES_ROUNDS = (RoundEnteredRecord, RoundClosedRecord, RoundRestatedRecord)
assert set(_MOVES_ROUNDS) <= set(_Standing.records)


def moves_index(record: CycleRecord) -> bool:
    """Whether *record* moves a cycle's index (`projections/cycle_index.py`): a fact its fold takes, or a round that stands."""
    if isinstance(record, PhaseRecord):
        return _init_formula(record) is not None
    return isinstance(record, (*_CycleFacts.records, *_MOVES_ROUNDS))


_Naming = (
    SampleScoredRecord | RoundClosedRecord | RoundRestatedRecord | RaceCatchUpRecord | PhaseRecord
)


class _Answers:
    """Every answer a ledger names — a slotted walk's, a close's rows, a catch-up's, a verify
    pass's — displaced rounds included: what a campaign has walked, read through the extractors
    `scan_ledger_walks` reads what STANDS through, so memory holds all evidence."""

    records: ClassVar[RecordClasses] = get_args(_Naming)

    def __init__(self) -> None:
        self._found: set[str] = set()
        self._frozen: frozenset[str] | None = frozenset()

    def feed(self, record: _Naming) -> None:
        match record:
            case SampleScoredRecord():
                walked = [] if (cell := _walked_cell(record)) is None else [cell]
            case RoundClosedRecord() | RoundRestatedRecord():
                walks = record.cells.walks()
                walked = [cell for walk in walks for cell in walk.cells]
            case RaceCatchUpRecord():
                walked = list(record.cells.values())
            case PhaseRecord(view=VerifyGradedView(verify_pass=banked)):
                walked = list(banked.walk().cells)
            case PhaseRecord():
                return
        named = [answer for _, answer, _ in walked]
        if not self._found.issuperset(named):
            self._found.update(named)
            self._frozen = None

    def value(self) -> frozenset[str]:
        # A fenced task reads it once per population, so it is frozen once per change.
        if self._frozen is None:
            self._frozen = frozenset(self._found)
        return self._frozen


class VerifyLedger(NamedTuple):
    graded: dict[ArmKey, tuple[VerifyPass, VerifyReading]]
    # EVERY graded pass, oldest first: a later one buys other cells and retires none.
    passes: list[VerifyPass]


class _Verify:
    records: ClassVar[RecordClasses] = (PhaseRecord,)

    def __init__(self) -> None:
        self._graded: dict[ArmKey, tuple[VerifyPass, VerifyReading]] = {}
        self._passes: list[VerifyPass] = []

    def feed(self, record: PhaseRecord) -> None:
        if isinstance(banked := record.view, VerifyGradedView):
            self._graded[banked.verify_pass.arm.key] = banked.verify_pass, banked.reading
            self._passes.append(banked.verify_pass)

    def value(self) -> VerifyLedger:
        return VerifyLedger(dict(self._graded), list(self._passes))


class LedgerSpend(NamedTuple):
    spend: SpendRollup
    calls: int
    worked_s: float


class _RoundSpend(NamedTuple):
    by_round: dict[int, SpendRollup]
    calls: int
    worked_s: float


class _Spend:
    records: ClassVar[RecordClasses] = (TokenUsageRecord,)

    def __init__(self) -> None:
        self._by_round: dict[int, SpendRollup] = {}
        self._calls = 0
        self._worked_s = 0.0

    def feed(self, usage: TokenUsageRecord) -> None:
        self._by_round.setdefault(usage.spend_round, SpendRollup()).bank(usage)
        if not usage.cached:
            self._calls += 1
            self._worked_s += usage.duration_s

    def value(self) -> _RoundSpend:
        return _RoundSpend(
            {n: held.model_copy(deep=True) for n, held in self._by_round.items()},
            self._calls,
            self._worked_s,
        )


class _Metered:
    """A running total per ceiling meter: a campaign's ceiling read adds one per cycle."""

    records: ClassVar[RecordClasses] = (TokenUsageRecord,)

    def __init__(self) -> None:
        self._usd: dict[CeilingMeter, float] = dict.fromkeys(get_args(CeilingMeter), 0.0)
        self._tokens: dict[CeilingMeter, int] = dict.fromkeys(get_args(CeilingMeter), 0)

    def feed(self, usage: TokenUsageRecord) -> None:
        for meter in self._usd:
            usd, tokens = metered(usage, meter)
            if usd is not None:
                self._usd[meter] += usd
            self._tokens[meter] += tokens

    def value(self) -> dict[CeilingMeter, Budget]:
        return {meter: Budget.amount(self._usd[meter], self._tokens[meter]) for meter in self._usd}


class _Bench:
    records: ClassVar[RecordClasses] = (PhaseRecord,)

    def __init__(self) -> None:
        self._graded: list[BenchGradedView] = []

    def feed(self, record: PhaseRecord) -> None:
        if record.phase != CampaignPhase.BENCH or record.event != "graded":
            return
        if isinstance(record.view, BenchGradedView):
            self._graded.append(record.view)

    def value(self) -> list[BenchGradedView]:
        return list(self._graded)


class _PricedKeys:
    records: ClassVar[RecordClasses] = (PricedKeyRecord,)

    def __init__(self) -> None:
        self._keys: set[str] = set()

    def feed(self, record: PricedKeyRecord) -> None:
        self._keys.add(record.priced_key)

    def value(self) -> frozenset[str]:
        return frozenset(self._keys)


class Timeline(NamedTuple):
    """The timed facts the wall clock reads, each led by its record's stamp, in ledger order."""

    phases: list[tuple[float, str, int | None, str]]
    gates: list[tuple[float, bool]]
    closes: list[tuple[float, int]]
    calls: list[tuple[float, str, str, float]]
    unworked: list[tuple[float, float]]


_Clocked = PhaseRecord | RunPhaseRecord | RoundClosedRecord | TokenUsageRecord | SampleScoredRecord


class _Timeline:
    records: ClassVar[RecordClasses] = get_args(_Clocked)

    def __init__(self) -> None:
        self._held = Timeline([], [], [], [], [])

    def feed(self, record: _Clocked) -> None:
        if (at := epoch_seconds(record.timestamp)) is None:
            return
        held = self._held
        match record:
            case PhaseRecord():
                held.phases.append((at, record.phase, record.round, record.event))
            case RunPhaseRecord():
                held.gates.append((at, record.run_phase is RunPhase.GATE))
            case RoundClosedRecord():
                held.closes.append((at, record.round))
            case TokenUsageRecord() if not record.cached:
                held.calls.append((at, record.kind, record.node, record.duration_s))
            case SampleScoredRecord():
                facts = record.result.facts
                if not facts.cached and (seconds := facts.pipeline.unworked_s) is not None:
                    held.unworked.append((at, seconds))

    def value(self) -> Timeline:
        held = self._held
        return Timeline(
            list(held.phases),
            list(held.gates),
            list(held.closes),
            list(held.calls),
            list(held.unworked),
        )


LEDGER_FOLDS = (
    *map(
        _last,
        (
            CycleSeedRecord,
            CycleShareRecord,
            PartitionRecord,
            StandingBudgetRecord,
            RunPhaseRecord,
            RunWiringRecord,
            ScoringLockedRecord,
        ),
    ),
    _ResolvedRun,
    _LeftComparison,
    _LaunchClocks,
    _CycleFacts,
    _Controls,
    _LaunchClaim,
    _Standing,
    _Answers,
    _Verify,
    _Spend,
    _Metered,
    _Bench,
    _PricedKeys,
    _Timeline,
)


def _view[V](ledger_path: Path, fold: Callable[[], LedgerFold[Any, V]]) -> V:
    return LedgerIndex.of(ledger_path, LEDGER_FOLDS).view(fold)


def scan_last[R: StrictModel](spans: Iterable[LedgerSpan], record: type[R]) -> R | None:
    """The last *record* the spans hold, a later span's over an earlier one's."""
    found: R | None = None
    for span in spans:
        own = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_last(record), span.until)
        found = found if own is None else cast("R", own)
    return found


def scan_resolved_run(spans: Iterable[LedgerSpan]) -> ResolvedRun:
    resolved = ResolvedRun()
    for span in spans:
        own = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_ResolvedRun, span.until)
        if own.experiment is not None:
            own = replace(own, experiment_cycle_id=CycleLayout.of_ledger(span.path).cycle_dir.name)
        resolved = resolved.then(own)
    return resolved


def scan_left_comparison(spans: Iterable[LedgerSpan]) -> list[ComparisonExit]:
    """Every act that ran the line off its declared comparison, oldest first."""
    return [
        act
        for span in spans
        for act in LedgerIndex.of(span.path, LEDGER_FOLDS).view(_LeftComparison, span.until)
    ]


def scan_launch_clocks(spans: Iterable[LedgerSpan]) -> list[LaunchClockRecord]:
    return [
        clock
        for span in spans
        for clock in LedgerIndex.of(span.path, LEDGER_FOLDS).view(_LaunchClocks, span.until)
    ]


def scan_ledger_controls(ledger_path: Path) -> Controls:
    return _view(ledger_path, _Controls)


def scan_cycle_share(ledger_path: Path) -> CycleShareRecord | None:
    """The cycle's own share, never inherited: a fork's ledger alone."""
    return scan_last([LedgerSpan(ledger_path)], CycleShareRecord)


def scan_launch_claim(ledger_path: Path) -> LaunchClaimRecord | None:
    return _view(ledger_path, _LaunchClaim)


def scan_bench_passes(spans: Iterable[LedgerSpan]) -> BenchPasses | None:
    held: BenchPasses | None = None
    for span in spans:
        for view in LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Bench, span.until):
            taken = view.bench_pass
            if view.subject == "selected":
                if held is not None:
                    held = held.model_copy(
                        update={"selections": {**held.selections, taken.candidate_id: taken}}
                    )
            elif held is None or held.origin != taken:
                # A different origin pass is a new reference: no earlier selection pairs on it.
                held = BenchPasses(
                    tolerance=view.tolerance,
                    origin=taken,
                    reserve_usd=view.reserve_usd or 0.0,
                    reserve_tokens=view.reserve_tokens or 0,
                    selections={},
                )
    return held


def scan_bench_readings(spans: Iterable[LedgerSpan]) -> dict[tuple[int, ArmKey], BenchReading]:
    return {
        (view.reading.round, ArmKey(view.bench_pass.candidate_id)): view.reading
        for span in spans
        for view in LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Bench, span.until)
        if view.reading is not None
    }


PROGRESS_FOLDS = (_Progress,)


def scan_cycle_facts(ledger_path: Path, until: int | None = None) -> CycleFacts:
    return LedgerIndex.of(ledger_path, LEDGER_FOLDS).view(_CycleFacts, until)


def scan_cell_formula(spans: Iterable[LedgerSpan]) -> str | None:
    formula: str | None = None
    for span in spans:
        held = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_CycleFacts, span.until)
        formula = held.formula or formula
    return formula


def scan_ledger_progress(ledger_path: Path) -> Progress:
    return LineIndex.of(ledger_path, PROGRESS_FOLDS).view(_Progress)


def scan_standing_rounds(spans: Iterable[LedgerSpan]) -> StandingRounds:
    standing = StandingRounds.none()
    for span in spans:
        standing = standing.then(
            LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Standing, span.until)
        )
    return standing


def scan_ledger_walks(spans: Iterable[LedgerSpan]) -> list[IndividualWalk[WalkedCell]]:
    chain = list(spans)
    standing = scan_standing_rounds(chain)
    closed = standing.rounds
    walks = [walk for held in closed.values() for walk in held.close.cells.walks()]
    # Per round: one individual walked in two open rounds is two walks.
    open_rounds: dict[int, dict[ArmKey, list[WalkedCell]]] = {}
    for (round_num, key), fold in standing.arms.items():
        if round_num not in closed and fold.cells:
            open_rounds.setdefault(round_num, {})[key] = list(fold.cells.values())
    walks += [walk for arms in open_rounds.values() for walk in RoundCells(arms=arms).walks()]
    walks += [
        IndividualWalk(individual_id, frozenset({role}), list(taken.values()))
        for (round_num, individual_id, role), taken in standing.passes.items()
        if round_num not in closed
    ]
    # The whole chain: a fork inherits its parent's verify CELLS, though not its assurance.
    for span in chain:
        banked = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Verify, span.until)
        walks += [verify_pass.walk() for verify_pass in banked.passes]
    return walks


_Span = tuple[float, float]
_CallSpan = tuple[str, str, float, float]


def scan_ledger_verify(ledger_path: Path) -> VerifyLedger:
    """Own ledger only: a fork inherits its parent's measurements, not its assurance."""
    return _view(ledger_path, _Verify)


def _phase_spans(
    phases: list[tuple[float, str, int | None, str]], optimizer_phases: frozenset[str]
) -> list[tuple[str, float, float]]:
    brackets = {p.value for p in CampaignPhase} | optimizer_phases
    open_at: dict[tuple[str, int | None], float] = {}
    out: list[tuple[str, float, float]] = []
    for at, phase, rnd, event in phases:
        if phase not in brackets:
            continue
        if event == "enter":
            open_at[(phase, rnd)] = at
        elif event == "exit" and (entered := open_at.pop((phase, rnd), None)) is not None:
            out.append((phase, entered, max(entered, at)))
    return out


def _gate_spans(gates: list[tuple[float, bool]], *, until: float | None) -> list[_Span]:
    out: list[_Span] = []
    opened: float | None = None
    for at, opens in gates:
        if opened is not None:
            out.append((opened, max(opened, at)))
            opened = None
        if opens:
            opened = at
    # An abandoned gate (abort, vanished producer) closes at *until*: it was held that long.
    if opened is not None and until is not None:
        out.append((opened, max(opened, until)))
    return out


def _call_spans(
    calls: list[tuple[float, str, str, float]], *, opened: float | None
) -> list[_CallSpan]:
    out: list[_CallSpan] = []
    for at, kind, node, seconds in calls:
        # A bill is stamped when it LANDS, so the span ends at its record.
        start = at - seconds if opened is None else max(at - seconds, opened)
        if start < at:
            out.append((kind, node, start, at))
    return out


def _merged(spans: list[_Span]) -> list[_Span]:
    out: list[_Span] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _unbracketed_call_seconds(
    calls: list[_CallSpan], covered: list[_Span]
) -> dict[str, dict[str, float]]:
    events = sorted(
        [(start, 1, (bucket, node)) for bucket, node, start, _ in calls]
        + [(end, -1, (bucket, node)) for bucket, node, _, end in calls]
    )
    active: dict[tuple[str, str], int] = {}
    out: dict[str, dict[str, float]] = {}
    cursor, prev = 0, None
    for at, delta, key in events:
        running = sum(active.values())
        if prev is not None and running and at > prev:
            while cursor < len(covered) and covered[cursor][1] <= prev:
                cursor += 1
            free, j = at - prev, cursor
            while j < len(covered) and covered[j][0] < at:
                free -= min(covered[j][1], at) - max(covered[j][0], prev)
                j += 1
            for (bucket, node), n in active.items() if free > 0 else ():
                by_node = out.setdefault(bucket, {})
                by_node[node] = by_node.get(node, 0.0) + free * n / running
        active[key] = active.get(key, 0) + delta
        if not active[key]:
            del active[key]
        prev = at
    return out


def _round_ended_seconds(
    closes: list[tuple[float, int]], *, opened: float | None
) -> dict[str, float]:
    out: dict[str, float] = {}
    if opened is None:
        return out
    for at, rnd in closes:
        # FIRST close wins: round 0 closes again at ruler warm, and a rewind re-runs a round.
        out.setdefault(str(rnd), max(0.0, at - opened))
    return out


def _unworked_seconds(unworked: list[tuple[float, float]]) -> float | None:
    # ``None``, never 0.0, where no cell carried an envelope: nothing watched for a suspend.
    return sum(max(0.0, seconds) for _, seconds in unworked) if unworked else None


def _launched[F: tuple[Any, ...]](
    held: list[Timeline], part: Callable[[Timeline], list[F]], opened: float | None
) -> list[F]:
    # A resumed ledger holds earlier launches; the endpoints are THIS launch's, so only its facts fold.
    facts = (fact for timeline in held for fact in part(timeline))
    return sorted(
        (fact for fact in facts if opened is None or fact[0] >= opened), key=lambda f: f[0]
    )


def scan_ledger_answers(spans: Iterable[LedgerSpan]) -> frozenset[str]:
    held = [LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Answers, span.until) for span in spans]
    return held[0] if len(held) == 1 else frozenset().union(*held)


def scan_ledger_priced_keys(ledger_paths: Iterable[Path]) -> set[str]:
    return {key for path in ledger_paths for key in _view(path, _PricedKeys)}


def scan_ledger_spend(spans: Iterable[LedgerSpan]) -> LedgerSpend:
    spend = SpendRollup()
    calls, worked_s = 0, 0.0
    for span in spans:
        own = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Spend, span.until)
        for held in own.by_round.values():
            spend.absorb(held)
        calls += own.calls
        worked_s += own.worked_s
    return LedgerSpend(spend, calls, worked_s)


def scan_ledger_metered(ledger_path: Path) -> dict[CeilingMeter, Budget]:
    """This ledger's OWN appends, never a fork's walked prefix, in every ceiling meter."""
    return _view(ledger_path, _Metered)


def scan_ledger_spend_by_round(spans: Iterable[LedgerSpan]) -> dict[int, SpendRollup]:
    """Not clamped by a rewind: a round measured twice cost money both times."""
    by_round: dict[int, SpendRollup] = {}
    for span in spans:
        own = LedgerIndex.of(span.path, LEDGER_FOLDS).view(_Spend, span.until)
        for n, held in own.by_round.items():
            by_round.setdefault(n, SpendRollup()).absorb(held)
    return dict(sorted(by_round.items()))


def scan_ledger_wall_clock(
    ledger_paths: Sequence[Path],
    *,
    started_at: str,
    finished_at: str,
    optimizer_phases: frozenset[str],
) -> WallClock:
    """The endpoints are the RUNNER's: the ledger's first record is already past ``init_services``."""
    timelines = [_view(ledger_path, _Timeline) for ledger_path in ledger_paths]
    opened, closed = epoch_seconds(started_at), epoch_seconds(finished_at)
    elapsed = None if opened is None or closed is None else max(0.0, closed - opened)
    phases = _phase_spans(_launched(timelines, lambda t: t.phases, opened), optimizer_phases)
    gates = _gate_spans(_launched(timelines, lambda t: t.gates, opened), until=closed)
    calls = _call_spans(_launched(timelines, lambda t: t.calls, opened), opened=opened)
    covered = _merged([(start, end) for _, start, end in phases] + gates)
    phase_s: dict[str, float] = {}
    for phase, start, end in phases:
        phase_s[phase] = phase_s.get(phase, 0.0) + end - start
    worked_s: dict[str, dict[str, float]] = {}
    for bucket, node, start, end in calls:
        by_node = worked_s.setdefault(bucket, {})
        by_node[node] = by_node.get(node, 0.0) + end - start
    unbracketed = _unbracketed_call_seconds(calls, covered)
    held = sum(end - start for start, end in covered)
    held += sum(s for by_node in unbracketed.values() for s in by_node.values())
    return WallClock(
        elapsed_s=elapsed,
        phase_s=phase_s,
        worked_s=worked_s,
        unbracketed_call_s=unbracketed,
        round_ended_s=_round_ended_seconds(
            _launched(timelines, lambda t: t.closes, opened), opened=opened
        ),
        gate_s=sum(end - start for start, end in gates),
        unattributed_s=None if elapsed is None else max(0.0, elapsed - held),
        unworked_s=_unworked_seconds(_launched(timelines, lambda t: t.unworked, opened)),
    )
