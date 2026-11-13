from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, assert_never

from promptpotter.domain.arms import Concluded, Measuring
from promptpotter.domain.bench import BandedValue, BenchReading
from promptpotter.domain.cycle_paths import Cut, CycleDir, CycleHop
from promptpotter.domain.dashboard_rows import RoundSummary, RunLimits, VerificationMark
from promptpotter.domain.paired_reading import ArmKey, ArmName
from promptpotter.domain.phase_views import (
    BenchScoredView,
    InitEnterView,
    InitExitView,
    OptimizerStepEnterView,
    RoundStartView,
    VerificationEnterView,
    VerificationGradedView,
)
from promptpotter.domain.phases import (
    CampaignPhase,
    DashboardState,
    ErrorRecord,
    RunPhase,
)
from promptpotter.domain.results_health import is_degraded
from promptpotter.domain.round_audit import NodeBlock
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.run_records import (
    ArmRecord,
    BackendWarningRecord,
    CycleRecord,
    ElectionRecord,
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
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import MeasuredCell
from promptpotter.domain.spend import CeilingMeter, MeteredSpend, SpendRollup
from promptpotter.domain.verification import NoiseBand, VerificationKind, VerifyReading
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.audit_trail import build_node_block
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.projections.live_dashboard.blocks import (
    build_candidate_rows,
    build_racing_block,
)
from promptpotter.infrastructure.projections.live_dashboard.round_buffer import RoundBuffer
from promptpotter.infrastructure.projections.live_dashboard.state import (
    BackendWarning,
    CatchUpLogEntry,
    CurrentRound,
    LiveDashboardState,
    WaitingOn,
)
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import (
    LedgerFold,
    LedgerIndex,
    LedgerSpan,
)

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
        ElectionRecord,
    }
)


# MEASURE, BENCH and optimizer steps are absent on purpose: sample records, a bracket and the enter view drive them.
_PHASE_TO_STATE: dict[str, DashboardState] = {
    CampaignPhase.INIT: DashboardState.INIT,
    CampaignPhase.ORIGIN: DashboardState.ORIGIN,
    CampaignPhase.PROPOSE: DashboardState.PROPOSING,
}


def _opened(kind: VerificationKind, arm: ArmName, rows: int) -> VerificationMark:
    return VerificationMark(
        kind=kind, arm=arm, state="running", rows=rows, scored=0, level=None, failed_as=None
    )


