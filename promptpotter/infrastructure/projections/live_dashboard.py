from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, NamedTuple, assert_never

from pydantic import ConfigDict, Field

from promptpotter.domain.arm_reading import (
    ArmElection,
    ArmReading,
    DisplayMetric,
    panel_counts,
    panel_cuts,
)
from promptpotter.domain.arms import Concluded, Measuring, arm_name
from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.bench import BenchReading
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.cycle_paths import Cut, CycleDir, CycleHop
from promptpotter.domain.dashboard_rows import (
    DashboardSample,
    LiveCandidate,
    RoundSummary,
    RunLimits,
    VerificationMark,
    sample_status,
)
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
from promptpotter.domain.records.held_out import BenchScoredRecord, PassGradedRecord
from promptpotter.domain.records.run_records import (
    ArmRecord,
    BackendRevision,
    BackendWarningRecord,
    CycleMintedRecord,
    DefaultsDrift,
    ElectionRecord,
    Ended,
    FlightRecord,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    ProviderEasedRecord,
    ProviderHoldRecord,
    RaceCatchUpRecord,
    RaceStandingRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundRestatedRecord,
    RoundStatement,
    RunPhaseRecord,
    RunWiringRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    StandingBudgetRecord,
    TokenUsageRecord,
)
from promptpotter.domain.results import Election
from promptpotter.domain.results_health import is_degraded, terminal_node
from promptpotter.domain.round_audit import NodeBlock
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import (
    Grade,
    MeasuredCell,
    SampleStatus,
    graded_answer,
    ground_truth_text,
    is_verifier_graded,
)
from promptpotter.domain.spend import CeilingMeter, MeteredSpend, SpendRollup, TokenAccount
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.verification import NoiseBand, VerifyReading
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.audit_trail import build_node_block
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import (
    LedgerFold,
    LedgerIndex,
    LedgerSpan,
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
        terminal_node=terminal_node(facts),
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
) -> list[LiveCandidate]:
    """The narration's live round: each arm it announced at *round_num*, with the cells it walked."""
    arms = [
        (key, arm, narration.fold(round_num, key))
        for key in narration.announced(round_num)
        if (arm := narration.state(round_num, key)).role != MeasurementRole.PARENT
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
            fit=None if election is None else election.fit.get(key),
        )
        rows.append(
            LiveCandidate.in_flight(
                reading, arm, samples=[sample_row(s) for s in samples.get(key, ())]
            )
        )
    return rows


def build_racing_block(
    race: RaceStandingRecord | None, standings: dict[str, float]
) -> RacingBlock | None:
    if race is None:
        return None
    ranked = sorted(standings.items(), key=lambda kv: -kv[1])
    return RacingBlock(
        member=race.member,
        n_samples=race.n_samples,
        top=[{"id": cid, "p_best": p} for cid, p in ranked[:5]],
    )


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


class RacingBlock(StrictModel):
    """The round's standing in its eliminator ``member``'s race."""

    member: str
    n_samples: int
    top: list[dict[str, Any]]


class CurrentRound(StrictModel):
    """The round in flight, rebuilt whole on every persist."""

    round: int = 0
    active_node: str | None = None
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
    racing: RacingBlock | None = None


