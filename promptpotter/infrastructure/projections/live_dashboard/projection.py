from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import assert_never

from promptpotter.domain.bench import BenchReading
from promptpotter.domain.cycle_paths import Cut, CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.dashboard_rows import RoundSummary
from promptpotter.domain.paired_reading import ArmKey, ArmName
from promptpotter.domain.phase_views import (
    BenchEnterView,
    BenchGradedView,
    BenchScoredView,
    InitEnterView,
    InitExitView,
    MeasureEnterView,
    OptimizerStepEnterView,
    RoundStartView,
)
from promptpotter.domain.phases import STOP_REASON_INFO, CampaignPhase, DashboardState, RunPhase
from promptpotter.domain.results import Concluded, Measuring
from promptpotter.domain.results_health import is_degraded
from promptpotter.domain.round_audit import LoopWarning, NodeBlock
from promptpotter.domain.run_records import (
    ArmRecord,
    BackendWarningRecord,
    CycleRecord,
    ElectionRecord,
    ErrorRecord,
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
    RoundStandingRecord,
    RoundWarningRecord,
    RunPhaseRecord,
    RunWiringRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    TokenUsageRecord,
)
from promptpotter.domain.scoring import GradedCell, MeasuredCell, fitness_bands
from promptpotter.domain.spend import CeilingMeter, MeteredSpend, SpendRollup
from promptpotter.infrastructure.ledger import ledger_chain, open_with_history
from promptpotter.infrastructure.projections.audit_trail import build_node_block
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.projections.live_dashboard.blocks import (
    build_candidate_rows,
    build_racing_block,
)
from promptpotter.infrastructure.projections.live_dashboard.round_buffer import RoundBuffer
from promptpotter.infrastructure.projections.live_dashboard.state import (
    BackendWarning,
    BenchPassProgress,
    CatchUpLogEntry,
    CurrentRound,
    DashboardError,
    LiveDashboardState,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_standing_rounds
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)


_DASHBOARD_DEBOUNCE_S = 0.25
# The write holds the lock every ledger append takes, so the next one rests this many times its cost.
_PERSIST_REST_FACTOR = 9.0


# MEASURE, BENCH and optimizer steps are absent on purpose: sample records, a bracket and the enter view drive them.
_PHASE_TO_STATE: dict[str, DashboardState] = {
    CampaignPhase.INIT: DashboardState.INIT,
    CampaignPhase.ORIGIN: DashboardState.ORIGIN,
    CampaignPhase.PROPOSE: DashboardState.PROPOSING,
}


_CHECKIN_NODE = "checkin"