class LiveDashboardProjection(Projection):
    def __init__(self) -> None:
        # No state stands before the first `RunWiringRecord`: what lands earlier waits here.
        self._unwired: list[CycleRecord] | None = []
        self.state: LiveDashboardState
        self._measurement_node: str
        self._at = ""
        self._standings: dict[int, tuple[LevelSeries | None, RunStanding | None]] = {}
        self._sticky_llm_calls: dict[str, tuple[int, NodeBlock]] = {}
        self._launch()

    def _launch(self) -> None:
        self.short_formula_template: str | None = None
        self._buffer = RoundBuffer()
        self._open_samples: dict[int, tuple[str, ArmKey | None, int]] = {}
        self._bench_readings: dict[int, BenchReading] = {}
        self._around_bench: tuple[DashboardState, str | None, ArmName | None] | None = None
        # Between a bench or verify bracket: its cells move no run state.
        self._in_pass = False
        self._flight: tuple[int, int, int] = (0, 0, 0)
        self._affordable: int | None = None
        self._cell_reserve_usd: float | None = None
        self._waiting: tuple[int, float] | None = None
        # A launch hears a hold still on at its first send: one the last launch saw open ends here.
        self._holds: dict[str, ProviderHoldRecord] = {}
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
                self._fold(held)
        self._fold(record)

    def _fold(self, record: CycleRecord) -> None:
        self._at = record.timestamp
        super().feed(record)

    def _set_state(self, name: DashboardState, *, step: str | None = None) -> None:
        self.state.state = name
        self.state.optimizer_step = step
        self.state.state_since = self._at

    def _handle_run_wiring(self, record: RunWiringRecord) -> None:
        for name, value in LiveDashboardState.launched(record).items():
            setattr(self.state, name, value)
        self.state.verification = self.state.orphaned()
        self._launch()
        self._measurement_node = record.measurement_node

    def _handle_run_phase(self, record: RunPhaseRecord) -> None:
        if self.state.declared_phase == record.run_phase:
            return
        self.state.declared_phase = record.run_phase
        if record.run_phase == RunPhase.TERMINAL:
            self.state.stop_reason = record.stop_reason
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
        self._holds[record.sender] = record

    def _handle_provider_eased(self, record: ProviderEasedRecord) -> None:
        self._holds.pop(record.sender, None)

    def _handle_round_entered(self, record: RoundEnteredRecord) -> None:
        s = self.state
        s.round = record.round
        s.rounds = [r for r in s.rounds if r.round < record.round]
        self._standings = {n: held for n, held in self._standings.items() if n < record.round}
        self._stand()
        self._buffer.enter(record)

    def _stand(self) -> None:
        """The run stands where its LATEST round's last statement says, whichever round was stated last."""
        s = self.state
        s.level_series, s.run_standing = (
            self._standings[max(self._standings)] if self._standings else (None, None)
        )

    def _move_to_round(self, round_num: int | None) -> None:
        if round_num is not None:
            self.state.round = round_num
        if self._buffer.round_num != self.state.round:
            self._buffer.reset(self.state.round)

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:
        self._stated(record)

    def _handle_round_restated(self, record: RoundRestatedRecord) -> None:
        self._stated(record)

    def _stated(self, record: RoundStatement) -> None:
        self._standings[record.round] = (record.level_series, record.run_standing)
        self._stand()
        prior = [r for r in self.state.rounds if r.round != record.round]
        summary = self._with_bench(RoundSummary.of(record))
        self.state.rounds = sorted([*prior, summary], key=lambda r: r.round)

    def _handle_phase(self, record: PhaseRecord) -> None:
        s = self.state
        self._move_to_round(record.round)
        view = record.view
        if record.phase == CampaignPhase.BENCH and record.event == "enter":
            # Stopped included: `grade-bench` opens a pass outside any run, and its exit hands the state back.
            self._around_bench = (s.state, s.optimizer_step, s.measuring_arm)
            self._set_state(DashboardState.BENCH)
        elif record.event == "enter" and s.state != DashboardState.STOPPED:
            mapped = _PHASE_TO_STATE.get(record.phase)
            if mapped is not None:
                self._set_state(mapped)
            elif isinstance(view, OptimizerStepEnterView):
                self._set_state(DashboardState.OPTIMIZER_STEP, step=view.activity)
        match view:
            case InitEnterView() | InitExitView():
                if view.composite_fitness_formula is not None:
                    s.composite_fitness_formula = view.composite_fitness_formula
                if view.composite_fitness_formula_short is not None:
                    self.short_formula_template = view.composite_fitness_formula_short
            case RoundStartView():
                self._proposer_node = view.node
            case OptimizerStepEnterView():
                self._step_node = view.node
            case VerificationEnterView():
                self._in_pass = True
                self._mark(_opened(view.verification, view.arm, view.rows))
            case BenchScoredView():
                for reading in (view.bench.origin, view.bench.selected):
                    if reading is not None:
                        self._place_bench(reading)
            case VerificationGradedView(verification=taken, reading=read):
                opened = _opened(taken.kind, taken.arm, len(taken.sample_keys))
                match read:
                    case None:
                        ended = opened.ended(
                            scored=len(taken.cells), level=None, failed_as=view.state
                        )
                    case BenchReading():
                        self._place_bench(read)
                        ended = opened.ended(scored=read.n, level=read.level, failed_as=None)
                    case VerifyReading(fresh=fresh):
                        ended = opened.ended(scored=fresh.n, level=fresh.fitness, failed_as=None)
                    case NoiseBand(fresh_fitness=fitness):
                        # Its rows are every rescore's, as its bracket opened them.
                        ended = _opened(taken.kind, taken.arm, len(taken.cells)).ended(
                            scored=len(taken.cells),
                            level=BandedValue(value=fitness, ci_lo=None, ci_hi=None),
                            failed_as=None,
                        )
                self._mark(ended)
        if record.event == "exit" and record.phase in (CampaignPhase.BENCH, CampaignPhase.VERIFY):
            self._in_pass = False
        if record.phase == CampaignPhase.VERIFY and record.event == "exit":
            # A verify or a noise floor is graded inside its bracket; a bench pass after it.
            s.verification = s.orphaned(spare="bench")
        if (
            record.phase == CampaignPhase.BENCH
            and record.event == "exit"
            and (around := self._around_bench) is not None
        ):
            self._around_bench = None
            state, step, s.measuring_arm = around
            self._set_state(state, step=step)

    def _handle_election(self, record: ElectionRecord) -> None:
        self._buffer.election = record

    def _handle_arm(self, record: ArmRecord) -> None:
        self._buffer.announce(record)
        if record.round != self._buffer.round_num:
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
        if self._buffer.take(record) is not None:
            return
        for held in self.state.verification:
            if (
                held.state == "running"
                and held.kind == record.role.value
                and held.arm.key == record.individual_id
            ):
                running = None if record.running is None else record.running.fitness
                self._mark(held.scored_one(running))
                return

    def _handle_race_standing(self, record: RaceStandingRecord) -> None:
        self._buffer.record_race_standing(
            record.member, record.current, record.n_samples, record.p_best
        )

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
                label=self._buffer.label(record.arm),
                position=record.position,
                prior_keys=record.prior_keys,
            )
        )

    def _refresh_open_sample_markers(self) -> None:
        s = self.state
        if not self._open_samples:
            s.current_query_payload = None
            s.current_position = None
            s.open_positions = []
            return
        sid, (query_text, arm, _depth) = next(iter(self._open_samples.items()))
        s.current_query_payload = query_text
        s.current_position = sid
        s.open_positions = list(self._open_samples)
        s.measuring_arm = None if arm is None else self._buffer.name(arm)

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
                label=None if arm is None else self._buffer.label(arm),
                position=self._waiting[0],
            )
            waiting_since = self._waiting[1]
        # The field holds one sender: the episode that has held longest.
        held = min(self._holds.values(), key=lambda r: r.gives_up_at, default=None)
        active = self._active_node()
        measuring = active == self._measurement_node
        current = CurrentRound(
            round=s.round,
            active_node=active,
            measurement_node=self._measurement_node,
            inner=self._inner,
            candidates=build_candidate_rows(self._buffer, self.short_formula_template),
            nodes=self._current_round_nodes(),
            racing=build_racing_block(self._buffer),
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
                "backpressure": None if held is None else held.reading,
                "at_time": self._at,
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
