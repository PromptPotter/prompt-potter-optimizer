from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, NamedTuple, assert_never, get_args

from promptpotter.domain.arms import ArmState, Cut, Measured, Skipped
from promptpotter.domain.bench import BenchReading, BenchScore
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.cycle_index import Intervention
from promptpotter.domain.cycle_result import WallClock
from promptpotter.domain.fork import ForkSpec
from promptpotter.domain.narration import Narration
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pass_kinds import IndividualWalk
from promptpotter.domain.phase_acts import InitClosed, InitOpened
from promptpotter.domain.phases import CampaignPhase, StopReason
from promptpotter.domain.pipeline_schema import CapabilityMenu
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.records.decision import DecisionRecord
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.fold_basis import FoldBasisRecord
from promptpotter.domain.records.held_out import BenchScoredRecord, PassGradedRecord
from promptpotter.domain.records.level_series import LevelSeriesRecord
from promptpotter.domain.records.lineage import (
    BABYSAT,
    ArmAct,
    ArmActRecord,
    CycleFinalRecord,
    CycleMintedRecord,
    CycleSupersededRecord,
    LaunchClockRecord,
    SpawnedBy,
    SpawnedRecord,
)
from promptpotter.domain.records.money import (
    CampaignCeilingRecord,
    CycleShareRecord,
    PricedKeyRecord,
    StandingBudgetRecord,
    TokenUsageRecord,
)
from promptpotter.domain.records.partition import PartitionRecord
from promptpotter.domain.records.rounds import (
    ArmRecord,
    ElectionRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundProposedRecord,
    RoundStatement,
)
from promptpotter.domain.records.run_phase import (
    ArmsGate,
    Ended,
    Paused,
    PhaseRecord,
    RunPhaseRecord,
)
from promptpotter.domain.records.run_wiring import (
    PipelineRead,
    ResolvedRunRecord,
    RunWiringRecord,
    ScoringLockedRecord,
)
from promptpotter.domain.records.sample_passes import RaceCatchUpRecord, SampleScoredRecord
from promptpotter.domain.results import round_walks
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import WalkedCell
from promptpotter.domain.spend import Budget, spend_slot
from promptpotter.domain.spend_rollup import SpendRollup
from promptpotter.domain.verification import (
    BenchPass,
    BenchPasses,
    VerificationPass,
    VerifyReading,
)
from promptpotter.infrastructure.llm.money_fold import Money, MoneyFold
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import (
    LastOf,
    LedgerIndex,
    LedgerSpan,
    Line,
    RecordClasses,
    last,
    line_concat,
    line_union,
    read_line,
)
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.measurement_context import MeasurementRole


@dataclass(frozen=True)
class ResolvedRun:
    read: PipelineRead | None = None
    experiment: dict[str, Any] | None = None
    # The cycle whose ledger landed the roster that stands.
    experiment_cycle_id: str | None = None
    model_capabilities: CapabilityMenu = field(default_factory=dict)
    # The LATEST resolve's moved roster (`ResolvedRunRecord.moved_panel`); a span resolving nothing keeps it.
    moved_panel: dict[str, Any] | None = None

    @classmethod
    def of(cls, record: ResolvedRunRecord) -> ResolvedRun:
        return cls(
            record.read,
            record.experiment,
            model_capabilities=record.model_capabilities,
            moved_panel=record.moved_panel,
        )

    def then(self, later: ResolvedRun) -> ResolvedRun:
        first = self if self.experiment else later
        latest = self if later.read is None else later
        return ResolvedRun(
            read=latest.read,
            experiment=first.experiment,
            experiment_cycle_id=first.experiment_cycle_id,
            model_capabilities=later.model_capabilities or self.model_capabilities,
            moved_panel=latest.moved_panel,
        )


class _ResolvedRun:
    records: ClassVar[RecordClasses] = (ResolvedRunRecord,)

    def __init__(self) -> None:
        self._found = ResolvedRun()

    def feed(self, record: ResolvedRunRecord) -> None:
        self._found = self._found.then(ResolvedRun.of(record))

    def value(self) -> ResolvedRun:
        return self._found


