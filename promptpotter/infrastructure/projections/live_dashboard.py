from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, ClassVar, NamedTuple

from pydantic import ConfigDict, Field

from promptpotter.domain.arm_reading import (
    ArmElection,
    ArmReading,
    DisplayMetric,
    panel_counts,
    panel_cuts,
)
from promptpotter.domain.arms import Concluded, arm_name
from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.bench import BenchReading
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.cycle_paths import CycleAt, CycleDir, CycleHop
from promptpotter.domain.dashboard_rows import (
    DashboardSample,
    LiveCandidate,
    RoundSummary,
    RunLimits,
    VerificationMark,
    sample_status,
)
from promptpotter.domain.fitness_fold import level_band
from promptpotter.domain.narration import Narration
from promptpotter.domain.paired_reading import ArmKey, ArmName
from promptpotter.domain.phase_acts import (
    InitClosed,
    InitOpened,
    PassOpened,
    ProposeOpened,
    StepOpened,
)
from promptpotter.domain.phases import CampaignPhase, DashboardState, RunPhase, StopReason
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.records.cell_beat import CellBeatRecord
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.fold_basis import FoldBasisRecord
from promptpotter.domain.records.held_out import BenchScoredRecord, PassGradedRecord
from promptpotter.domain.records.level_series import LevelSeriesRecord
from promptpotter.domain.records.lineage import CycleMintedRecord
from promptpotter.domain.records.llm_calls import (
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
)
from promptpotter.domain.records.money import StandingBudgetRecord
from promptpotter.domain.records.provider import (
    BackendWarningRecord,
    FlightRecord,
    FlightWaiting,
    ProviderEasedRecord,
    ProviderHoldRecord,
)
from promptpotter.domain.records.rounds import (
    ArmRecord,
    ElectionRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
)
from promptpotter.domain.records.run_phase import Ended, PhaseRecord, RunPhaseRecord
from promptpotter.domain.records.run_wiring import BackendRevision, DefaultsDrift, RunWiringRecord
from promptpotter.domain.records.sample_passes import (
    RaceCatchUpRecord,
    SampleScoredRecord,
    SampleStartedRecord,
)
from promptpotter.domain.results import Election
from promptpotter.domain.round_audit import NodeBlock
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import (
    Grade,
    GradedCell,
    MeasuredCell,
    SampleStatus,
    graded_answer,
    ground_truth_text,
    is_verifier_graded,
)
from promptpotter.domain.spend import TokenAccount
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.verification import NoiseBand, VerifyReading
from promptpotter.infrastructure.projections.audit_trail import build_node_block
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import (
    LedgerFold,
    LedgerIndex,
    LedgerSpan,
    Projection,
    fork_chain,
)
from promptpotter.shared.measurement_context import MeasurementRole


def _trim(text: str, n: int) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def sample_row(s: WalkedSample) -> DashboardSample:
    facts, grade = s.facts, s.grade
    cost_s = facts.cost_s
    status: SampleStatus = sample_status(facts, grade)
    # A verifier-graded row's `predicted` is the `NO_RESULT` sentinel: served EMPTY, not as an answer.
    ground_truth = _trim(facts.ground_truth, 20)
    graded_by_verifier = is_verifier_graded(ground_truth)
    account = TokenAccount.from_step_tokens(facts.pipeline.step_tokens)
    return DashboardSample(
        position=s.position,
        sample_key=facts.sample_key,
        status=status,
        fitness=grade.fitness,
        terminal_node=facts.pipeline.terminal_node,
        cached=facts.cached,
        cost_s=None if cost_s is None else round(cost_s, 2),
        predicted="" if graded_by_verifier else _trim(graded_answer(facts, grade) or "", 28),
        ground_truth=ground_truth,
        ground_truth_text=ground_truth_text(ground_truth),
        query=_trim(facts.query, 42),
        input_tokens=account.input if account else None,
        output_tokens=account.output if account else None,
        cache_read_tokens=account.cache_read if account else None,
    )


