from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import assert_never

from promptpotter.domain.bench import BenchReading
from promptpotter.domain.cycle_paths import Cut, CycleDir
from promptpotter.domain.dashboard_rows import RoundSummary, VerificationKind, VerificationMark
from promptpotter.domain.paired_reading import READING_STATE_INFO, ArmKey, ArmName
from promptpotter.domain.phase_views import (
    BenchEnterView,
    BenchGradedView,
    BenchScoredView,
    InitEnterView,
    InitExitView,
    MeasureEnterView,
    OptimizerStepEnterView,
    RoundStartView,
    VerifyEnterView,
    VerifyGradedView,
)
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    CampaignPhase,
    DashboardState,
    ErrorRecord,
    RunPhase,
)
from promptpotter.domain.results import Concluded, Measuring, RunStanding
from promptpotter.domain.results_health import is_degraded
from promptpotter.domain.round_audit import LoopWarning, NodeBlock
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
    RoundWarningRecord,
    RunPhaseRecord,
    RunWiringRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    TokenUsageRecord,
)
from promptpotter.domain.scoring import GradedCell, MeasuredCell, fitness_bands
from promptpotter.domain.spend import CeilingMeter, MeteredSpend, SpendRollup
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
    DashboardError,
    LiveDashboardState,
)
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import (
    LedgerIndex,
    LedgerSpan,
    folded,
)

logger = logging.getLogger(__name__)


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
        RoundWarningRecord,
    }
)


# MEASURE, BENCH and optimizer steps are absent on purpose: sample records, a bracket and the enter view drive them.
_PHASE_TO_STATE: dict[str, DashboardState] = {
    CampaignPhase.INIT: DashboardState.INIT,
    CampaignPhase.ORIGIN: DashboardState.ORIGIN,
    CampaignPhase.PROPOSE: DashboardState.PROPOSING,
}


_CHECKIN_NODE = "checkin"

_VERIFY_UNREAD = "The pass ended without a reading; the verb that sent it says why."


def _opened(kind: VerificationKind, arm: ArmName, rows: int) -> VerificationMark:
    return VerificationMark(
        kind=kind, arm=arm, state="running", rows=rows, scored=0, level=None, failure=None
    )