class DashboardFacts(StrictModel):
    model_config = ConfigDict(validate_assignment=False)

    # ``-1`` until the first record lands; the SSE snapshot parks its tail one past it.
    at_offset: int = -1

    state: DashboardState = DashboardState.INIT
    optimizer_step: str | None = None

    stop_reason: StopReason | None = None

    round: int = 0
    # The arm the oldest open cell is walked for; ``None`` between walks and on a pass of no arm.
    measuring_arm: ArmName | None = None

    rounds: list[RoundSummary] = Field(default_factory=list)

    verification: list[VerificationMark] = Field(
        default_factory=list,
        description="Each arm's latest verify and held-out bench pass on this cycle's ledger, "
        "in flight or ended; its rows are no round's cells.",
    )
    composite_fitness_formula: str | None = None
    display_metric: DisplayMetric

    degraded_count: int = 0
    error_count: int = 0

    backend_retry_count: int = 0
    recent_backend_warnings: list[BackendWarning] = Field(default_factory=list)

    total_backend_calls: int = 0

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
    # A FAILED stop's failure, else absent: served only as the run's pinned `said` row.
    error: ErrorRecord | None = None
    # The course entry serves both (`CourseFacts.standing` / `elects_on`); the file keeps them.
    run_standing: RunStanding | None = None
    elects_on: DisplayMetric
    # The cell the open call holds; served only as `ServedDashboard.held_call`.
    waiting_on: WaitingOn | None = None

    current_position: int | None = None
    # The last close's; served only as each `ServedRound`'s readings of it.
    level_series: LevelSeries | None = None
    # A PLAN: an eliminator can stop a candidate before the tail is reached.
    declared_sample_order: list[int] = Field(default_factory=list)

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

    spend: SpendRollup = Field(default_factory=SpendRollup)

    # PER ROUND, and NOT clamped by a rewind, unlike `rounds[]`: a re-measured round costs twice.
    spend_by_round: dict[str, SpendRollup] = Field(default_factory=dict)

    def orphaned(self, spare: MeasurementRole | None = None) -> list[VerificationMark]:
        """The passes, each still running and not of kind *spare* ended stopped: its process is gone ungraded."""
        return [
            mark.orphaned() if mark.state == "running" and mark.kind != spare else mark
            for mark in self.verification
        ]

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
            "measuring_arm": None,
            "stop_reason": None,
            "error": None,
            "current_query_payload": None,
            "current_position": None,
            "declared_sample_order": [],
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
        RoundRestatedRecord,
        PhaseRecord,
        PassGradedRecord,
        BenchScoredRecord,
        ElectionRecord,
    }
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
        self._sticky_llm_calls: dict[str, tuple[int, NodeBlock]] = {}
        self._launch()

    def _launch(self) -> None:
        # Every round's arms and the run's holds: a cell names an arm announced before its round.
        self._narration = Narration()
        # The live round the arms below are of, each with the cells it walked.
        self._live = 0
        self._samples: dict[ArmKey, list[WalkedSample]] = {}
        self._election: Election | None = None
        self._race: RaceStandingRecord | None = None
        self._race_standings: dict[str, float] = {}
        self._open_samples: dict[int, tuple[str, ArmKey | None, int]] = {}
        self._bench_readings: dict[int, BenchReading] = {}
        self._around_bench: tuple[DashboardState, str | None, ArmName | None] | None = None
        # Between a bench or verify bracket: its cells move no run state.
        self._in_pass = False
        self._flight: tuple[int, int, int] = (0, 0, 0)
        self._affordable: int | None = None
        self._cell_reserve_usd: float | None = None
        self._waiting: tuple[int, float] | None = None
        self._in_flight: tuple[str, str] | None = None
        self._inner: CycleHop | None = None
        self._proposer_node: str | None = None
        self._step_node: str | None = None

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
            self.state.measuring_arm = None
        else:
            # A run declared again is past its failed stop, as `ledger_scan.py::CycleFacts` folds it.
            self.state.error = None

    def _handle_backend_warning(self, record: BackendWarningRecord) -> None:
        self.state.backend_retry_count += 1
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
        self._stand()
        self._narration.enter(round_num)
        self._reset_live(round_num)

    def _reset_live(self, round_num: int) -> None:
        self._live = round_num
        self._samples = {}
        self._election = None
        self._race = None
        self._race_standings = {}

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

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:
        self._stated(record)

    def _handle_round_restated(self, record: RoundRestatedRecord) -> None:
        self._stated(record)

    def _stated(self, record: RoundStatement) -> None:
        self._standings[record.round] = (record.level_series, record.run_standing)
        self._stand()
        prior = [r for r in self.state.rounds if r.round != record.round]
        summary = self._with_bench(record.summary())
        self.state.rounds = sorted([*prior, summary], key=lambda r: r.round)

    def _handle_phase(self, record: PhaseRecord) -> None:
        s = self.state
        self._move_to_round(record.round)
        act = record.act
        if record.phase == CampaignPhase.BENCH and record.step == "enter":
            # Stopped included: `grade-bench` opens a pass outside any run, and its exit hands the state back.
            self._around_bench = (s.state, s.optimizer_step, s.measuring_arm)
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
            case ProposeOpened():
                self._proposer_node = act.node
            case StepOpened():
                self._step_node = act.node
            case PassOpened():
                self._in_pass = True
                self._mark(_opened(act.verification, act.arm, act.rows))
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
            state, step, s.measuring_arm = around
            self._set_state(state, step=step)

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
        # A walk ending: a sample launched INTO an armed window is a discard; one left open at depth 1 failed on its own.
        if isinstance(record.state, Concluded) and self._open_samples:
            self.state.sample_lookahead_discards += sum(
                1 for *_, depth in self._open_samples.values() if depth > 1
            )
            self._open_samples.clear()
        if isinstance(record.state, Measuring) and record.state.sample_order:
            self.state.declared_sample_order = record.state.sample_order

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
        self._open_samples[record.position] = (
            record.query_preview,
            record.arm,
            record.sample_lookahead,
        )
        self._refresh_open_sample_markers()
        if not self._in_pass:
            self._set_state(DashboardState.SCORING)

    def _handle_sample_scored(self, record: SampleScoredRecord) -> None:
        qi, qt = record.sample_idx or 0, record.sample_total or 0
        self._open_samples.pop(record.position, None)
        # A beat names no cell: an inner run still open names itself again on its next beat.
        self._inner = None
        self._absorb_sample_scored(record.result.facts, last_in_candidate=(qi + 1 >= qt))
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
                running = None if record.running is None else record.running.fitness_band

                self._mark(held.scored_one(running))
                return

    def _handle_race_standing(self, record: RaceStandingRecord) -> None:
        self._race = record
        self._race_standings[record.current] = record.p_best

    def _handle_flight(self, record: FlightRecord) -> None:
        self._flight = (record.out, record.allowed, record.most)
        self._affordable = record.affordable
        self._cell_reserve_usd = record.cell_usd
        waiting = record.waiting
        self._waiting = None if waiting is None else (waiting.position, waiting.since)

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

    def _refresh_open_sample_markers(self) -> None:
        s = self.state
        if not self._open_samples:
            s.current_query_payload = None
            s.current_position = None
            return
        sid, (query_text, arm, _depth) = next(iter(self._open_samples.items()))
        s.current_query_payload = query_text
        s.current_position = sid
        s.measuring_arm = None if arm is None else self._name(arm)

    def _name(self, key: ArmKey) -> ArmName | None:
        if key not in self._narration.announced(self._live):
            return None
        return arm_name(key, self._narration.state(self._live, key), self._live)

    def _label(self, key: ArmKey) -> str:
        return "" if (name := self._name(key)) is None else name.label

    def _absorb_sample_scored(self, facts: MeasuredCell, *, last_in_candidate: bool) -> None:
        s = self.state

        # The second arm is not redundant: `pipeline.error` is the BACKEND faulting a row PP classified clean.
        if facts.errored or facts.pipeline.error:
            s.error_count += 1

        if not facts.cached:
            s.total_backend_calls += 1
            if is_degraded(facts):
                s.degraded_count += 1

        self._refresh_open_sample_markers()
        if not self._in_pass:
            self._set_state(
                DashboardState.BETWEEN_CANDIDATES
                if last_in_candidate
                else DashboardState.BETWEEN_SAMPLES
            )

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        # Assigned, never banked in place: a snapshot `value` served holds the rollups as they were.
        key = str(record.spend_round)
        held = self.state.spend_by_round.get(key)
        self.state.spend = self.state.spend.banked(record)
        rollup = (SpendRollup() if held is None else held).banked(record)
        self.state.spend_by_round = {**self.state.spend_by_round, key: rollup}

    def spend_metered(self, meters: CeilingMeter) -> MeteredSpend:
        return MeteredSpend.of(self.state.spend, meters)

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        self._in_flight = (record.call_id, record.node)

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        if record.inner is not None:
            self._inner = record.inner

    def _handle_cell_beat(self, record: CellBeatRecord) -> None:
        if record.inner is not None:
            self._inner = record.inner

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        self._sticky_llm_calls[record.node] = (self.state.round, build_node_block(record))
        if self._in_flight is not None and record.call_id and self._in_flight[0] == record.call_id:
            self._in_flight = None

    def _handle_error(self, record: ErrorRecord) -> None:
        self.state.error = record

    def _append_catch_up(self, entry: CatchUpLogEntry) -> None:
        self.state.catch_up_log = [*self.state.catch_up_log, entry][-256:]

    def _active_node(self) -> str | None:
        if self._in_flight is not None:
            return self._in_flight[1]
        state = self.state.state
        match state:
            case DashboardState.PROPOSING:
                return self._proposer_node
            case (
                DashboardState.ORIGIN
                | DashboardState.SCORING
                | DashboardState.BETWEEN_SAMPLES
                | DashboardState.BETWEEN_CANDIDATES
                | DashboardState.BENCH
            ):
                return self._measurement_node
            case DashboardState.OPTIMIZER_STEP:
                return self._step_node
            case DashboardState.INIT | DashboardState.STOPPED:
                return None
            case _:
                assert_never(state)

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
        opened = self._open_samples.get(self._waiting[0]) if self._waiting else None
        waiting_on: WaitingOn | None = None
        waiting_since: float | None = None
        if self._waiting is not None and opened is not None:
            arm = opened[1]
            waiting_on = WaitingOn(
                label=None if arm is None else self._label(arm),
                position=self._waiting[0],
            )
            waiting_since = self._waiting[1]
        active = self._active_node()
        measuring = active == self._measurement_node
        current = CurrentRound(
            round=s.round,
            active_node=active,
            measurement_node=self._measurement_node,
            inner=self._inner,
            candidates=build_candidate_rows(
                self._narration, self._live, self._samples, self._election
            ),
            nodes=self._current_round_nodes(),
            racing=build_racing_block(self._race, self._race_standings),
        )
        return s.model_copy(
            update={
                "at_offset": self.at_offset,
                "current_round": current,
                "measuring_arm": s.measuring_arm if measuring else None,
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


class DashboardCheckout:
    """`dashboard.json`: its ledger index's `LiveDashboardProjection`, written out at a boundary or
    settled; a write that cannot land is its ledger's warning (`LedgerIndex.shielded`)."""

    def __init__(self, cycle_dir: CycleDir) -> None:
        *self._prefix, own = ledger_chain(cycle_dir, moment=None)
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
            LiveDashboardProjection, seed=_seed(LiveDashboardProjection, self._prefix)
        )

    @property
    def spend(self) -> SpendRollup:
        """What the run has spent so far, as its fold banks it."""
        return self.live.state.spend

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
            LiveDashboardProjection, seed=_seed(LiveDashboardProjection, self._prefix)
        )
        if state is None:
            return
        started = time.perf_counter()
        write_json(self._path, state.model_dump(), default=str)
        self._cost_s = time.perf_counter() - started