def build_candidate_rows(
    narration: Narration,
    round_num: int,
    samples: dict[ArmKey, list[WalkedSample]],
    election: Election | None,
    open_cells: dict[ArmKey, list[int]],
) -> list[LiveCandidate]:
    """The narration's live round: each arm it announced at *round_num*, with the cells it walked."""
    arms = [
        (key, arm, narration.fold(round_num, key))
        for key in narration.announced(round_num)
        if (arm := narration.state(round_num, key)).role.is_proposal
    ]
    cuts = panel_cuts([panel_counts(arm, fold) for _, arm, fold in arms])
    rows: list[LiveCandidate] = []
    for (key, arm, fold), cut in zip(arms, cuts, strict=True):
        reading = ArmReading.of(
            arm_name(key, arm, round_num),
            arm,
            fold=fold,
            cut=cut,
            election=ArmElection.of(
                key,
                held=election is not None,
                selected=None if election is None else election.selected,
                leading=None if election is None else election.leading,
                electable=None if election is None else election.electable,
            ),
            vs_reference=None if election is None else election.vs_reference.get(key),
        )
        rows.append(
            LiveCandidate.in_flight(
                reading,
                arm,
                samples=[sample_row(s) for s in samples.get(key, ())],
                open_cells=open_cells.get(key, []),
            )
        )
    return rows


class CatchUpLogEntry(StrictModel):
    """One race catch-up: the priors eliminator ``member`` re-measured on one sample."""

    member: str
    round: int
    arm: ArmKey
    # The arm's label as the round names it.
    label: str
    position: int
    prior_keys: list[ArmKey]


class BackendWarning(StrictModel):
    """A backend transport or 5xx retry; a 429 is ``backpressure``, never one of these."""

    ts: str
    kind: str
    attempt: int | None = None
    max_attempts: int | None = None
    wait_s: float | None = None
    error_class: str | None = None
    status_code: int | None = None
    final: bool = False
    query: str | None = None
    detail: str | None = None


class WaitingOn(StrictModel):
    """The cell the producer's open call holds, worded once by `served_dashboard.py::held_call`."""

    label: str | None = Field(description="The owning arm's label; null for a pass no arm owns.")
    position: int


class ActiveNode(StrictModel):
    """The optimizer's node running now: a target's calls land on its own ledger, never this one."""

    node: str


class CurrentRound(StrictModel):
    """The round in flight, rebuilt whole on every persist."""

    round: int = 0
    measurement_node: str = Field(
        description="The optimizer node every scoring pass of the run runs as — origin, round, "
        "bench and verify alike — as its wiring declares it."
    )
    inner: CycleHop | None = Field(
        default=None,
        description="The inner cycle an open cell runs, where a cell is a whole campaign (L4), "
        "in this cycle's sandbox; null where no open cell has named one.",
    )
    candidates: list[LiveCandidate] = Field(default_factory=list)
    nodes: dict[str, NodeBlock] = Field(default_factory=dict)


class DashboardFacts(StrictModel):
    model_config = ConfigDict(validate_assignment=False)

    # ``-1`` until the first record lands; the SSE snapshot parks its tail one past it.
    at_offset: int = -1

    state: DashboardState = DashboardState.INIT

    stop_reason: StopReason | None = None

    round: int = 0

    rounds: list[RoundSummary] = Field(default_factory=list)

    verification: list[VerificationMark] = Field(
        default_factory=list,
        description="Each arm's latest verify and held-out bench pass on this cycle's ledger, "
        "in flight or ended; its rows are no round's cells.",
    )
    composite_fitness_formula: str | None = None
    display_metric: DisplayMetric

    recent_backend_warnings: list[BackendWarning] = Field(default_factory=list)

    current_query_payload: str | None = None

    backpressure: BackpressureReading | None = None
    measured_unit: MeasuredUnit

    round_cells_ceiling: int

    defaults_drift: DefaultsDrift | None = Field(
        default=None,
        description="Set where the bench defaults or the treatment the campaign's declaration is a "
        "delta over moved since it was declared, under an unchanged origin: this launch runs "
        "today's values.",
    )
    backend_revision: BackendRevision | None = Field(
        default=None,
        description="What this launch read of the backend's revision beside the one its connector "
        "expects; `None` where the connector checks none. A drift is served, never refused.",
    )

    catch_up_log: list[CatchUpLogEntry] = Field(default_factory=list)

    current_round: CurrentRound