class _ArmActs:
    records: ClassVar[RecordClasses] = (ArmActRecord,)

    def __init__(self) -> None:
        self._found: list[ArmAct] = []

    def feed(self, record: ArmActRecord) -> None:
        self._found.append(record.act)

    def value(self) -> list[ArmAct]:
        return list(self._found)


class _LaunchClocks:
    records: ClassVar[RecordClasses] = (LaunchClockRecord,)

    def __init__(self) -> None:
        self._found: list[LaunchClockRecord] = []

    def feed(self, record: LaunchClockRecord) -> None:
        self._found.append(record)

    def value(self) -> list[LaunchClockRecord]:
        return list(self._found)


class StandingBudgetFold:
    records: ClassVar[RecordClasses] = (StandingBudgetRecord,)

    def __init__(self) -> None:
        # No record stands no axis, which the empty record says.
        self._standing = StandingBudgetRecord()

    def feed(self, record: StandingBudgetRecord) -> None:
        self._standing = self._standing.over(record)

    def value(self) -> StandingBudgetRecord:
        return self._standing


class CycleFacts(NamedTuple):
    minted: CycleMintedRecord | None = None
    ended: Ended | None = None
    ended_at: str | None = None
    # The ending the standing launch retired; a pause or an ending of its own clears it.
    resumed_ending: StopReason | None = None
    final: CycleFinalRecord | None = None
    superseded_by: str | None = None
    error: ErrorRecord | None = None
    interventions: tuple[Intervention, ...] = ()
    spawned_by: SpawnedBy | None = None
    formula: str | None = None
    updated_at: str = ""

    @property
    def fork(self) -> ForkSpec | None:
        return None if self.minted is None else self.minted.fork


_CycleFact = (
    CycleMintedRecord
    | CycleFinalRecord
    | CycleSupersededRecord
    | ArmActRecord
    | SpawnedRecord
    | RunPhaseRecord
    | PhaseRecord
    | RoundClosedRecord
    | ErrorRecord
)


def _init_formula(record: PhaseRecord) -> str | None:
    match record.act:
        case InitOpened(formula=formula) | InitClosed(formula=formula):
            return formula.formula
    return None


class CycleFactsFold:
    records: ClassVar[RecordClasses] = get_args(_CycleFact)

    def __init__(self) -> None:
        self._held = CycleFacts()

    def feed(self, record: _CycleFact) -> None:
        held = self._held
        match record:
            case CycleMintedRecord():
                # The babysat stamp is the mint's own: an intervention, never a second append.
                stamped = (Intervention(kind=BABYSAT, at=record.timestamp),)
                held = held._replace(
                    minted=record,
                    interventions=(*held.interventions, *(stamped if record.babysat else ())),
                )
            case RunPhaseRecord(state=Ended() as ended):
                held = held._replace(ended=ended, ended_at=record.timestamp, resumed_ending=None)
            case RunPhaseRecord():
                paused = isinstance(record.state, Paused)
                held = held._replace(
                    ended=None,
                    ended_at=None,
                    resumed_ending=None
                    if paused
                    else held.ended.stop_reason
                    if held.ended is not None
                    else held.resumed_ending,
                    final=None,
                    # A fork's pause of its parent lands after the parent is named superseded.
                    superseded_by=held.superseded_by if paused else None,
                    error=None,
                )
            case ErrorRecord():
                held = held._replace(error=record)
            case CycleFinalRecord():
                held = held._replace(final=record)
            case CycleSupersededRecord():
                held = held._replace(superseded_by=record.successor_cycle_id)
            case ArmActRecord():
                if not record.act.babysits:
                    return
                act = Intervention(kind=record.act.value, at=record.timestamp)
                held = held._replace(interventions=(*held.interventions, act))
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