class LiveDashboardProjection(Projection):
    def __init__(
        self,
        cycle_dir: CycleDir,
        *,
        state_path: Path | None,
        hop: CycleHop,
        seed: LiveDashboardState,
    ) -> None:
        cycle_path = Path(cycle_dir)
        self.cycle_dir = cycle_path
        # None: a replay answering for a past moment, which must not overwrite the head's file.
        self.state_path = state_path
        self.state = LiveDashboardState.for_run(seed, hop=hop)
        self.short_formula_template: str | None = None
        self._buffer = RoundBuffer()
        self._closed: dict[int, RoundClosedRecord] = {}
        self._open_samples: dict[int, tuple[str, ArmKey | None, int]] = {}
        self._bench_readings: dict[int, BenchReading] = {}
        self._around_bench: tuple[DashboardState, str | None, ArmName | None] | None = None
        self._flight: tuple[int, int, int] = (0, 0, 0)
        self._affordable: int | None = None
        self._cell_reserve_usd: float | None = None
        self._waiting: tuple[int, float] | None = None
        self._holds: dict[str, ProviderHoldRecord] = {}
        self._sticky_llm_calls: dict[str, tuple[int, NodeBlock]] = {
            node: (seed.current_round.round, block)
            for node, block in seed.current_round.nodes.items()
        }
        self._in_flight: tuple[str, str] | None = None
        self._proposer_node: str | None = None
        self._measurement_node: str | None = None
        self._step_node: str | None = None
        # RLock: a boundary flush runs inside a handler that already holds it via `on_record`.
        self._persist_lock: threading.RLock = threading.RLock()
        self._persist_timer: threading.Timer | None = None
        self._persist_dirty: bool = False
        self._persist_cost_s: float = 0.0

        self._persist()

    @classmethod
    def for_session(
        cls,
        hop: CycleHop,
        *,
        tenant_root: str,
        resumed_from_round: int | None = None,
    ) -> LiveDashboardProjection | None:
        if not (tenant_root and hop.campaign_id and hop.cycle_id):
            return None

        cycle_dir = CycleDir(cycle_dir_for(WorkspaceDir(Path(tenant_root)), hop))
        return cls(
            cycle_dir,
            state_path=CycleLayout(Path(cycle_dir)).dashboard,
            hop=hop,
            seed=resolve_resume_state(
                Cut(cycle=cycle_dir, hop=hop), Path(cycle_dir), resumed_from_round
            ),
        )

    def _set_state(self, name: DashboardState, *, step: str | None = None) -> None:
        self.state.state = name
        self.state.optimizer_step = step
        self.state.state_since = utcnow_iso()

    def on_record(self, record: CycleRecord, offset: int) -> None:
        with self._persist_lock:
            super().on_record(record, offset)

    def _schedule_persist(self) -> None:
        if self.state_path is None:
            return
        self._persist_dirty = True
        # An armed timer is never replaced: staleness is bounded from the FIRST mutation.
        if self._persist_timer is not None:
            return
        rest = max(_DASHBOARD_DEBOUNCE_S, self._persist_cost_s * _PERSIST_REST_FACTOR)
        timer = threading.Timer(rest, self._fire_debounced_persist)
        timer.daemon = True
        self._persist_timer = timer
        timer.start()

    def _fire_debounced_persist(self) -> None:
        try:
            with self._persist_lock:
                # Before the dirty test: left set on the not-dirty path, it disarms the debounce.
                self._persist_timer = None
                if not self._persist_dirty:
                    return
                self._persist_dirty = False
                self._persist()
        except Exception:
            logger.exception("debounced dashboard persist failed")

    def _flush_pending_persist(self) -> None:
        with self._persist_lock:
            if self._persist_timer is not None:
                self._persist_timer.cancel()
                self._persist_timer = None
            self._persist_dirty = False
            self._persist()

    def drain(self) -> None:
        self._flush_pending_persist()

    def _handle_run_wiring(self, record: RunWiringRecord) -> None:
        for name, value in LiveDashboardState.wired(record).items():
            setattr(self.state, name, value)
        # A launch hears a hold still on at its first send, so one the last launch saw open ends here.
        self._holds.clear()
        self._flush_pending_persist()

    def _handle_run_phase(self, record: RunPhaseRecord) -> None:
        if self.state.declared_phase == record.run_phase:
            return
        self.state.declared_phase = record.run_phase
        if record.run_phase == RunPhase.TERMINAL:
            self.state.stop_reason = record.stop_reason
            self._set_state(DashboardState.STOPPED)
            self.state.measuring_arm = None
        self._flush_pending_persist()

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
        self._flush_pending_persist()

    def _handle_provider_hold(self, record: ProviderHoldRecord) -> None:
        self._holds[record.sender] = record
        self._flush_pending_persist()

    def _handle_provider_eased(self, record: ProviderEasedRecord) -> None:
        self._holds.pop(record.sender, None)
        self._flush_pending_persist()

    def _handle_round_entered(self, record: RoundEnteredRecord) -> None:
        self.state.round = record.round
        self._buffer.enter(record)
        self._flush_pending_persist()

    def _move_to_round(self, round_num: int | None) -> None:
        if round_num is not None:
            self.state.round = round_num
        if self._buffer.round_num != self.state.round:
            self._buffer.reset(self.state.round)

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:
        self._closed[record.round] = record
        self.state.level_series = record.level_series
        self._flush_pending_persist()

    def _handle_round_standing(self, record: RoundStandingRecord) -> None:
        self.state.run_standing = record.run_standing
        closed = self._closed.get(record.round)
        if closed is not None:
            prior = [r for r in self.state.rounds if r.round != closed.round]
            summary = RoundSummary.of(closed, record.panel_precision)
            self.state.rounds = sorted([*prior, self._with_bench(summary)], key=lambda r: r.round)
        self._flush_pending_persist()

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
                s.rounds = [r for r in s.rounds if r.round < s.round]
            case MeasureEnterView():
                self._measurement_node = view.node
            case OptimizerStepEnterView():
                self._step_node = view.node
            case BenchEnterView():
                s.bench_pass = BenchPassProgress(
                    subject=view.subject,
                    arm=view.arm,
                    label=view.label,
                    sp_hash=view.sp_hash,
                    round=view.round,
                    rows=view.rows,
                    scored=0,
                )
            case BenchScoredView():
                s.bench_score = view.bench
                for reading in (view.bench.origin, view.bench.selected):
                    if reading is not None:
                        self._place_bench(reading)
            case BenchGradedView():
                if (reading := view.reading) is not None:
                    self._place_bench(reading)
        if record.phase == CampaignPhase.BENCH and record.event == "exit":
            s.bench_pass = None
            if (around := self._around_bench) is not None:
                self._around_bench = None
                state, step, s.measuring_arm = around
                self._set_state(state, step=step)
        self._flush_pending_persist()

    def _handle_election(self, record: ElectionRecord) -> None:
        self._buffer.election = record
        self._flush_pending_persist()

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
        self._schedule_persist()

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
        if self.state.state is not DashboardState.BENCH:
            self._set_state(DashboardState.SCORING)
        self._schedule_persist()

    def _handle_sample_scored(self, record: SampleScoredRecord) -> None:
        qi, qt = record.sample_idx or 0, record.sample_total or 0
        self._open_samples.pop(record.position, None)
        self._absorb_sample_scored(record.result.facts, last_in_candidate=(qi + 1 >= qt))
        # A pass no arm walks (a bench pass, a verify) moves the run's scalars, never the round's arms.
        if self._buffer.take(record) is None and (bench_pass := self.state.bench_pass) is not None:
            self.state.bench_pass = bench_pass.model_copy(
                update={
                    "scored": bench_pass.scored + 1,
                    "accuracy": None if record.running is None else record.running.accuracy,
                }
            )
        self._schedule_persist()

    def _handle_race_standing(self, record: RaceStandingRecord) -> None:
        self._buffer.record_race_standing(
            record.member, record.current, record.n_samples, record.p_best
        )
        self._schedule_persist()

    def _handle_flight(self, record: FlightRecord) -> None:
        self._flight = (record.out, record.allowed, record.most)
        self._affordable = record.affordable
        self._cell_reserve_usd = record.cell_usd
        waiting = record.waiting
        self._waiting = None if waiting is None else (waiting.position, waiting.since)
        self._schedule_persist()

    def _handle_race_catch_up(self, record: RaceCatchUpRecord) -> None:
        self._append_catch_up(
            CatchUpLogEntry(
                member=record.member,
                round=record.round,
                arm=record.arm,
                label=self._buffer.label(record.arm),
                position=record.position,
                prior_ids=record.prior_ids,
            )
        )
        self._schedule_persist()

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
        if s.state is not DashboardState.BENCH:
            self._set_state(
                DashboardState.BETWEEN_CANDIDATES
                if last_in_candidate
                else DashboardState.BETWEEN_SAMPLES
            )

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        self.state.spend.bank(record)
        self.state.spend_by_round.setdefault(str(record.spend_round), SpendRollup()).bank(record)
        self._schedule_persist()

    def spend_metered(self, meters: CeilingMeter) -> MeteredSpend:
        return MeteredSpend.of(self.state.spend, meters)

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        self._in_flight = (record.call_id, record.node)
        self._schedule_persist()

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        self._sticky_llm_calls[record.node] = (self.state.round, build_node_block(record))
        if self._in_flight is not None and record.call_id and self._in_flight[0] == record.call_id:
            self._in_flight = None
        self._schedule_persist()

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        """A heartbeat: re-persists so `wallclock_serialized_at` stays fresh while no other record fires."""
        del record
        self._schedule_persist()

    def _handle_error(self, record: ErrorRecord) -> None:
        info = STOP_REASON_INFO[record.stop_reason]
        self.state.error = DashboardError(
            kind=record.kind,
            message=record.message,
            stop_reason=record.stop_reason,
            label=info.label,
            next_step=info.next_step,
        )
        self._schedule_persist()

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
        self._flush_pending_persist()

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

    def _persist(self) -> None:
        if self.state_path is None:
            return
        started = time.perf_counter()
        self.compose()
        try:
            write_json(self.state_path, self.state.model_dump(), default=str)
            self._persist_cost_s = time.perf_counter() - started
        except OSError as exc:
            # Logged, never raised or emitted: a round warning appends to the ledger this view subscribes to.
            logger.warning(
                "dashboard write refused — %s: %s · %s is stale until the next write lands "
                "(the run is unaffected; the ledger holds every fact this file shows)",
                exc.__class__.__name__,
                exc,
                self.state_path,
            )

    def compose(self) -> LiveDashboardState:
        s = self.state
        s.at_offset = self.at_offset
        s.current_round = CurrentRound(
            round=s.round,
            active_node=self._active_node(),
            measurement_node=self._measurement_node,
            candidates=build_candidate_rows(self._buffer, self.short_formula_template),
            fitness_bands=fitness_bands(
                GradedCell.of(s.facts, s.grade)
                for walked in self._buffer.samples.values()
                for s in walked
            ),
            nodes=self._current_round_nodes(),
            racing=build_racing_block(self._buffer),
        )
        if any(self._flight):
            s.in_flight, s.lookahead_allowed, s.lookahead_most = self._flight
        else:
            s.in_flight = s.lookahead_allowed = 0
            s.lookahead_most = (
                None if s.arms_per_round is None else (s.arms_per_round + 1) * s.sp_budget_round
            )
        s.lookahead_affordable = self._affordable
        s.cell_reserve_usd = self._cell_reserve_usd
        opened = self._open_samples.get(self._waiting[0]) if self._waiting else None
        if self._waiting is None or opened is None:
            s.waiting_on, s.waiting_since = None, None
        else:
            arm = opened[1]
            owner = "pass" if arm is None else self._buffer.label(arm)
            s.waiting_on = f"{owner} · sample {self._waiting[0]}"
            s.waiting_since = self._waiting[1]
        # The field holds one sender: the episode that has held longest.
        held = min(self._holds.values(), key=lambda r: r.gives_up_at, default=None)
        s.backpressure = None if held is None else held.reading
        s.wallclock_serialized_at = utcnow_iso()
        return s