class LiveDashboardState(DashboardFacts):
    # Says "running" forever after a kill: the answer is ``ServedDashboard.run_phase``.
    declared_phase: RunPhase = RunPhase.RUNNING
    optimizer_step: str | None = None  # served only as `ServedDashboard.pause_waits_on`
    # A FAILED stop's failure, else absent: served only as the run's pinned `said` row.
    error: ErrorRecord | None = None
    # The cycle entry serves both (`CycleEntryFacts.standing` / `elects_on`); the file keeps them.
    run_standing: RunStanding | None = None
    elects_on: DisplayMetric
    # Served only as `ServedDashboard.active`, where the cycle is appending.
    active: ActiveNode | None = None
    # The cell the open call holds; served only as `ServedDashboard.held_call`.
    waiting_on: WaitingOn | None = None

    # The last close's; served only as each `ServedRound`'s readings of it.
    level_series: LevelSeries | None = None
    waiting_since: float | None = None

    # The look-ahead gauge, served only as `ServedDashboard.lookahead`.
    sample_lookahead_discards: int = 0
    in_flight: int = 0
    lookahead_allowed: int = 0
    # Between phases it is the NEXT round's.
    lookahead_most: int = 0
    # ``None``: no book binds.
    lookahead_affordable: int | None = None
    cell_reserve_usd: float | None = None
    # `1` says the look-ahead control does not apply.
    max_cells_in_flight: int

    def orphaned(self, spare: MeasurementRole | None = None) -> list[VerificationMark]:
        """The passes, each still running and not of kind *spare* ended stopped: its process is gone ungraded."""
        return [
            mark.orphaned() if mark.state == "running" and mark.kind != spare else mark
            for mark in self.verification
        ]

    def unfiled(self) -> int | None:
        """`current_round` while no round file holds it: the one answer every served read asks."""
        current = self.current_round.round
        return None if any(r.round == current for r in self.rounds) else current

    def round_closed(self) -> bool:
        """`current_round` lingers after its close: closed once `rounds` holds it with rows."""
        return any(r.round == self.current_round.round and r.candidates for r in self.rounds)

    @classmethod
    def declared(cls, wiring: RunWiringRecord) -> LiveDashboardState:
        return cls(
            current_round=CurrentRound(measurement_node=wiring.measurement_node),
            **cls.launched(wiring),
        )

    @staticmethod
    def launched(wiring: RunWiringRecord) -> dict[str, Any]:
        """What each launch's wiring declares, and clears of what the last launch left."""
        return {
            "round_cells_ceiling": wiring.round_cells_ceiling,
            "display_metric": wiring.display_metric,
            "elects_on": wiring.elects_on,
            "max_cells_in_flight": wiring.max_cells_in_flight,
            "measured_unit": wiring.measured_unit,
            "defaults_drift": wiring.defaults_drift,
            "backend_revision": wiring.backend_revision,
            "state": DashboardState.INIT,
            "optimizer_step": None,
            "stop_reason": None,
            "error": None,
        }


class WalkedSample(NamedTuple):
    position: int
    facts: MeasuredCell
    grade: Grade


_DASHBOARD_DEBOUNCE_S = 0.25
# The write holds the lock every ledger append takes, so the next one rests this many times its cost.
_PERSIST_REST_FACTOR = 9.0

# Every other record the fold routes settles within the debounce.
_AT_ONCE: frozenset[type[CycleRecord]] = frozenset(
    {
        RunWiringRecord,
        RunPhaseRecord,
        BackendWarningRecord,
        ProviderHoldRecord,
        ProviderEasedRecord,
        RoundEnteredRecord,
        RoundClosedRecord,
        PhaseRecord,
        PassGradedRecord,
        BenchScoredRecord,
        ElectionRecord,
    }
)


_MEASURING_PHASES = frozenset(
    {CampaignPhase.ORIGIN, CampaignPhase.MEASURE, CampaignPhase.BENCH, CampaignPhase.VERIFY}
)