class StandingArm(NamedTuple):
    """One arm of one round as its records left it; ``fold`` is `Narration.fold`."""

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
    # The δ scale's reading of the line at each close, banked beside it.
    series: dict[int, LevelSeries]
    # Keyed on the record's round STAMP, never ledger position: a round can close more than once.
    decisions: dict[int, list[DecisionRecord]]
    # The cells an operator's skip left each arm at: it stands as its proposal does, past a re-entry.
    skipped: dict[tuple[int, ArmKey], int]
    # The open round's rows in the order its walks read them, which an ``arrival`` resume replays
    # (`query_loop.py::_ScoringPhase.filed_reads`):
    # it stands past a re-entry, a leg that re-reads it extending it.
    reads: dict[int, list[tuple[ArmKey, int]]]
    entered: int | None = None
    rewound: int | None = None

    @classmethod
    def none(cls) -> StandingRounds:
        return cls({}, {}, {}, {}, {}, {}, {}, {}, {}, {})

    def displaced(self, entered: int | None, rewound: int | None) -> StandingRounds:
        return self._replace(
            rounds=_before(self.rounds, entered),
            proposals=_before(self.proposals, rewound),
            submitted=_before(self.submitted, rewound),
            arms=_before(self.arms, entered),
            passes=_before(self.passes, entered),
            elections=_before(self.elections, entered),
            series=_before(self.series, entered),
            decisions=_before(self.decisions, entered),
            skipped=_before(self.skipped, rewound),
            reads=_before(self.reads, rewound),
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
            series={**prefix.series, **later.series},
            decisions=prefix.decisions,
            skipped={**prefix.skipped, **later.skipped},
            reads={
                **prefix.reads,
                **{n: _reread(prefix.reads.get(n, []), leg) for n, leg in later.reads.items()},
            },
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
        return self.series[max(self.rounds)] if self.rounds else None

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


def _reread[T](held: list[T], leg: list[T]) -> list[T]:
    """A leg that re-reads the order held so far, stopped short of its end, extends nothing."""
    return held if leg == held[: len(leg)] else leg


def _take(taken: dict[str, WalkedCell], cell: WalkedCell) -> None:
    # Popped first: the cell moves to where the walk last read it.
    taken.pop(cell[0], None)
    taken[cell[0]] = cell


_RoundStep = (
    CycleMintedRecord
    | RoundEnteredRecord
    | ElectionRecord
    | LevelSeriesRecord
    | DecisionRecord
    | RoundClosedRecord
    | RoundProposedRecord
    | ArmRecord
    | SampleScoredRecord
    | FoldBasisRecord
)


class _StandingFold:
    records: ClassVar[RecordClasses] = get_args(_RoundStep)

    def __init__(self) -> None:
        self.at_offset = -1
        self._held = StandingRounds.none()
        self._arms = Narration()
        self._cells: dict[tuple[int, ArmKey], dict[str, WalkedCell]] = {}
        # Below the round last entered, a value shares an arm's cells: a later cell copies them first.
        self._live: int | None = None
        # Each closed round's arms as its close left them, built once: a later record of one thaws it.
        self._frozen: dict[tuple[int, ArmKey], StandingArm] = {}
        # How far the live leg has re-read its round's read order.
        self._reread = 0

    def feed(self, record: _RoundStep) -> None:
        if isinstance(record, CycleMintedRecord):
            # A fork's mint is its rewound entry at its cut: the rounds below are its parent line's.
            self._held = self._held._replace(entered=record.cut_round, rewound=record.cut_round)
            return
        if isinstance(record, FoldBasisRecord):
            self._arms.read(self.at_offset, record)
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
                self._frozen = _before(self._frozen, rnd)
                self._live = rnd
                self._reread = 0
            case ElectionRecord():
                held.elections[rnd] = record
            case LevelSeriesRecord():
                held.series[rnd] = record.series
            case DecisionRecord():
                held.decisions.setdefault(rnd, []).append(record)
            case RoundClosedRecord():
                self._arms.close(record, record.arms)
                # A restatement is its round WHOLE, so the ledger holding it owns every arm it never walked.
                for arm_key in record.arms:
                    walked = record.cells.get(arm_key, ())
                    self._cells.setdefault((rnd, arm_key), {c[0]: c for c in walked})
                self._freeze(rnd, record.arms)
                held.rounds[rnd] = StandingRound(record, offset)
                held.reads.pop(rnd, None)
            case RoundProposedRecord() if record.submitted:
                held.submitted[rnd] = record
            case RoundProposedRecord():
                held.proposals[rnd] = StandingProposal(record, offset)
            case ArmRecord():
                self._frozen.pop((rnd, record.arm), None)
                self._arms.announce(record)
                self._cells.setdefault((rnd, record.arm), {})
                match record.state:
                    case Cut(reason=Skipped()):
                        fold = self._arms.fold(rnd, record.arm)
                        held.skipped[rnd, record.arm] = 0 if fold is None else fold.walked
                    case Cut() | Measured():
                        held.skipped.pop((rnd, record.arm), None)
            case SampleScoredRecord():
                # The arm's fold moved, so its frozen standing goes whether or not a cell was walked.
                if (walking := self._arms.take(record)) is not None:
                    self._frozen.pop((rnd, walking), None)
                    self._read(held.reads.setdefault(rnd, []), (walking, record.position))
                if (cell := _walked_cell(record)) is None:
                    return
                if walking is not None:
                    taken = self._cells[rnd, walking]
                    if self._shared(rnd):
                        self._cells[rnd, walking] = taken = dict(taken)
                    _take(taken, cell)
                else:
                    _take(
                        held.passes.setdefault((rnd, record.individual_id, record.role), {}), cell
                    )
            case _:
                assert_never(record)

    def _read(self, order: list[tuple[ArmKey, int]], call: tuple[ArmKey, int]) -> None:
        if order[self._reread : self._reread + 1] != [call]:
            del order[self._reread :]
            order.append(call)
        self._reread += 1

    def _shared(self, rnd: int) -> bool:
        return self._live is not None and rnd < self._live

    def _standing_arm(self, rnd: int, key: ArmKey, *, copied: bool) -> StandingArm:
        cells = self._cells[rnd, key]
        return StandingArm(
            self._arms.state(rnd, key), dict(cells) if copied else cells, self._arms.fold(rnd, key)
        )

    def _freeze(self, rnd: int, keys: Iterable[ArmKey]) -> None:
        # Its cells copied once: the live round keeps walking its own dict past the close.
        for key in keys:
            self._frozen[rnd, key] = self._standing_arm(rnd, key, copied=True)

    def value(self) -> StandingRounds:
        """The aggregate plus the live round: a closed round's arms stand frozen at its close."""
        held, frozen = self._held, self._frozen
        return held._replace(
            rounds=dict(held.rounds),
            proposals=dict(held.proposals),
            submitted=dict(held.submitted),
            arms={
                at: frozen[at]
                if at in frozen
                else self._standing_arm(*at, copied=not self._shared(at[0]))
                for at in self._arms
            },
            passes={k: dict(taken) for k, taken in held.passes.items()},
            elections=dict(held.elections),
            series=dict(held.series),
            decisions={n: list(made) for n, made in held.decisions.items()},
            skipped=dict(held.skipped),
            reads={n: list(order) for n, order in held.reads.items()},
        )


_MOVES_ROUNDS = (RoundEnteredRecord, RoundClosedRecord)
assert set(_MOVES_ROUNDS) <= set(_StandingFold.records)


def moves_index(record: CycleRecord) -> bool:
    """Whether *record* moves a cycle's index (`store.py::fold_cycle_index`): a fact its fold takes, or a round that stands."""
    if isinstance(record, PhaseRecord):
        return _init_formula(record) is not None
    return isinstance(record, (*CycleFactsFold.records, *_MOVES_ROUNDS))


_Naming = SampleScoredRecord | RoundClosedRecord | RaceCatchUpRecord | PassGradedRecord


class _Answers:
    """Every answer a ledger names — a slotted walk's, a close's rows, a catch-up's, a verify
    pass's — displaced rounds included: what a campaign has walked, read through the extractors
    `line_walks` reads what STANDS through, so memory holds all evidence."""

    records: ClassVar[RecordClasses] = get_args(_Naming)

    def __init__(self) -> None:
        self._found: set[str] = set()
        self._frozen: frozenset[str] | None = frozenset()

    def feed(self, record: _Naming) -> None:
        match record:
            case SampleScoredRecord():
                walked = [] if (cell := _walked_cell(record)) is None else [cell]
            case RoundClosedRecord():
                walks = record.walks()
                walked = [cell for walk in walks for cell in walk.cells]
            case RaceCatchUpRecord():
                walked = list(record.cells.values())
            case PassGradedRecord(verification=banked):
                walked = banked.cells
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

    records: ClassVar[RecordClasses] = (PassGradedRecord,)

    def __init__(self) -> None:
        self._graded: list[PassGradedRecord] = []

    def feed(self, record: PassGradedRecord) -> None:
        self._graded.append(record)

    def value(self) -> list[PassGradedRecord]:
        return list(self._graded)


class ClosedBenchFold:
    """The last bench a run declared: its close's, which carries the run's own word on its ending."""

    records: ClassVar[RecordClasses] = (BenchScoredRecord,)

    def __init__(self) -> None:
        self._bench: BenchScore | None = None

    def feed(self, record: BenchScoredRecord) -> None:
        self._bench = record.bench

    def value(self) -> BenchScore | None:
        return self._bench


class LedgerSpend(NamedTuple):
    spend: SpendRollup
    calls: int
    worked_s: float


class BenchOpenFold:
    """A bench pass's bracket: entered and not yet exited, the dashboard's running bench mark."""

    records: ClassVar[RecordClasses] = (PhaseRecord,)

    def __init__(self) -> None:
        self._open = False

    def feed(self, record: PhaseRecord) -> None:
        if record.phase == CampaignPhase.BENCH:
            self._open = record.step == "enter"

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
                held.phases.append((at, record.phase, record.round, record.step))
            case RunPhaseRecord():
                held.gates.append((at, isinstance(record.state, ArmsGate)))
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
    LastOf(CampaignCeilingRecord),
    LastOf(CycleMintedRecord),
    LastOf(CycleShareRecord),
    LastOf(PartitionRecord),
    LastOf(RunPhaseRecord),
    LastOf(RunWiringRecord),
    LastOf(ScoringLockedRecord),
    _ResolvedRun,
    _ArmActs,
    _LaunchClocks,
    StandingBudgetFold,
    CycleFactsFold,
    _StandingFold,
    _Answers,
    _Verification,
    BenchOpenFold,
    _PricedKeys,
    _Timeline,
)