class RunLimitsProjection(Projection):
    """The limits each launch declares, every later standing budget laid over: the campaign's
    ceiling rides its root's ledger (`CampaignStore.run_limits`)."""

    def __init__(self) -> None:
        self._limits: RunLimits | None = None

    def _handle_run_wiring(self, record: RunWiringRecord) -> None:
        self._limits = record.run_limits

    def _handle_standing_budget(self, record: StandingBudgetRecord) -> None:
        # Last wins and names every cycle axis it stands, so laying each over the last is laying the last.
        if self._limits is not None:
            self._limits = self._limits.model_copy(
                update={
                    "budget": self._limits.budget.over(record.budget),
                    "overshoot": record.overshoot,
                }
            )

    def value(self) -> RunLimits | None:
        return self._limits


def fold_at(cut: Cut) -> LiveDashboardState | None:
    """`None`: the cut holds no `RunWiringRecord` yet (a check-in still authoring its origin)."""
    *prefix, own = ledger_chain(cut.cycle, moment=None)
    return LedgerIndex.of(own.path).view(
        LiveDashboardProjection, _until(cut), seed=_seed(LiveDashboardProjection, prefix)
    )


def run_limits_at(cut: Cut) -> RunLimits | None:
    *prefix, own = ledger_chain(cut.cycle, moment=None)
    return LedgerIndex.of(own.path).view(
        RunLimitsProjection, _until(cut), seed=_seed(RunLimitsProjection, prefix)
    )


def _until(cut: Cut) -> int | None:
    return None if cut.offset is None else cut.offset + 1


def _seed[F: LedgerFold[Any, Any]](fold: type[F], prefix: list[LedgerSpan]) -> F | None:
    seed: F | None = None
    for span in prefix:
        assert span.until is not None, "a fork's parent is read to its cut"
        seed = LedgerIndex.of(span.path).prefix(fold, span.until, seed=seed)
    return seed