# MEASURE, BENCH and optimizer steps are absent on purpose: sample records, a bracket and the step's act drive them.
_PHASE_TO_STATE: dict[str, DashboardState] = {
    CampaignPhase.INIT: DashboardState.INIT,
    CampaignPhase.ORIGIN: DashboardState.ORIGIN,
    CampaignPhase.PROPOSE: DashboardState.PROPOSING,
}


def _opened(kind: MeasurementRole, arm: ArmName, rows: int) -> VerificationMark:
    return VerificationMark(
        kind=kind, arm=arm, state="running", rows=rows, scored=0, level=None, failed_as=None
    )


class LiveDashboardProjection(Projection):
    def __init__(self) -> None:
        # No state stands before the first `RunWiringRecord`: what lands earlier waits here.
        self._unwired: list[CycleRecord] | None = []
        self.state: LiveDashboardState
        self._measurement_node: str
        self._standings: dict[int, tuple[LevelSeries | None, RunStanding | None]] = {}
        # Banked just before each close: the close takes its round's.
        self._series: dict[int, LevelSeries] = {}
        self._sticky_llm_calls: dict[str, tuple[int, NodeBlock]] = {}
        self._launch()

    def _launch(self) -> None:
        # Every round's arms and the run's holds: a cell names an arm announced before its round.
        self._narration = Narration()
        # The live round the arms below are of, each with the cells it walked.
        self._live = 0
        self._samples: dict[ArmKey, list[WalkedSample]] = {}
        self._election: Election | None = None
        # By (individual, position): each open cell's query, arm, and whether it went out armed.
        self._open_samples: dict[tuple[str, int], tuple[str, ArmKey | None, bool]] = {}
        self._bench_readings: dict[int, BenchReading] = {}
        self._around_bench: tuple[DashboardState, str | None] | None = None
        # Between a bench or verify bracket: its cells move no run state, and its rows so far.
        self._in_pass = False
        self._pass_rows: list[GradedCell] = []
        self._flight: tuple[int, int, int] = (0, 0, 0)
        self._affordable: int | None = None
        self._cell_reserve_usd: float | None = None
        self._waiting: FlightWaiting | None = None
        # Each open call's node by call id, and each open phase's node by phase, oldest first.
        self._open_calls: dict[str, str] = {}
        self._open_phases: dict[str, str] = {}
        self._inner: CycleHop | None = None

    def value(self) -> LiveDashboardState | None:
        """`None`: no `RunWiringRecord` yet (a check-in still authoring its origin)."""
        return None if self._unwired is not None else self.composed()

    def feed(self, record: CycleRecord) -> None:
        if type(record) not in self._routes:
            return
        if self._unwired is not None:
            if not isinstance(record, RunWiringRecord):
                self._unwired.append(record)
                return
            self.state = LiveDashboardState.declared(record)
            # Folded under the state it opens: a check-in bills its cycle before Start.
            unwired, self._unwired = self._unwired, None
            for held in unwired:
                super().feed(held)
        super().feed(record)

    def _set_state(self, name: DashboardState, *, step: str | None = None) -> None:
        self.state.state = name
        self.state.optimizer_step = step

    def _handle_run_wiring(self, record: RunWiringRecord) -> None:
        for name, value in LiveDashboardState.launched(record).items():
            setattr(self.state, name, value)
        self.state.verification = self.state.orphaned()
        self._launch()
        self._measurement_node = record.measurement_node

    def _handle_run_phase(self, record: RunPhaseRecord) -> None:
        self._narration.read(self.at_offset, record)
        if self.state.declared_phase == record.state.run_phase:
            return
        self.state.declared_phase = record.state.run_phase
        if isinstance(record.state, Ended):
            self.state.stop_reason = record.state.stop_reason
            self._set_state(DashboardState.STOPPED)
        else:
            # A run declared again is past its failed stop, as `ledger_scan.py::CycleFacts` folds it.
            self.state.error = None

    def _handle_backend_warning(self, record: BackendWarningRecord) -> None:
        warning = BackendWarning(
            ts=record.timestamp,
            kind=record.kind,
            attempt=record.attempt,
            max_attempts=record.max_attempts,
            wait_s=record.wait_s,
            error_class=record.error_class,
            status_code=record.status_code,
            final=record.final,
            query=record.query,
            detail=record.detail,
        )
        self.state.recent_backend_warnings = [*self.state.recent_backend_warnings, warning][-10:]

    def _handle_provider_hold(self, record: ProviderHoldRecord) -> None:
        self._narration.read(self.at_offset, record)

    def _handle_provider_eased(self, record: ProviderEasedRecord) -> None:
        self._narration.read(self.at_offset, record)

    def _handle_fold_basis(self, record: FoldBasisRecord) -> None:
        self._narration.read(self.at_offset, record)

    def _handle_round_entered(self, record: RoundEnteredRecord) -> None:
        self._enter(record.round)

    def _handle_cycle_minted(self, record: CycleMintedRecord) -> None:
        # A fork's mint enters its cut: the rounds from it on are its own, never its parent line's.
        if record.cut_round is not None:
            self._enter(record.cut_round)

    def _enter(self, round_num: int) -> None:
        s = self.state
        s.round = round_num
        s.rounds = [r for r in s.rounds if r.round < round_num]
        self._standings = {n: held for n, held in self._standings.items() if n < round_num}
        self._series = {n: held for n, held in self._series.items() if n < round_num}
        self._stand()
        self._narration.enter(round_num)
        self._reset_live(round_num)

    def _reset_live(self, round_num: int) -> None:
        self._live = round_num
        self._samples = {}
        self._election = None

    def _stand(self) -> None:
        """The run stands where its LATEST round's last statement says, whichever round was stated last."""
        s = self.state
        s.level_series, s.run_standing = (
            self._standings[max(self._standings)] if self._standings else (None, None)
        )

    def _move_to_round(self, round_num: int | None) -> None:
        if round_num is not None:
            self.state.round = round_num
        if self._live != self.state.round:
            self._reset_live(self.state.round)

    def _handle_level_series(self, record: LevelSeriesRecord) -> None:
        self._series[record.round] = record.series

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:
        self._standings[record.round] = (self._series.get(record.round), record.run_standing)
        self._stand()
        prior = [r for r in self.state.rounds if r.round != record.round]
        summary = self._with_bench(record.summary())
        self.state.rounds = sorted([*prior, summary], key=lambda r: r.round)

    def _handle_phase(self, record: PhaseRecord) -> None:
        s = self.state
        self._move_to_round(record.round)
        act = record.act
        if record.phase == CampaignPhase.BENCH and record.step == "enter":
            # Stopped included: `held-out` opens a pass outside any run, and its exit hands the state back.
            self._around_bench = (s.state, s.optimizer_step)
            self._set_state(DashboardState.BENCH)
        elif record.step == "enter" and s.state != DashboardState.STOPPED:
            mapped = _PHASE_TO_STATE.get(record.phase)
            if mapped is not None:
                self._set_state(mapped)
            elif isinstance(act, StepOpened):
                self._set_state(DashboardState.OPTIMIZER_STEP, step=act.activity)
        match act:
            case InitOpened() | InitClosed():
                s.composite_fitness_formula = act.formula.formula
            case PassOpened():
                self._in_pass, self._pass_rows = True, []
                self._mark(_opened(act.verification, act.arm, act.rows))
        self._open_phases.pop(record.phase, None)
        if record.step == "enter" and (node := self._phase_node(record)) is not None:
            self._open_phases[record.phase] = node
        if record.step == "exit" and record.phase in (CampaignPhase.BENCH, CampaignPhase.VERIFY):
            self._in_pass = False
        if record.phase == CampaignPhase.VERIFY and record.step == "exit":
            # A verify or a noise floor is graded inside its bracket; a bench pass after it.
            s.verification = s.orphaned(spare=MeasurementRole.BENCH)
        if (
            record.phase == CampaignPhase.BENCH
            and record.step == "exit"
            and (around := self._around_bench) is not None
        ):
            self._around_bench = None
            self._set_state(around[0], step=around[1])

    def _handle_bench_scored(self, record: BenchScoredRecord) -> None:
        for reading in (record.bench.origin, record.bench.selected):
            if reading is not None:
                self._place_bench(reading)

    def _handle_pass_graded(self, record: PassGradedRecord) -> None:
        taken = record.verification
        opened = _opened(taken.kind, taken.arm, len(taken.sample_keys))
        match read := record.reading:
            case None:
                ended = opened.ended(scored=len(taken.cells), level=None, failed_as=record.state)
            case BenchReading():
                self._place_bench(read)
                ended = opened.ended(scored=read.n, level=read.level, failed_as=None)
            case VerifyReading(fresh=fresh):
                ended = opened.ended(scored=fresh.n, level=fresh.fitness, failed_as=None)
            case NoiseBand(fresh_fitness=fitness):
                # Its rows are every rescore's, as its bracket opened them.
                ended = _opened(taken.kind, taken.arm, len(taken.cells)).ended(
                    scored=len(taken.cells), level=fitness, failed_as=None
                )
        self._mark(ended)

    def _handle_election(self, record: ElectionRecord) -> None:
        self._election = record

    def _handle_arm(self, record: ArmRecord) -> None:
        self._narration.announce(record)
        if record.round != self._live:
            return
        # A walk ending: its sample launched INTO an armed window is a discard; one left open
        # unarmed failed on its own. Another walk's open cells are still its own to take.
        if isinstance(record.state, Concluded):
            ended = [k for k, (_, arm, _) in self._open_samples.items() if arm == record.arm]
            self.state.sample_lookahead_discards += sum(
                1 for k in ended if self._open_samples.pop(k)[2]
            )

    def _mark(self, mark: VerificationMark) -> None:
        held = [
            m for m in self.state.verification if (m.kind, m.arm.key) != (mark.kind, mark.arm.key)
        ]
        self.state.verification = [*held, mark]

    def _place_bench(self, reading: BenchReading) -> None:
        # Kept, not only applied: the origin's reading lands before round 0's summary exists.
        self._bench_readings[reading.round] = reading
        self.state.rounds = [self._with_bench(r) for r in self.state.rounds]

    def _with_bench(self, r: RoundSummary) -> RoundSummary:
        reading = self._bench_readings.get(r.round)
        if reading is None:
            return r
        return r.model_copy(
            update={
                "candidates": [
                    c.model_copy(
                        update={"reading": c.reading.model_copy(update={"bench": reading})}
                    )
                    if c.reading.sp_hash == reading.sp_hash
                    else c
                    for c in r.candidates
                ],
            }
        )

    def _handle_sample_started(self, record: SampleStartedRecord) -> None:
        self._open_samples[record.individual_id, record.position] = (
            record.query_preview,
            record.arm,
            record.sample_lookahead > 1 or record.sample_abreast != 1,
        )
        if not self._in_pass:
            self._set_state(DashboardState.SCORING)

    def _handle_sample_scored(self, record: SampleScoredRecord) -> None:
        qi, qt = record.sample_idx or 0, record.sample_total or 0
        self._open_samples.pop((record.individual_id, record.position), None)
        # A beat names no cell: an inner run still open names itself again on its next beat.
        self._inner = None
        if not self._in_pass:
            self._set_state(
                DashboardState.BETWEEN_CANDIDATES
                if qi + 1 >= qt
                else DashboardState.BETWEEN_SAMPLES
            )
        # A pass no arm walks (a bench pass, a verify) moves the run's scalars, never the round's arms.
        if (arm := self._narration.take(record)) is not None:
            if record.round == self._live:
                # Held with its grade: without it `blocks.py::sample_row` renders an ungraded cell MISS.
                result = record.result
                walked = WalkedSample(record.position, result.facts, result.grade)
                self._samples.setdefault(arm, []).append(walked)
            return
        for held in self.state.verification:
            if (
                held.state == "running"
                and held.kind == record.role.value
                and held.arm.key == record.individual_id
            ):
                self._pass_rows.append(record.result)
                basis = self._narration.basis_of(record)
                sheet = basis.sheet(self._pass_rows)
                self._mark(held.scored_one(level_band(sheet, "fitness", basis.fitness_unit)))
                return

    def _handle_flight(self, record: FlightRecord) -> None:
        self._flight = (record.out, record.allowed, record.most)
        self._affordable = record.affordable
        self._cell_reserve_usd = record.cell_usd
        self._waiting = record.waiting

    def _handle_race_catch_up(self, record: RaceCatchUpRecord) -> None:
        self._append_catch_up(
            CatchUpLogEntry(
                member=record.member,
                round=record.round,
                arm=record.arm,
                label=self._label(record.arm),
                position=record.position,
                prior_keys=record.prior_keys,
            )
        )

    def _name(self, key: ArmKey) -> ArmName | None:
        if key not in self._narration.announced(self._live):
            return None
        return arm_name(key, self._narration.state(self._live, key), self._live)

    def _label(self, key: ArmKey) -> str:
        return "" if (name := self._name(key)) is None else name.label

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        self._open_calls[record.call_id] = record.node

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        if record.inner is not None:
            self._inner = record.inner

    def _handle_cell_beat(self, record: CellBeatRecord) -> None:
        if record.inner is not None:
            self._inner = record.inner

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        self._sticky_llm_calls[record.node] = (self.state.round, build_node_block(record))
        self._open_calls.pop(record.call_id, None)

    def _handle_error(self, record: ErrorRecord) -> None:
        self.state.error = record

    def _append_catch_up(self, entry: CatchUpLogEntry) -> None:
        self.state.catch_up_log = [*self.state.catch_up_log, entry][-256:]

    def _phase_node(self, record: PhaseRecord) -> str | None:
        """The node a phase runs as it opens: its act's, or the measurement node for a scoring pass."""
        match record.act:
            case ProposeOpened(node=node) | StepOpened(node=node):
                return node
        return self._measurement_node if record.phase in _MEASURING_PHASES else None

    def _active_node(self) -> ActiveNode | None:
        """Every node on this ledger is the optimizer's: a target's calls land on its own ledger."""
        held = [*self._open_calls.values()] or [*self._open_phases.values()]
        return ActiveNode(node=held[-1]) if held else None

    def _current_round_nodes(self) -> dict[str, NodeBlock]:
        return {
            node: block
            for node, (fired_in, block) in self._sticky_llm_calls.items()
            if fired_in == self.state.round
        }

    def composed(self) -> LiveDashboardState:
        """A snapshot: the fold replaces a field, never changes one in place, so what it shares stands."""
        s = self.state
        if any(self._flight):
            in_flight, allowed, most = self._flight
        else:
            in_flight = allowed = 0
            most = s.round_cells_ceiling
        waiting_on: WaitingOn | None = None
        waiting_since: float | None = None
        w = self._waiting
        opened = None if w is None else self._open_samples.get((w.individual_id, w.position))
        if w is not None and opened is not None:
            label = None if (arm := opened[1]) is None else self._label(arm)
            waiting_on = WaitingOn(label=label, position=w.position)
            waiting_since = w.since
        active = self._active_node()
        oldest = next(iter(self._open_samples.values()), None)
        open_cells: dict[ArmKey, list[int]] = {}
        for (_, position), (_, arm, _) in self._open_samples.items():
            if arm is not None:
                open_cells.setdefault(arm, []).append(position)
        current = CurrentRound(
            round=s.round,
            measurement_node=self._measurement_node,
            inner=self._inner,
            candidates=build_candidate_rows(
                self._narration, self._live, self._samples, self._election, open_cells
            ),
            nodes=self._current_round_nodes(),
        )
        return s.model_copy(
            update={
                "at_offset": self.at_offset,
                "current_round": current,
                "active": active,
                "current_query_payload": oldest[0] if oldest else None,
                "in_flight": in_flight,
                "lookahead_allowed": allowed,
                "lookahead_most": most,
                "lookahead_affordable": self._affordable,
                "cell_reserve_usd": self._cell_reserve_usd,
                "waiting_on": waiting_on,
                "waiting_since": waiting_since,
                # The field holds one sender: the episode that has held longest.
                "backpressure": self._narration.longest_hold,
            }
        )