def _resolved_then(held: ResolvedRun, own: ResolvedRun, span: LedgerSpan) -> ResolvedRun:
    if own.experiment is not None:
        own = replace(own, experiment_cycle_id=CycleLayout.of_ledger(span.path).cycle_dir.name)
    return held.then(own)


RESOLVED_RUN = Line(_ResolvedRun, ResolvedRun, _resolved_then)
# Every act that ran the line off its declared comparison, oldest first.
ARM_ACTS = line_concat(_ArmActs)
LAUNCH_CLOCKS = line_concat(_LaunchClocks)
# The campaign's money axes, off its ROOT's ledger, which every cycle of it reads; none names none.
CAMPAIGN_CEILING: Line[CampaignCeilingRecord | None, CampaignCeilingRecord] = Line(
    last(CampaignCeilingRecord).fold,
    lambda: CampaignCeilingRecord(budget=Budget(), asked=Budget()),
    lambda held, own, _: held if own is None else own,
)
GRADED_PASSES = line_concat(_Verification)


def _bench_then(
    held: BenchPasses | None, own: list[PassGradedRecord], _: LedgerSpan
) -> BenchPasses | None:
    for view in own:
        taken = view.verification
        if taken.kind != MeasurementRole.BENCH or not isinstance(taken, BenchPass):
            continue
        if view.subject == "selected":
            if held is not None:
                held = held.with_selection(taken, view.refusal)
        elif held is None or held.origin != taken:
            # A different origin pass is a new reference: no earlier selection pairs on it.
            held = BenchPasses(
                tolerance=view.tolerance or 0,
                origin=taken,
                reserve_usd=view.reserve_usd or 0.0,
                reserve_tokens=view.reserve_tokens or 0,
                selections={},
                refused={},
            )
    return held