def resolve_resume_state(
    seed: Cut,
    active_cycle_dir: Path,
    resumed_from_round: int | None,
) -> LiveDashboardState:
    prior = fold_at(seed)
    if prior is None:
        raise RuntimeError(
            f"{seed.hop.campaign_id}/{seed.hop.cycle_id} holds no RunWiringRecord — a run's "
            "dashboard is written only once its cycle has declared what it runs under"
        )
    surviving = [
        r for r in prior.rounds if resumed_from_round is None or r.round < resumed_from_round
    ]
    standing = scan_standing_rounds(ledger_chain(CycleDir(active_cycle_dir), moment=None))
    return prior.model_copy(
        update={
            "rounds": surviving,
            "round": max(prior.round, max(standing.rounds, default=0)),
            "run_standing": standing.standing,
            "level_series": standing.level_series,
        }
    )


def fold_at(cut: Cut) -> LiveDashboardState | None:
    """`None`: the cut holds no `RunWiringRecord` yet (a check-in still authoring its origin)."""
    view = _folded(cut)
    return None if view is None else view.compose()


def materializing_over(cycle_dir: CycleDir, hop: CycleHop) -> LiveDashboardProjection | None:
    view = _folded(Cut(cycle=cycle_dir, hop=hop))
    if view is not None:
        view.state_path = CycleLayout(Path(cycle_dir)).dashboard
    return view


def _folded(cut: Cut) -> LiveDashboardProjection | None:
    history = open_with_history(cut.cycle)
    limit = None if cut.offset is None else cut.offset + 1
    wiring = next((r for _, r in history.iter(limit) if isinstance(r, RunWiringRecord)), None)
    if wiring is None:
        return None
    view = LiveDashboardProjection(
        cut.cycle,
        state_path=None,
        hop=cut.hop,
        seed=LiveDashboardState.declared(cut.hop, wiring),
    )
    # Every record of the cut, those before the wiring included: a check-in bills its cycle before Start.
    for offset, record in history.iter(limit):
        view.on_record(record, offset)
    return view


__all__ = ["LiveDashboardProjection", "fold_at", "materializing_over", "resolve_resume_state"]