class DashboardCheckout(Projection):
    """`dashboard.json`: its ledger index's `LiveDashboardProjection`, written out at a boundary or
    settled; a write that cannot land is its ledger's warning (`LedgerIndex.shielded`). It routes no
    arm: every line it is fed only times the write."""

    derives: ClassVar[str] = "dashboard.json"

    def __init__(self, cycle_dir: CycleDir) -> None:
        *self._prefix, own = fork_chain(cycle_dir)
        self._index = LedgerIndex.of(own.path)
        self._path = CycleLayout(Path(cycle_dir)).dashboard
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._dirty = False
        self._cost_s = 0.0

    @property
    def live(self) -> LiveDashboardProjection:
        """The run's own fold, fed on as its ledger grows."""
        return self._index.live(
            LiveDashboardProjection, start=_start(LiveDashboardProjection, self._prefix)
        )

    def feed(self, record: CycleRecord) -> None:
        with self._lock:
            if type(record) in _AT_ONCE:
                self._write()
            else:
                self._settle()

    def _settle(self) -> None:
        self._dirty = True
        # An armed timer is never replaced: staleness is bounded from the FIRST mutation.
        if self._timer is not None:
            return
        rest = max(_DASHBOARD_DEBOUNCE_S, self._cost_s * _PERSIST_REST_FACTOR)
        timer = threading.Timer(rest, self._index.shielded, (self, self._settled))
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _settled(self) -> None:
        with self._lock:
            # Before the dirty test: left set on the not-dirty path, it disarms the debounce.
            self._timer = None
            if self._dirty:
                self._write()

    def drain(self) -> None:
        """Writes it now, through its index's guard."""
        self._index.shielded(self, self._drained)

    def _drained(self) -> None:
        with self._lock:
            self._write()

    def _write(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._dirty = False
        state = self._index.view(
            LiveDashboardProjection, start=_start(LiveDashboardProjection, self._prefix)
        )
        if state is None:
            return
        started = time.perf_counter()
        write_json(self._path, state.model_dump(), default=str)
        self._cost_s = time.perf_counter() - started


class RunLimitsProjection(Projection):
    """Each patch of the limits in order of arrival: a launch's wiring names its budget and its
    optimizer's bounds, a standing record what it changes. The campaign's ceiling rides its root's
    ledger (`CampaignStore.run_limits`)."""

    def __init__(self) -> None:
        self._limits = RunLimits()
        self._wired = False

    def _handle_run_wiring(self, record: RunWiringRecord) -> None:
        limits = self._limits
        self._limits = RunLimits(
            budget=record.budget, overshoot=limits.overshoot, optimizer=record.optimizer_limits
        )
        self._wired = True

    def _handle_standing_budget(self, record: StandingBudgetRecord) -> None:
        limits = self._limits
        named = "overshoot" in record.model_fields_set
        self._limits = RunLimits(
            budget=limits.budget.over(record.budget),
            overshoot=record.overshoot if named else limits.overshoot,
            optimizer=limits.optimizer,
        )

    def value(self) -> RunLimits | None:
        return self._limits if self._wired else None


def fold_at(cut: CycleAt) -> LiveDashboardState | None:
    """`None`: the cut holds no `RunWiringRecord` yet (a check-in still authoring its origin)."""
    *prefix, own = fork_chain(cut.cycle)
    return LedgerIndex.of(own.path).view(
        LiveDashboardProjection, _until(cut), start=_start(LiveDashboardProjection, prefix)
    )


def run_limits_at(cut: CycleAt) -> RunLimits | None:
    *prefix, own = fork_chain(cut.cycle)
    return LedgerIndex.of(own.path).view(
        RunLimitsProjection, _until(cut), start=_start(RunLimitsProjection, prefix)
    )


def _until(cut: CycleAt) -> int | None:
    return None if cut.offset is None else cut.offset + 1


def _start[F: LedgerFold[Any, Any]](fold: type[F], prefix: list[LedgerSpan]) -> F | None:
    start: F | None = None
    for span in prefix:
        assert span.until is not None, "a fork's parent is read to its cut"
        start = LedgerIndex.of(span.path).prefix(fold, span.until, start=start)
    return start