BENCH_PASSES: Line[list[PassGradedRecord], BenchPasses | None] = Line(
    _Verification, lambda: None, _bench_then
)
BENCH_READINGS: Line[list[PassGradedRecord], dict[tuple[int, ArmKey], BenchReading]] = Line(
    _Verification,
    dict,
    lambda held, own, _: (
        held
        | {
            (view.reading.round, view.verification.arm.key): view.reading
            for view in own
            if view.verification.kind == MeasurementRole.BENCH
            and isinstance(view.reading, BenchReading)
        }
    ),
)
CELL_FORMULA: Line[CycleFacts, str | None] = Line(
    CycleFactsFold, lambda: None, lambda held, own, _: own.formula or held
)
STANDING_ROUNDS: Line[StandingRounds, StandingRounds] = Line(
    _StandingFold, StandingRounds.none, lambda held, own, _: held.then(own)
)


def line_walks(spans: Iterable[LedgerSpan]) -> list[IndividualWalk[WalkedCell]]:
    chain = list(spans)
    standing = read_line(chain, STANDING_ROUNDS)
    closed = standing.rounds
    walks = [walk for held in closed.values() for walk in held.close.walks()]
    # Per round: one individual walked in two open rounds is two walks.
    open_rounds: dict[int, dict[ArmKey, list[WalkedCell]]] = {}
    for (round_num, key), fold in standing.arms.items():
        if round_num not in closed and fold.cells:
            open_rounds.setdefault(round_num, {})[key] = list(fold.cells.values())
    walks += [walk for arms in open_rounds.values() for walk in round_walks(arms, {})]
    walks += [
        IndividualWalk(individual_id, frozenset({role}), list(taken.values()))
        for (round_num, individual_id, role), taken in standing.passes.items()
        if round_num not in closed
    ]
    # The whole chain: a fork inherits its parent's verify CELLS, though not its assurance.
    walks += [
        view.verification.walk()
        for view in read_line(chain, GRADED_PASSES)
        if view.verification.kind == MeasurementRole.VERIFY
    ]
    return walks


