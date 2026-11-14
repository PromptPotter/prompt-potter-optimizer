from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Any, ClassVar, NamedTuple, assert_never, cast, get_args

from promptpotter.domain.arms import ArmState, Cut, Measured, Skipped
from promptpotter.domain.bench import BenchReading, BenchScore
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pass_kinds import IndividualWalk
from promptpotter.domain.phase_views import (
    BenchScoredView,
    InitEnterView,
    InitExitView,
    VerificationGradedView,
)
from promptpotter.domain.phases import CampaignPhase, RunPhase, StopReason
from promptpotter.domain.pipeline_schema import CapabilityMenu
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.partition import PartitionRecord
from promptpotter.domain.records.run_records import (
    BABYSAT,
    AnnouncedArms,
    ArmRecord,
    CampaignCeilingRecord,
    ComparisonExit,
    CycleFinalRecord,
    CycleMintedRecord,
    CycleShareRecord,
    CycleSupersededRecord,
    ElectionRecord,
    ForkDirection,
    ForkGradedRecord,
    ForkSpec,
    Hold,
    InterventionRecord,
    LaunchClockRecord,
    LeftComparisonRecord,
    PhaseRecord,
    PipelineRead,
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
from promptpotter.domain.results import RoundCells
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import WalkedCell
from promptpotter.domain.spend import (
    Budget,
    CeilingMeter,
    SpendRollup,
    spend_slot,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.verification import (
    BenchPasses,
    VerificationPass,
    VerifyReading,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import (
    LedgerFold,
    LedgerIndex,
    LedgerSpan,
    LineIndex,
    RecordClasses,
    money_on,
)
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.measurement_context import MeasurementRole


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
    read: PipelineRead | None = None
    experiment: dict[str, Any] | None = None
    # The cycle whose ledger landed the roster that stands.
    experiment_cycle_id: str | None = None
    model_capabilities: CapabilityMenu = field(default_factory=dict)

    def then(self, later: ResolvedRun) -> ResolvedRun:
        first = self if self.experiment else later
        return ResolvedRun(
            read=self.read if later.read is None else later.read,
            experiment=first.experiment,
            experiment_cycle_id=first.experiment_cycle_id,
            model_capabilities=later.model_capabilities or self.model_capabilities,
        )


class _ResolvedRun:
    records: ClassVar[RecordClasses] = (ResolvedRunRecord,)

    def __init__(self) -> None:
        self._found = ResolvedRun()

    def feed(self, record: ResolvedRunRecord) -> None:
        self._found = self._found.then(
            ResolvedRun(
                record.read, record.experiment, model_capabilities=record.model_capabilities
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
    # The ending the standing launch retired; a pause or an ending of its own clears it.
    resumed_ending: StopReason | None = None
    final: CycleFinalRecord | None = None
    superseded_by: str | None = None
    error: ErrorRecord | None = None
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
                # The babysat stamp is the mint's own: an intervention, never a second append.
                stamped = (InterventionRecord(kind=BABYSAT, timestamp=record.timestamp),)
                held = held._replace(
                    minted=record,
                    interventions=(*held.interventions, *(stamped if record.babysat else ())),
                )
            case RunPhaseRecord(run_phase=RunPhase.TERMINAL):
                held = held._replace(ended=record, paused=None, resumed_ending=None)
            case RunPhaseRecord():
                paused = record.run_phase is RunPhase.PAUSED
                held = held._replace(
                    ended=None,
                    paused=record if paused else None,
                    resumed_ending=None
                    if paused
                    else held.ended.stop_reason
                    if held.ended is not None
                    else held.resumed_ending,
                    final=None,
                    superseded_by=None,
                    error=None,
                )
            case ErrorRecord():
                held = held._replace(error=record)
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
            case _:
                assert_never(record)
        self._held = held._replace(updated_at=record.timestamp)

    def value(self) -> CycleFacts:
        return self._held


class CycleHold(NamedTuple):
    """The last job's hold on the cycle, and whether a run declared itself since."""

    hold: Hold
    declared: bool


class _CycleHold:
    records: ClassVar[RecordClasses] = (Hold, RunPhaseRecord)

    def __init__(self) -> None:
        self._found: CycleHold | None = None

    def feed(self, record: Hold | RunPhaseRecord) -> None:
        match record:
            case RunPhaseRecord():
                if self._found is not None:
                    self._found = self._found._replace(declared=True)
            case Hold():
                self._found = CycleHold(record, declared=False)
            case _:
                assert_never(record)

    def value(self) -> CycleHold | None:
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
    CycleMintedRecord
    | RoundEnteredRecord
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
        self._arms = AnnouncedArms()
        self._cells: dict[tuple[int, ArmKey], dict[str, WalkedCell]] = {}

    def feed(self, record: _RoundStep) -> None:
        if isinstance(record, CycleMintedRecord):
            # A fork's mint is its rewound entry at its cut: the rounds below are its parent line's.
            self._held = self._held._replace(entered=record.cut_round, rewound=record.cut_round)
            return
        rnd = record.round
        if rnd is None:
            return
        held, offset = self._held, self.at_offset
        match record:
            case RoundEnteredRecord():
                rewound = rnd if record.rewound else None
                self._held = held.displaced(rnd, rewound)._replace(
                    entered=_earliest(held.entered, rnd),
                    rewound=_earliest(held.rewound, rewound),
                )
                self._arms.enter(rnd)
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
                held.proposals[rnd] = StandingProposal(record, offset)
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
            case _:
                assert_never(record)

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
            case PhaseRecord(view=VerificationGradedView(verification=banked)):
                walked = banked.cells
            case PhaseRecord():
                return
            case _:
                assert_never(record)
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
    graded: dict[ArmKey, tuple[VerificationPass, VerifyReading]]
    # EVERY graded pass, oldest first: a later one buys other cells and retires none.
    passes: list[VerificationPass]


class _Verification:
    """Every graded held-out pass of either kind, oldest first; a reader keys it `(kind, arm.key)`."""

    records: ClassVar[RecordClasses] = (PhaseRecord,)

    def __init__(self) -> None:
        self._graded: list[VerificationGradedView] = []

    def feed(self, record: PhaseRecord) -> None:
        if isinstance(record.view, VerificationGradedView):
            self._graded.append(record.view)

    def value(self) -> list[VerificationGradedView]:
        return list(self._graded)


class _ClosedBench:
    """The last bench a run declared: its close's, which carries the run's own word on its ending."""

    records: ClassVar[RecordClasses] = (PhaseRecord,)

    def __init__(self) -> None:
        self._bench: BenchScore | None = None

    def feed(self, record: PhaseRecord) -> None:
        if isinstance(record.view, BenchScoredView):
            self._bench = record.view.bench

    def value(self) -> BenchScore | None:
        return self._bench


def scan_closed_bench(ledger_path: Path) -> BenchScore | None:
    return _view(ledger_path, _ClosedBench)


def _graded(spans: Iterable[LedgerSpan], kind: MeasurementRole) -> Iterator[VerificationGradedView]:
    for span in spans:
        for view in LedgerIndex.of(span.path).view(_Verification, span.until):
            if view.verification.kind == kind:
                yield view


class LedgerSpend(NamedTuple):
    spend: SpendRollup
    calls: int
    worked_s: float


class _BenchOpen:
    """A bench pass's bracket: entered and not yet exited, the dashboard's running bench mark."""

    records: ClassVar[RecordClasses] = (PhaseRecord,)

    def __init__(self) -> None:
        self._open = False

    def feed(self, record: PhaseRecord) -> None:
        if record.phase == CampaignPhase.BENCH and record.event in ("enter", "exit"):
            self._open = record.event == "enter"

    def value(self) -> bool:
        return self._open


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
            case TokenUsageRecord() if record.bill is not None:
                held.calls.append((at, record.price.kind, record.node, record.duration_s))
            case SampleScoredRecord():
                facts = record.result.facts
                if not facts.cached and (seconds := facts.pipeline.unworked_s) is not None:
                    held.unworked.append((at, seconds))
            case TokenUsageRecord():
                pass
            case _:
                assert_never(record)

    def value(self) -> Timeline:
        held = self._held
        return Timeline(
            list(held.phases),
            list(held.gates),
            list(held.closes),
            list(held.calls),
            list(held.unworked),
        )


LedgerIndex.readers(
    *map(
        _last,
        (
            CampaignCeilingRecord,
            CycleMintedRecord,
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
    _CycleHold,
    _Standing,
    _Answers,
    _Verification,
    _BenchOpen,
    _PricedKeys,
    _Timeline,
)


def _view[V](ledger_path: Path, fold: Callable[[], LedgerFold[Any, V]]) -> V:
    return LedgerIndex.of(ledger_path).view(fold)


def scan_last[R: StrictModel](spans: Iterable[LedgerSpan], record: type[R]) -> R | None:
    """The last *record* the spans hold, a later span's over an earlier one's."""
    found: R | None = None
    for span in spans:
        own = LedgerIndex.of(span.path).view(_last(record), span.until)
        found = found if own is None else cast("R", own)
    return found


def scan_each_last[R: StrictModel](spans: Iterable[LedgerSpan], record: type[R]) -> list[R | None]:
    """Each span's own last *record*, ``None`` where it holds none, the earliest span's first."""
    return [
        cast("R | None", LedgerIndex.of(span.path).view(_last(record), span.until))
        for span in spans
    ]


def scan_resolved_run(spans: Iterable[LedgerSpan]) -> ResolvedRun:
    resolved = ResolvedRun()
    for span in spans:
        own = LedgerIndex.of(span.path).view(_ResolvedRun, span.until)
        if own.experiment is not None:
            own = replace(own, experiment_cycle_id=CycleLayout.of_ledger(span.path).cycle_dir.name)
        resolved = resolved.then(own)
    return resolved


def scan_left_comparison(spans: Iterable[LedgerSpan]) -> list[ComparisonExit]:
    """Every act that ran the line off its declared comparison, oldest first."""
    return [
        act for span in spans for act in LedgerIndex.of(span.path).view(_LeftComparison, span.until)
    ]


def scan_launch_clocks(spans: Iterable[LedgerSpan]) -> list[LaunchClockRecord]:
    return [
        clock
        for span in spans
        for clock in LedgerIndex.of(span.path).view(_LaunchClocks, span.until)
    ]


def scan_campaign_ceiling(root_ledger: Path) -> Budget:
    """The campaign's money axes, off its ROOT's ledger, which every cycle of it reads; none names none."""
    held = scan_last([LedgerSpan(root_ledger)], CampaignCeilingRecord)
    return Budget() if held is None else held.budget


def scan_cycle_hold(ledger_path: Path) -> CycleHold | None:
    return _view(ledger_path, _CycleHold)


def scan_bench_passes(spans: Iterable[LedgerSpan]) -> BenchPasses | None:
    held: BenchPasses | None = None
    for view in _graded(spans, MeasurementRole.BENCH):
        taken = view.verification
        if view.subject == "selected":
            if held is not None:
                held = held.model_copy(
                    update={"selections": {**held.selections, taken.arm.key: taken}}
                )
        elif held is None or held.origin != taken:
            # A different origin pass is a new reference: no earlier selection pairs on it.
            held = BenchPasses(
                tolerance=view.tolerance or 0,
                origin=taken,
                reserve_usd=view.reserve_usd or 0.0,
                reserve_tokens=view.reserve_tokens or 0,
                selections={},
            )
    return held


def scan_bench_readings(spans: Iterable[LedgerSpan]) -> dict[tuple[int, ArmKey], BenchReading]:
    return {
        (view.reading.round, view.verification.arm.key): view.reading
        for view in _graded(spans, MeasurementRole.BENCH)
        if isinstance(view.reading, BenchReading)
    }


def scan_bench_open(ledger_path: Path) -> bool:
    """Own ledger only."""
    return _view(ledger_path, _BenchOpen)


PROGRESS_FOLDS = (_Progress,)


def scan_cycle_facts(ledger_path: Path, until: int | None = None) -> CycleFacts:
    return LedgerIndex.of(ledger_path).view(_CycleFacts, until)


def scan_cell_formula(spans: Iterable[LedgerSpan]) -> str | None:
    formula: str | None = None
    for span in spans:
        held = LedgerIndex.of(span.path).view(_CycleFacts, span.until)
        formula = held.formula or formula
    return formula


def scan_ledger_progress(ledger_path: Path) -> Progress:
    return LineIndex.of(ledger_path, PROGRESS_FOLDS).view(_Progress)


def scan_standing_rounds(spans: Iterable[LedgerSpan]) -> StandingRounds:
    standing = StandingRounds.none()
    for span in spans:
        standing = standing.then(LedgerIndex.of(span.path).view(_Standing, span.until))
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
    walks += [view.verification.walk() for view in _graded(chain, MeasurementRole.VERIFY)]
    return walks


_Span = tuple[float, float]
_CallSpan = tuple[str, str, float, float]


def scan_ledger_verify(ledger_path: Path) -> VerifyLedger:
    """Own ledger only: a fork inherits its parent's measurements, not its assurance."""
    graded: dict[ArmKey, tuple[VerificationPass, VerifyReading]] = {}
    passes: list[VerificationPass] = []
    for view in _graded([LedgerSpan(ledger_path)], MeasurementRole.VERIFY):
        # A refused pass sent nothing: it verified no round.
        if isinstance(view.reading, VerifyReading):
            graded[view.verification.arm.key] = view.verification, view.reading
            passes.append(view.verification)
    return VerifyLedger(graded, passes)


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
    held = [LedgerIndex.of(span.path).view(_Answers, span.until) for span in spans]
    return held[0] if len(held) == 1 else frozenset().union(*held)


def scan_ledger_priced_keys(ledger_paths: Iterable[Path]) -> set[str]:
    return {key for path in ledger_paths for key in _view(path, _PricedKeys)}


def scan_ledger_spend(spans: Iterable[LedgerSpan]) -> LedgerSpend:
    spend = SpendRollup()
    calls, worked_s = 0, 0.0
    for span in spans:
        own = money_on(span.path, span.until)
        for held in own.by_round.values():
            spend.absorb(held)
        calls += own.calls
        worked_s += own.worked_s
    return LedgerSpend(spend, calls, worked_s)


def scan_ledger_metered(ledger_path: Path) -> dict[CeilingMeter, Budget]:
    """This ledger's OWN appends, never a fork's walked prefix, in every ceiling meter."""
    return money_on(ledger_path).metered


def scan_ledger_spend_by_round(spans: Iterable[LedgerSpan]) -> dict[int, SpendRollup]:
    """Not clamped by a rewind: a round measured twice cost money both times."""
    by_round: dict[int, SpendRollup] = {}
    for span in spans:
        own = money_on(span.path, span.until)
        for n, held in own.by_round.items():
            spend_slot(by_round, n, SpendRollup).absorb(held)
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