class LiveDashboardProjection(Projection):
    def __init__(self) -> None:
        # No state stands before the first `RunWiringRecord`: what lands earlier waits here.
        self._unwired: list[tuple[CycleRecord, int]] | None = []
        self.state: LiveDashboardState
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
        self._proposer_node: str | None = None
        self._measurement_node: str | None = None
        self._step_node: str | None = None

    def value(self) -> LiveDashboardState | None:
        """`None`: no `RunWiringRecord` yet (a check-in still authoring its origin)."""
        return None if self._unwired is not None else self.composed().model_copy(deep=True)

    def on_record(self, record: CycleRecord, offset: int) -> None:
        self.at_offset = offset
        if type(record) not in self._routes:
            return
        if self._unwired is not None:
            if not isinstance(record, RunWiringRecord):
                self._unwired.append((record, offset))
                return
            self.state = LiveDashboardState.declared(record)
            # Folded under the state it opens: a check-in bills its cycle before Start.
            unwired, self._unwired = self._unwired, None
            for held, at in unwired:
                self._fold(held, at)
        self._fold(record, offset)

    def _fold(self, record: CycleRecord, offset: int) -> None:
        self._at = record.timestamp
        super().on_record(record, offset)

    def _set_state(self, name: DashboardState, *, step: str | None = None) -> None:
        self.state.state = name
        self.state.optimizer_step = step
        self.state.state_since = self._at

    def _handle_run_wiring(self, record: RunWiringRecord) -> None:
        for name, value in LiveDashboardState.launched(record).items():
            setattr(self.state, name, value)
        self.state.verification = [
            m.orphaned() if m.state == "running" else m for m in self.state.verification
        ]
        self._launch()

    def _handle_run_phase(self, record: RunPhaseRecord) -> None:
        if self.state.declared_phase == record.run_phase:
            return
        self.state.declared_phase = record.run_phase
        if record.run_phase == RunPhase.TERMINAL:
            self.state.stop_reason = record.stop_reason
            self._set_state(DashboardState.STOPPED)
            self.state.measuring_arm = None

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
            case MeasureEnterView():
                self._measurement_node = view.node
            case OptimizerStepEnterView():
                self._step_node = view.node
            case BenchEnterView():
                self._in_pass = True
                arm = ArmName(key=view.arm, round=view.round, label=view.label)
                self._mark(_opened("bench", arm, view.rows))
            case VerifyEnterView():
                self._in_pass = True
                self._mark(_opened("verify", view.arm, view.rows))
            case BenchScoredView():
                s.bench_score = view.bench
                for reading in (view.bench.origin, view.bench.selected):
                    if reading is not None:
                        self._place_bench(reading)
            case BenchGradedView(bench_pass=taken, reading=reading):
                if reading is not None:
                    self._place_bench(reading)
                arm = ArmName(key=ArmKey(taken.candidate_id), round=taken.round, label=view.label)
                self._mark(
                    _opened("bench", arm, len(taken.sample_keys)).ended(
                        scored=len(taken.cells) if reading is None else reading.n,
                        level=None if reading is None else reading.level,
                        failure=None
                        if reading is not None
                        else READING_STATE_INFO[view.state].sentence,
                    )
                )
            case VerifyGradedView(verify_pass=banked, reading=read):
                self._mark(
                    _opened("verify", banked.arm, len(banked.positions)).ended(
                        scored=read.fresh.n, level=read.fresh.accuracy, failure=None
                    )
                )
        if record.event == "exit" and record.phase in (CampaignPhase.BENCH, CampaignPhase.VERIFY):
            self._in_pass = False
        if record.phase == CampaignPhase.VERIFY and record.event == "exit":
            # A verify is graded inside its bracket; a bench pass after it.
            s.verification = [
                m.ended(scored=m.scored, level=None, failure=_VERIFY_UNREAD)
                if m.kind == "verify" and m.state == "running"
                else m
                for m in s.verification
            ]
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
                "bench": reading,
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
                running = None if record.running is None else record.running.accuracy
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
        query_time = facts.elapsed_s

        # The second arm is not redundant: `pipeline.error` is the BACKEND faulting a row PP classified clean.
        if facts.errored or facts.pipeline.error:
            s.error_count += 1

        s.total_queries_scored += 1
        if not facts.cached:
            s.total_backend_calls += 1
            if is_degraded(facts):
                s.degraded_count += 1

        self._refresh_open_sample_markers()
        s.last_query_elapsed_s = None if query_time is None else round(query_time, 2)
        if not self._in_pass:
            self._set_state(
                DashboardState.BETWEEN_CANDIDATES
                if last_in_candidate
                else DashboardState.BETWEEN_SAMPLES
            )

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        self.state.spend.bank(record)
        self.state.spend_by_round.setdefault(str(record.spend_round), SpendRollup()).bank(record)

    def spend_metered(self, meters: CeilingMeter) -> MeteredSpend:
        return MeteredSpend.of(self.state.spend, meters)

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        self._in_flight = (record.call_id, record.node)

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        # Taken so a streaming call moves `at_time`, which `_fold` stamps before every handler.
        del record

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        self._sticky_llm_calls[record.node] = (self.state.round, build_node_block(record))
        if self._in_flight is not None and record.call_id and self._in_flight[0] == record.call_id:
            self._in_flight = None

    def _handle_error(self, record: ErrorRecord) -> None:
        info = STOP_REASON_INFO[record.stop_reason]
        self.state.error = DashboardError(
            kind=record.kind,
            message=record.message,
            stop_reason=record.stop_reason,
            label=info.label,
            next_step=info.next_step,
        )

    def _handle_round_warning(self, record: RoundWarningRecord) -> None:
        warning = LoopWarning(
            ts=record.timestamp,
            kind=record.kind,
            severity=record.severity,
            message=record.message,
            round=record.round,
            detail=dict(record.detail),
        )
        self.state.recent_loop_warnings = [*self.state.recent_loop_warnings, warning][-10:]

    def _append_catch_up(self, entry: CatchUpLogEntry) -> None:
        self.state.catch_up_log = [*self.state.catch_up_log, entry][-256:]

    def _active_node(self) -> str | None:
        if self._in_flight is not None:
            return self._in_flight[1]
        state = self.state.state
        match state:
            case DashboardState.INIT | DashboardState.ORIGIN:
                return _CHECKIN_NODE
            case DashboardState.PROPOSING:
                return self._proposer_node
            case (
                DashboardState.SCORING
                | DashboardState.BETWEEN_SAMPLES
                | DashboardState.BETWEEN_CANDIDATES
            ):
                return self._measurement_node
            case DashboardState.OPTIMIZER_STEP:
                return self._step_node
            case DashboardState.BENCH | DashboardState.STOPPED:
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
        """Shares the fold's own fields, so it is read before the next record lands."""
        s = self.state
        if any(self._flight):
            in_flight, allowed, most = self._flight
        else:
            in_flight = allowed = 0
            most = s.round_cells_ceiling
        opened = self._open_samples.get(self._waiting[0]) if self._waiting else None
        waiting_on: str | None = None
        waiting_since: float | None = None
        if self._waiting is not None and opened is not None:
            arm = opened[1]
            owner = "pass" if arm is None else self._buffer.label(arm)
            waiting_on = f"{owner} · sample {self._waiting[0]}"
            waiting_since = self._waiting[1]
        # The field holds one sender: the episode that has held longest.
        held = min(self._holds.values(), key=lambda r: r.gives_up_at, default=None)
        current = CurrentRound(
            round=s.round,
            active_node=self._active_node(),
            measurement_node=self._measurement_node,
            candidates=build_candidate_rows(self._buffer, self.short_formula_template),
            fitness_bands=fitness_bands(
                GradedCell.of(w.facts, w.grade)
                for walked in self._buffer.samples.values()
                for w in walked
            ),
            nodes=self._current_round_nodes(),
            racing=build_racing_block(self._buffer),
        )
        return s.model_copy(
            update={
                "at_offset": self.at_offset,
                "current_round": current,
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


class DashboardCheckout(LiveDashboardProjection):
    """`dashboard.json`: the fold its run goes on folding, written out at a boundary or settled."""

    def __init__(self, folded: LiveDashboardProjection, path: Path) -> None:
        # Adopts the one-shot fold `folded` hands out; the lock and the timer are its own.
        vars(self).update(vars(folded))
        self._path = path
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._dirty = False
        self._cost_s = 0.0

    @classmethod
    def over(cls, cycle_dir: CycleDir) -> DashboardCheckout | None:
        """``None`` where the cycle has declared no wiring."""
        *prefix, own = ledger_chain(cycle_dir, moment=None)
        head = folded(own.path, LiveDashboardProjection, seed=_seed(prefix), until=None)
        if head._unwired is not None:
            return None
        checkout = cls(head, CycleLayout(Path(cycle_dir)).dashboard)
        checkout.drain()
        return checkout

    def on_record(self, record: CycleRecord, offset: int) -> None:
        with self._lock:
            super().on_record(record, offset)
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
        timer = threading.Timer(rest, self._settled)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _settled(self) -> None:
        try:
            with self._lock:
                # Before the dirty test: left set on the not-dirty path, it disarms the debounce.
                self._timer = None
                if self._dirty:
                    self._write()
        except Exception:
            logger.exception("debounced dashboard persist failed")

    def drain(self) -> None:
        with self._lock:
            self._write()

    def _write(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._dirty = False
        started = time.perf_counter()
        try:
            write_json(self._path, self.composed().model_dump(), default=str)
            self._cost_s = time.perf_counter() - started
        except OSError as exc:
            # Logged, never raised or emitted: a round warning appends to the ledger this view subscribes to.
            logger.warning(
                "dashboard write refused — %s: %s · %s is stale until the next write lands "
                "(the run is unaffected; the ledger holds every fact this file shows)",
                exc.__class__.__name__,
                exc,
                self._path,
            )


def fold_at(cut: Cut) -> LiveDashboardState | None:
    """`None`: the cut holds no `RunWiringRecord` yet (a check-in still authoring its origin)."""
    *prefix, own = ledger_chain(cut.cycle, moment=None)
    until = None if cut.offset is None else cut.offset + 1
    return LedgerIndex.of(own.path, ()).view(LiveDashboardProjection, until, seed=_seed(prefix))


def _seed(prefix: list[LedgerSpan]) -> LiveDashboardProjection | None:
    seed: LiveDashboardProjection | None = None
    for span in prefix:
        assert span.until is not None, "a fork's parent is read to its cut"
        seed = LedgerIndex.of(span.path, ()).prefix(LiveDashboardProjection, span.until, seed=seed)
    return seed