_Span = tuple[float, float]
_CallSpan = tuple[str, str, float, float]


def _verify_then(held: VerifyLedger, own: list[PassGradedRecord], _: LedgerSpan) -> VerifyLedger:
    for view in own:
        # A refused pass sent nothing: it verified no round.
        if view.verification.kind == MeasurementRole.VERIFY and isinstance(
            view.reading, VerifyReading
        ):
            held.graded[view.verification.arm.key] = view.verification, view.reading
            held.passes.append(view.verification)
    return held


# Read on a cycle's own ledger: a fork inherits its parent's measurements, not its assurance.
VERIFY_LEDGER: Line[list[PassGradedRecord], VerifyLedger] = Line(
    _Verification, lambda: VerifyLedger({}, []), _verify_then
)


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


WALKED_ANSWERS = line_union(_Answers)
PRICED_KEYS = line_union(_PricedKeys)


def _spend_then(held: LedgerSpend, own: Money, _: LedgerSpan) -> LedgerSpend:
    for rollup in own.by_round.values():
        held.spend.absorb(rollup)
    return LedgerSpend(held.spend, held.calls + own.calls, held.worked_s + own.worked_s)


LEDGER_SPEND: Line[Money, LedgerSpend] = Line(
    MoneyFold, lambda: LedgerSpend(SpendRollup(), 0, 0.0), _spend_then
)


def _by_round_then(
    held: dict[int, SpendRollup], own: Money, _: LedgerSpan
) -> dict[int, SpendRollup]:
    for n, rollup in own.by_round.items():
        spend_slot(held, n, SpendRollup).absorb(rollup)
    return dict(sorted(held.items()))


# Not clamped by a rewind: a round measured twice cost money both times.
SPEND_BY_ROUND: Line[Money, dict[int, SpendRollup]] = Line(MoneyFold, dict, _by_round_then)
TIMELINES: Line[Timeline, list[Timeline]] = Line(_Timeline, list, lambda held, own, _: [*held, own])


def wall_clock_of(
    timelines: list[Timeline],
    *,
    started_at: str,
    finished_at: str,
    optimizer_phases: frozenset[str],
) -> WallClock:
    """The endpoints are the RUNNER's: the ledger's first record is already past ``init_services``."""
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
