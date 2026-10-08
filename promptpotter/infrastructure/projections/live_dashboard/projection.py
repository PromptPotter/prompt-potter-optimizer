from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, assert_never, cast

from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.bench import BenchReading, BenchScore
from promptpotter.domain.cycle_paths import Cut, CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.dashboard_rows import RoundSummary, RunStanding
from promptpotter.domain.phases import (
    CONTROL_PHASE,
    CampaignPhase,
    DashboardState,
    PhaseEvent,
    RunPhase,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.results import (
    DisplayMetric,
    SharedCellPoint,
    best_round_on_shared_cells,
    candidate_label,
)
from promptpotter.domain.results_health import is_degraded
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    CycleRecord,
    ElectionRecord,
    ErrorRecord,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    RoundWarningRecord,
    SnapshotRecord,
    TokenUsageRecord,
    view_fields,
)
from promptpotter.domain.scoring import (
    QueryMeasurement,
    recorded_elapsed_s,
)
from promptpotter.domain.spend import CeilingMeter, MeteredSpend, SpendRollup
from promptpotter.infrastructure.ledger import CycleEventLog, open_with_history
from promptpotter.infrastructure.projections.audit_trail import build_node_block
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.projections.live_dashboard.blocks import (
    build_candidate_rows,
    build_racing_block,
)
from promptpotter.infrastructure.projections.live_dashboard.round_buffer import RoundBuffer
from promptpotter.infrastructure.projections.live_dashboard.round_summary import (
    build_round_summary,
    origin_rows_from_disk,
    round_result_from_disk,
)
from promptpotter.infrastructure.projections.live_dashboard.state import (
    BackendWarning,
    BenchPassProgress,
    CatchUpLogEntry,
    CurrentRound,
    DashboardError,
    LiveDashboardState,
    LoopWarning,
    RunLimits,
)
from promptpotter.infrastructure.runtime_flags import (
    armed_run_limits,
    effective_lookahead,
    read_sample_lookahead,
    sample_lookahead_auto,
)
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import (
    ROUND_GLOB,
    CycleLayout,
    cycle_dir_for,
    round_number,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.instrument import NO_ROUND_SLOT

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from promptpotter.domain.connector import MeasuredUnit
    from promptpotter.domain.results import RoundResult

logger = logging.getLogger(__name__)


# Coalesces the per-sample / per-token / per-LLM-call hot paths onto one disk write. Phase
# boundaries still flush immediately, so `dashboard.json` is current at every transition.
_DASHBOARD_DEBOUNCE_S = 0.25
# The write holds the lock every ledger append takes and its cost grows with the live round, so
# the next write waits this many times what the last one cost.
_PERSIST_REST_FACTOR = 9.0


# MEASURE absent: driven by sample_started / sample_scored. An optimizer's own phase is absent
# too: its enter view names its node and activity (`OptimizerStepEnterView`), so it maps itself.
_PHASE_TO_STATE: dict[str, DashboardState] = {
    CampaignPhase.INIT: DashboardState.INIT,
    CampaignPhase.ORIGIN: DashboardState.ORIGIN,
    CampaignPhase.PROPOSE: DashboardState.PROPOSING,
    CampaignPhase.BENCH: DashboardState.BENCH,
}


# The bench's own check-in node, which runs around every optimizer's loop.
_CHECKIN_NODE = "checkin"


class LiveDashboardProjection(Projection):
    """Per-cycle dashboard writer; not an optimizer checkpoint."""

    def __init__(
        self,
        cycle_dir: CycleDir,
        *,
        state_path: Path | None,
        hop: CycleHop,
        session_id: str,
        arms_per_round: int | None,
        sp_budget_round: int,
        display_metric: DisplayMetric,
        langfuse_trace_url: str | None = None,
        resume_from: LiveDashboardState | None = None,
    ) -> None:
        cycle_path = Path(cycle_dir)
        self.cycle_dir = cycle_path
        # Where this fold MATERIALIZES itself, or ``None`` for one that answers a question
        # instead — a replay serving a past moment must not overwrite the head's file with it.
        # Spelled by the caller rather than derived from `cycle_dir`, because both modes read
        # that same directory and only the write target tells them apart.
        self.state_path = state_path
        # The schema IS the on-disk shape (`_persist` dumps this instance), so it also owns
        # which fields a resume inherits and which this process stamps fresh.
        self.state = LiveDashboardState.for_run(
            resume_from,
            hop=hop,
            session_id=session_id,
            arms_per_round=arms_per_round,
            sp_budget_round=sp_budget_round,
            langfuse_trace_url=langfuse_trace_url,
            display_metric=display_metric,
        )
        self.short_formula_template: str | None = None
        self._buffer = RoundBuffer()
        # Launched, not yet absorbed, in launch order: HEAD drives the in-flight markers, and
        # whatever remains at candidate close was discarded.
        # sample_id -> (query_text, candidate_idx, cand_total, depth)
        # The depth it LAUNCHED at is what separates look-ahead cost from a plain failure.
        self._open_samples: dict[int, tuple[str, int, int, int]] = {}
        # A bench reading names a round and a searchpoint; the pass's own events name the label.
        self._bench_labels: dict[tuple[int, str], str] = {}
        self._bench_readings: dict[int, BenchReading] = {}
        # The last `flight` reading (out, allowed, most); all zero while nothing is scoring. And the
        # call a decision waits on, as (sample_id, launched at), and the provider holding calls.
        self._flight: tuple[int, int, int] = (0, 0, 0)
        self._affordable: int | None = None
        self._cell_reserve_usd: float | None = None
        self._waiting: tuple[int, float] | None = None
        self._backpressure: BackpressureReading | None = None
        # Sticky LLM-call mirror for ``current_round.nodes`` — owned here, not on the
        # audit-trail, which records the same event independently into its round flush. A resume
        # opens on the blocks its seed's fold held, so they show before the first new call lands.
        self._sticky_llm_calls: dict[str, dict[str, Any]] = (
            {} if resume_from is None else dict(resume_from.current_round.nodes)
        )
        # ``(call_id, node)`` of the optimizer call in progress — view-private, because the ONE
        # thing it decides is which node ``_active_node`` lights. It was a served field for a
        # reader that never arrived.
        self._in_flight: tuple[str, str] | None = None
        # The nodes the ledger named at `propose:enter`, `measure:enter` and an optimizer step's.
        self._proposer_node: str | None = None
        self._measurement_node: str | None = None
        self._step_node: str | None = None
        # RLock so the boundary-flush path can be called from inside a handler already holding
        # it via `on_record`; the Timer thread takes the same lock and cannot race a mutation.
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
        session_id: str,
        arms_per_round: int | None,
        sp_budget_round: int,
        display_metric: DisplayMetric,
        langfuse_trace_url: str | None = None,
        resumed_from_round: int | None = None,
        max_cells_in_flight: int | None = None,
        measured_unit: MeasuredUnit | None = None,
        stamps_theta: bool = False,
    ) -> LiveDashboardProjection | None:
        """Seeded from the cycle's own history — a fork's walks its parent's ledger up to the cut
        stamped at mint, never past it, while counting its own copied round files."""
        if not (tenant_root and session_id and hop.campaign_id and hop.cycle_id):
            return None

        cycle_dir = CycleDir(cycle_dir_for(WorkspaceDir(Path(tenant_root)), hop))
        resume_from = resolve_resume_state(
            Cut(cycle=cycle_dir, hop=hop), Path(cycle_dir), resumed_from_round
        )
        view = cls(
            cycle_dir,
            state_path=CycleLayout(Path(cycle_dir)).dashboard,
            hop=hop,
            session_id=session_id,
            arms_per_round=arms_per_round,
            sp_budget_round=sp_budget_round,
            display_metric=display_metric,
            langfuse_trace_url=langfuse_trace_url,
            resume_from=resume_from,
        )
        # Stamped at WIRING, not on a phase event: origin scoring runs before INIT fires, and the
        # browser reads the `1` default as "this backend holds one sample" and disables the
        # control. After `resume_from` — the live connector outranks a pair the prior run wrote.
        if max_cells_in_flight is not None:
            view.state.max_cells_in_flight = max_cells_in_flight
        if measured_unit is not None:
            view.state.measured_unit = measured_unit
        # Campaign-constant, unlike the two above: the same selector runs every round, so this
        # is never left at the resumed prior's stamp.
        view.state.stamps_theta = stamps_theta
        return view

    def stamp_run_limits(self, limits: RunLimits) -> None:
        """The ceilings are WIRING, but they are not knowable at wiring: the wallet and the
        operator's carried ceiling compose into the config AFTER this view is built. Published at
        that seam instead — still before the origin scores, which is the whole point."""
        self.state.run_limits = limits

    @classmethod
    def write_launch_stop(
        cls,
        cycle_dir: CycleDir,
        *,
        hop: CycleHop,
        session_id: str = "",
        exc: BaseException,
        stop_reason: StopReason,
    ) -> None:
        """Stamps a cycle whose run stopped BEFORE the projection bound, so the tree shows what
        happened. Pair with ``mark_finished`` only on a terminal stop — a ``finished_at`` unresumes
        a pause.

        Both facts go on the LEDGER and the file is the fold of it: ``derive_run_phase`` reads the
        declaration there, and an error written only into this file is one no replay shows."""
        cycle_path = Path(cycle_dir)
        interrupted = stop_reason_outcome(stop_reason) is StopOutcome.PAUSED
        ledger = CycleEventLog.open(cycle_dir)
        if not interrupted:
            ledger.append(
                ErrorRecord(
                    kind="launch_failed",
                    message=str(exc) or type(exc).__name__,
                    stop_reason=stop_reason,
                )
            )
        ledger.append(
            PhaseRecord(
                phase=CONTROL_PHASE,
                event=str(RunPhase.PAUSED if interrupted else RunPhase.TERMINAL),
                payload={} if interrupted else {"stop_reason": stop_reason.value},
            )
        )
        state = resolve_resume_state(Cut(cycle=CycleDir(cycle_path), hop=hop), cycle_path, None)
        if session_id:
            state.session_id = session_id
        write_json(CycleLayout(cycle_path).dashboard, state.model_dump(), default=str)

    # -- State transitions ----------------------------------------------------

    def _set_state(self, name: DashboardState, *, step: str | None = None) -> None:
        self.state.state = name
        self.state.optimizer_step = step
        self.state.state_since = utcnow_iso()

    # -- Write coalesce -------------------------------------------------------

    def on_record(self, record: CycleRecord, offset: int) -> None:
        with self._persist_lock:
            super().on_record(record, offset)

    def _schedule_persist(self) -> None:
        """An already-armed timer is left to run rather than replaced — the flush reads
        ``_persist_dirty``, not the event, so staleness is bounded from the FIRST mutation.
        A fold that materializes nothing arms no timer: a replay would otherwise leave one
        daemon thread per request behind it."""
        if self.state_path is None:
            return
        self._persist_dirty = True
        if self._persist_timer is not None:
            return
        rest = max(_DASHBOARD_DEBOUNCE_S, self._persist_cost_s * _PERSIST_REST_FACTOR)
        timer = threading.Timer(rest, self._fire_debounced_persist)
        timer.daemon = True
        self._persist_timer = timer
        timer.start()

    def _fire_debounced_persist(self) -> None:
        """Runs on a Timer thread; swallows exceptions so a torn-down cycle dir cannot reach the
        daemon-thread default handler."""
        try:
            with self._persist_lock:
                # Before the dirty test: `_schedule_persist` reads this slot to decide whether a
                # flush is armed, so leaving it set on the not-dirty path disarms the debounce.
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

    # -- Ledger subscription (sole ingress) -----------------------------------
    # Phases → scalars; snapshots → per-round candidate structures. No second dispatch.

    def _handle_phase(self, record: PhaseRecord) -> None:
        if record.phase == CONTROL_PHASE:
            # The SOLE writer of `declared_phase` — and nothing here writes `run_phase`, which is
            # wire-only and derived at the read. Flushing bumps the mtime, so the 304-cached route
            # re-derives immediately. TERMINAL arrives here like every other phase, so a fold
            # of the ledger reports a stopped cycle as stopped.
            event_phase = RunPhase(record.event)
            if self.state.declared_phase == event_phase:
                return
            self.state.declared_phase = event_phase
            if event_phase == RunPhase.TERMINAL:
                self.state.stop_reason = StopReason(record.payload["stop_reason"])
                self._set_state(DashboardState.STOPPED)
                # A stopped cycle is running nothing (`_active_node`'s STOPPED arm), so it is
                # scoring no candidate: drop the marker rather than leave the final round's
                # last candidate claiming the slot on every surface that outlives the run.
                # Paused keeps it — it says where the run stopped.
                self.state.candidate = ""
            self._flush_pending_persist()
            return

        if record.phase == "backend" and record.event == "warning":
            # Surface backend retries (429 / 5xx / transport) — retry behaviour itself is unchanged.
            payload = dict(record.payload)
            self.state.backend_retry_count += 1
            warning = BackendWarning(
                ts=utcnow_iso(),
                kind=payload.get("kind", "unknown"),
                attempt=payload.get("attempt"),
                max_attempts=payload.get("max_attempts"),
                wait_s=payload.get("wait_s"),
                error_class=payload.get("error_class"),
                status_code=payload.get("status_code"),
                final=bool(payload.get("final", False)),
                query=payload.get("query"),
                detail=payload.get("detail"),
            )
            self.state.recent_backend_warnings = [*self.state.recent_backend_warnings, warning][
                -10:
            ]
            self._flush_pending_persist()
            return

        if record.phase == "round" and record.event == "complete":
            # A round can close TWICE: round 0 re-persists once the ruler warms, since the
            # origin's θ cannot be fit before a second arm exists. That re-emit carries only
            # this lean record, so absorbing the correction here is what keeps the served
            # `rounds[0]` from holding a COLD θ everything later differences against.
            raw = record.payload.get("ability")
            if isinstance(raw, dict):
                ability = AbilityReading.model_validate(raw)
                self.state.rounds = [
                    self._restamp_ability(r, ability) if r.round == record.round else r
                    for r in self.state.rounds
                ]
            self._flush_pending_persist()
            return

        if record.phase == CampaignPhase.BENCH and record.event == "scored":
            score = BenchScore.model_validate(view_fields(record)["bench"])
            self.state.bench_score = score
            for reading in (score.origin, score.selected):
                if reading is not None:
                    self._place_bench(reading)
            self._flush_pending_persist()
            return

        if record.phase == CampaignPhase.BENCH and record.event == "graded":
            view = view_fields(record)
            if (raw := view["reading"]) is not None:
                reading = BenchReading.model_validate(raw)
                self._bench_labels[reading.round, reading.sp_hash] = view["label"]
                self._place_bench(reading)
            self._flush_pending_persist()
            return

        if record.phase == "round" and record.event == "display":
            payload = record.payload
            self.state.run_standing = RunStanding.model_validate(payload["run_standing"])
            # The headline scalars come off the PERSISTED lean form — the same numbers the full
            # result carries, so they fold whether or not a live producer is behind the record.
            self._update_current_acc(payload.get("round_result") or {})
            # The trajectory row needs the WHOLE `RoundResult`. It rides the in-memory-only field
            # for a live producer and comes off `rounds/round_NNNN.json` for a fold with none —
            # one document, two carriers, resolved HERE so no caller has to know which it got.
            round_result = record.live_round_result
            if round_result is None and record.round is not None:
                round_result = round_result_from_disk(self.cycle_dir, record.round)
            if round_result is not None:
                # Append round summary; re-firing the same round (a replay) replaces in place.
                origin_rows = (
                    [] if round_result.round == 0 else origin_rows_from_disk(self.cycle_dir)
                )
                prior = [r for r in self.state.rounds if r.round != round_result.round]
                self.state.best = _best_on_shared_cells([*prior, round_result])
                summary = build_round_summary(
                    round_result, origin_rows, best_so_far=self.state.best
                )
                self.state.rounds = sorted(
                    [*prior, self._with_bench(summary)], key=lambda r: r.round
                )
                self._flush_pending_persist()
            return

        # The view, never ``record.data``: the data bag is the phase builder's in-memory INPUT
        # (``exclude=True``, empty off disk) and the view is the persisted output built from it,
        # so every fact this fold needs is a declared field on the view or it does not survive.
        view = view_fields(record)
        event = PhaseEvent(phase=record.phase, event=record.event, round=record.round)
        self._apply_phase(event, view)
        # The buffer belongs to ONE round, so it clears when the round NUMBER moves, not at
        # `propose:enter` — `round:enter` advances the number first, which leaves the closed
        # round's candidates standing under the new number for the whole proposal.
        if self._buffer.round_num != self.state.round:
            self._buffer.reset(self.state.round)
        self._flush_pending_persist()

    def _handle_election(self, record: ElectionRecord) -> None:
        """The crown, from the record that IS the crown — never a measurement phase's view, which
        cannot reach round 0: the origin is ADOPTED rather than elected, runs no measure phase, and
        so would fold with ``is_selected`` false on the one arm it has. The election record fires
        for round 0 too, saying exactly that it adopted ``C0``.

        Everything the election stamps rides this record — the per-arm fit and the round's own
        readings — because the alternative carrier is ``rounds[]`` at the close, two LLM calls
        later. The round-level stamp is guarded on the live handle: a REPLAY carries none, and the
        round file it would fall back on does not exist yet at this offset."""
        self._buffer.mark_selected(record.selected_labels)
        self._buffer.stamp_fit(record.fit)
        if (rr := record.live_round_result) is not None:
            self._buffer.stamp_overlap(rr.overlap)
        self._flush_pending_persist()

    def _handle_candidate_minted(self, record: CandidateMintedRecord) -> None:
        """A candidate exists the moment its proposer mints it, a whole node before the
        measurement's ``candidate_started`` — seeding here makes the round's shape appear in
        ``dashboard.json`` as it is decided rather than after the generate call.

        The mint carries no candidate TOTAL — nothing knows it until generation returns — so the
        slot opens at the buffer's default and ``candidate_started`` stamps it later. Flushed
        rather than debounced, for the same reason a loop warning is: the operator is watching for
        exactly this."""
        if record.round != self._buffer.round_num:
            # The buffer belongs to ONE round and `_handle_phase` owns its reset, so a mint
            # arriving against another round is a real ordering fault — dropped, but never
            # silently: seeding it here would file this round's candidates under that one.
            logger.warning(
                "candidate_minted for round %d reached a buffer holding round %d — not seeded",
                record.round,
                self._buffer.round_num,
            )
            return
        self._buffer.slot(record.idx)["changes_description"] = record.changes_description
        self._flush_pending_persist()

    def _place_bench(self, reading: BenchReading) -> None:
        """A bench reading onto the round whose selection it graded and the candidate it read.
        Kept, because the origin's lands before round 0's summary does."""
        self._bench_readings[reading.round] = reading
        self.state.rounds = [self._with_bench(r) for r in self.state.rounds]

    def _with_bench(self, r: RoundSummary) -> RoundSummary:
        reading = self._bench_readings.get(r.round)
        if reading is None:
            return r
        label = self._bench_labels.get((reading.round, reading.sp_hash))
        return r.model_copy(
            update={
                "bench": reading,
                "candidates": [
                    c.model_copy(update={"bench": reading}) if c.label == label else c
                    for c in r.candidates
                ],
            }
        )

    @staticmethod
    def _restamp_ability(r: RoundSummary, ability: AbilityReading) -> RoundSummary:
        """The warm-ruler correction, applied to the round AND to round 0's candidate row.

        Round 0 holds no election fit of its own, so the round's θ IS its one candidate's; later
        rounds stamp per candidate at the election and are left alone."""
        if not r.stamps_theta:
            return r
        candidates = (
            [
                c.model_copy(update={"theta": ability.theta, "theta_se": ability.se})
                for c in r.candidates
            ]
            if r.round == 0
            else r.candidates
        )
        return r.model_copy(update={"ability": ability, "candidates": candidates})

    def _handle_snapshot(self, record: SnapshotRecord) -> None:
        ev = record.event
        payload = record.payload
        ci = int(record.candidate_idx or 0)
        ct = int(record.candidate_total or 0)
        qi = int(record.sample_idx or 0)
        qt = int(record.sample_total or 0)
        if ev == "sample_started":
            # The launch depth is this SAMPLE's, kept on its open-marker rather than on the state:
            # `sample_lookahead` is the depth in force and is read from the flag at `_persist`.
            self._open_samples[payload["sample_id"]] = (
                payload["query_preview"],
                ci,
                ct,
                payload["sample_lookahead"],
            )
            self._refresh_open_sample_markers()
            # The bench pass keeps its state through its cells, as no round's measurement runs it.
            if self.state.state is not DashboardState.BENCH:
                self._set_state(DashboardState.SCORING)
        elif ev == "sample_scored":
            result = payload["result"]
            # `is not None`, never `or`: sample_id 0 is falsy, and coercing it to a sentinel
            # leaves every candidate's first sample open, so it lands in the discard count.
            scored_sid = result.get("sample_id")
            if scored_sid is not None:
                self._open_samples.pop(int(scored_sid), None)
            self._absorb_sample_scored(result, last_in_candidate=(qi + 1 >= qt))
            # The scalars above are the RUN's and a slotless measurement is paid work like any
            # other; the buffer below is the ROUND's population, which it is not a member of.
            if ci != NO_ROUND_SLOT:
                self._buffer.append_sample(ci, ct, qi, qt, result)
            elif (bench_pass := self.state.bench_pass) is not None:
                self.state.bench_pass = bench_pass.model_copy(
                    update={
                        "scored": bench_pass.scored + 1,
                        "accuracy": (result.get("_running") or {}).get("accuracy"),
                    }
                )
        elif ev == "candidate_started":
            # Seed it empty so the lineage draws the round's path the instant a candidate is
            # known, rendering as a pending node until `sample_scored` fills it in.
            self._buffer.seed_candidate(
                ci,
                ct,
                payload["changes_description"],
                payload["pipeline_overlay"],
                payload["prompt_fields"],
                payload["resolved_pipeline_params"],
            )
        elif ev == "candidate_scored":
            scores = payload["scores"]
            # Still open at close ⇒ launched and never absorbed, but only a sample launched INTO
            # an armed window is the ARMING's cost: at depth 1 exactly one sample is in flight, so
            # one open here failed on its own and belongs to no control.
            if self._open_samples:
                self.state.sample_lookahead_discards += sum(
                    1 for *_, depth in self._open_samples.values() if depth > 1
                )
                self._open_samples.clear()
            self._update_current_acc(scores)
            self._buffer.set_candidate_scores(ci, ct, scores)
        elif ev == "sample_order_preview":
            self.state.declared_sample_order = payload["sample_order"]
        elif ev == "race_standing":
            self._buffer.record_race_standing(
                payload["member"], payload["current_id"], payload["n_samples"], payload["p_best"]
            )
        elif ev == "flight":
            self._flight = (payload["out"], payload["allowed"], payload["most"])
            self._affordable = payload["affordable"]
            self._cell_reserve_usd = payload["cell_usd"]
            waiting = payload["waiting"]
            self._waiting = None if waiting is None else (waiting["sample_id"], waiting["since"])
            held = payload["backpressure"]
            self._backpressure = None if held is None else BackpressureReading.model_validate(held)
        elif ev == "race_catch_up":
            self._append_catch_up(
                CatchUpLogEntry(
                    member=payload["member"],
                    round=record.round,
                    candidate_idx=ci,
                    candidate_total=ct,
                    sample_id=payload["sample_id"],
                    prior_ids=payload["prior_ids"],
                )
            )
        # One flush for EVERY branch, so none can forget: a branch that does leaves a finished
        # candidate's scores unwritten until the next event.
        self._schedule_persist()

    # -- Scalar mutations -----------------------------------------------------

    def _apply_phase(self, event: PhaseEvent, view: dict[str, Any]) -> None:
        s = self.state
        if event.round is not None:
            s.round = event.round
        # `activity` is what only an optimizer step's enter view declares.
        activity = view.get("activity") if event.event == "enter" else None
        if event.event == "enter" and s.state != DashboardState.STOPPED:
            mapped = _PHASE_TO_STATE.get(event.phase)
            if mapped is not None:
                self._set_state(mapped)
            elif activity is not None:
                self._set_state(DashboardState.OPTIMIZER_STEP, step=str(activity))
        if event.event == "enter" and (node := view.get("node")):
            if event.phase == CampaignPhase.PROPOSE:
                self._proposer_node = str(node)
            elif event.phase == CampaignPhase.MEASURE:
                self._measurement_node = str(node)
            elif activity is not None:
                self._step_node = str(node)

        if event.phase == CampaignPhase.INIT and view:
            # Stamped at ENTER, because origin scoring runs before the exit fires and the scoring
            # form needs the formula by then — and again at EXIT, because dials are locked into
            # a formula only once that origin is measured.
            formula = view.get("composite_fitness_formula")
            if formula is not None:
                s.composite_fitness_formula = formula
            short = view.get("composite_fitness_formula_short")
            if short is not None:
                self.short_formula_template = short
        elif event.phase == CampaignPhase.BENCH and event.event == "enter":
            self._bench_labels[view["round"], view["sp_hash"]] = view["label"]
            s.bench_pass = BenchPassProgress(
                subject=view["subject"],
                label=view["label"],
                sp_hash=view["sp_hash"],
                round=view["round"],
                rows=view["rows"],
                scored=0,
            )
        elif event.phase == CampaignPhase.BENCH and event.event == "exit":
            s.bench_pass = None
        elif event.phase == CampaignPhase.PROPOSE and event.event == "enter":
            # Rewind/fork-in-place clamp: drop rounds this run will overwrite. Sole clamp
            # writer; `round:display` is the sole growth site.
            s.rounds = [r for r in s.rounds if r.round < s.round]

    def _refresh_open_sample_markers(self) -> None:
        """Point the in-flight scalars at the OLDEST open sample. Derived, not assigned: under
        look-ahead ``sample_started`` for *n+1* precedes ``sample_scored`` for *n*."""
        s = self.state
        if not self._open_samples:
            s.current_query_payload = None
            s.current_sample_id = None
            s.open_sample_ids = []
            return
        sid, (query_text, ci, ct, _depth) = next(iter(self._open_samples.items()))
        s.current_query_payload = query_text
        s.current_sample_id = sid
        s.open_sample_ids = list(self._open_samples)
        s.candidate = "reference" if ci == NO_ROUND_SLOT else f"{candidate_label(s.round, ci)}/{ct}"

    def _absorb_sample_scored(self, result: dict[str, Any], *, last_in_candidate: bool) -> None:
        s = self.state
        pd = result.get("pipeline_data") or {}
        query_time = recorded_elapsed_s(cast("QueryMeasurement", result))
        is_cached = bool(result.get("cached", False))

        # First arm through the single owner of "this sample errored" — the measurement path sets
        # ``error`` and ``error_category`` together, so reading the human message was a second
        # spelling of the typed channel that every other error number here already reads.
        # The second arm is NOT redundant with it and stays: ``pipeline_data.error`` is the
        # BACKEND reporting a fault on a row PP classified clean, which no PP-side category covers.
        if is_error_result(result) or pd.get("error"):
            s.error_count += 1

        s.total_queries_scored += 1
        if not is_cached:
            s.total_backend_calls += 1
            # Counted where it was MEASURED: a replay re-reads the banked row, it degrades nothing.
            if is_degraded(result):
                s.degraded_count += 1

        # Not cleared outright: under look-ahead another sample is often still open when this
        # one lands, and blanking the panel would report "nothing in flight" mid-request.
        self._refresh_open_sample_markers()
        s.last_query_elapsed_s = None if query_time is None else round(query_time, 2)
        if s.state is not DashboardState.BENCH:
            self._set_state(
                DashboardState.BETWEEN_CANDIDATES
                if last_in_candidate
                else DashboardState.BETWEEN_SAMPLES
            )

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        """EVERY call lands in ``incurred``; only one that reached the wire lands in the bill. A
        cached call spent nothing, so billing it would halt a run over money it never cost.

        Banked TWICE from ONE price, the one the record carries — into the cycle's running total and
        into the round the call stamped itself with — so the two sides stay reconcilable."""
        self.state.spend.bank(record)
        # A call carrying no round ran before any round closed (init, the origin score); banking it
        # at 0 rather than dropping it is what keeps the two sides reconcilable.
        key = str(record.round if record.round is not None else 0)
        self.state.spend_by_round.setdefault(key, SpendRollup()).bank(record)
        self._schedule_persist()

    def spend_metered(self, meters: CeilingMeter) -> MeteredSpend:
        return MeteredSpend.of(self.state.spend, meters)

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        """Lights the node of the optimizer call in progress — the multi-minute blind spot during a
        reasoning-heavy optimizer call, where no other record fires. Reaches the browser as
        ``active_node``, which is why the persist still fires here."""
        self._in_flight = (record.call_id, record.node)
        self._schedule_persist()

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        """The sticky store backs ``current_round.nodes`` and survives round transitions —
        most-recent fire per phase-keyed slot."""
        self._sticky_llm_calls[record.node] = {
            **build_node_block(record),
            "round": self.state.round,
        }
        if self._in_flight is not None and record.call_id and self._in_flight[0] == record.call_id:
            self._in_flight = None
        self._schedule_persist()

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        """Heartbeat → re-persist, so ``wallclock_serialized_at`` stays fresh through an optimizer
        phase that fires no other record. No state mutation."""
        del record
        self._schedule_persist()

    def _handle_error(self, record: ErrorRecord) -> None:
        """Sole writer of ``dashboard.json::error``, fed by the ``ErrorRecord`` ``end_run_on``
        emits — so the webapp renders a crash without parsing ``index.json``."""
        self.state.error = DashboardError(
            kind=record.kind,
            message=record.message,
            stop_reason=record.stop_reason,
        )
        self._schedule_persist()

    def _handle_round_warning(self, record: RoundWarningRecord) -> None:
        """Sole writer of ``recent_loop_warnings``, flushed immediately: a zero-candidate round is a
        material fact the operator must see without waiting on the debounce."""
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

    def _update_current_acc(self, scores: dict[str, Any]) -> None:
        # An absent `accuracy` means the in-flight candidate has scored nothing yet. Defaulting
        # it to 0.0 drops the live headline mid-round and reads as a regression that never
        # happened, so hold the last measured value.
        acc = scores.get("accuracy")
        if acc is not None:
            self.state.current_acc = round(float(acc), 4)

    # -- Round-state mutations (snapshot-record fan-out) ----------------------
    # Per-candidate / per-sample / P(best) writes live on the ``RoundBuffer``;
    # ``_append_catch_up`` stays here because it writes scalar state instead.

    def _append_catch_up(self, entry: CatchUpLogEntry) -> None:
        """Absence of an entry for a sample means every prior was already cached for it. Capped at
        256 — per-sample events accumulate as samples × priors × candidates."""
        self.state.catch_up_log = [*self.state.catch_up_log, entry][-256:]

    # -- Internal --------------------------------------------------------------

    def _active_node(self) -> str | None:
        """Which node is working. The in-flight LLM call names its node and wins; otherwise the
        state does, which is what keeps a node lit across the gap between two calls in one phase.
        A new state is a type error here rather than a silent "nothing running"."""
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
            # The held-out pass is the bench's own walk: no node of the optimizer's graph runs it.
            case DashboardState.BENCH | DashboardState.STOPPED:
                return None
            case _:
                assert_never(state)

    def _current_round_nodes(self) -> dict[str, dict[str, Any]]:
        """THIS round's optimizer calls. ``_sticky_llm_calls`` is most-recent-fire-per-slot and
        survives round transitions, so it is filtered by each block's own ``round``: presence in
        this map is the client's whole definition of "this node has fired"."""
        return {
            node: block
            for node, block in self._sticky_llm_calls.items()
            if block.get("round") == self.state.round
        }

    def _persist(self) -> None:
        """Compose, then swap atomically via ``write_json`` — polling readers never see a torn
        payload. A fold with no ``state_path`` composes on demand instead (``compose``), so
        nothing here fires per record for a replay that will be read once.

        A refused swap is reported, never raised: the ledger is the truth here, so the cost is one
        stale poll, while raising costs the caller whatever it was measuring."""
        if self.state_path is None:
            return
        started = time.perf_counter()
        self.compose()
        self._stamp_armed_controls()
        try:
            write_json(self.state_path, self.state.model_dump(), default=str)
            self._persist_cost_s = time.perf_counter() - started
        except OSError as exc:
            # Logged, not emitted: a round warning appends to the ledger and this view subscribes
            # to that append, so reporting the failure re-enters the path that failed.
            logger.warning(
                "dashboard write refused — %s: %s · %s is stale until the next write lands "
                "(the run is unaffected; the ledger holds every fact this file shows)",
                exc.__class__.__name__,
                exc,
                self.state_path,
            )

    def _stamp_armed_controls(self) -> None:
        """What the operator has ARMED right now, onto the file being written — never ``compose``'s:
        a fold of a past moment must not carry the present's controls. No SERVED read depends on it."""
        s = self.state
        # `_arm_spend_book` prefers `run_limits.json` over the admitted cap, so the stamped
        # value alone would quote a ceiling nothing enforces.
        if s.run_limits is not None:
            armed = armed_run_limits(self.cycle_dir)
            if armed:
                s.run_limits = s.run_limits.model_copy(update=armed)
        # CLAMPED: an unclamped 8 beside `max_cells_in_flight: 2` writes a depth nothing is
        # running. Both readings end at `effective_lookahead` — the one "depth in force".
        s.sample_lookahead = effective_lookahead(
            read_sample_lookahead(self.cycle_dir), s.max_cells_in_flight
        )
        s.sample_lookahead_auto = sample_lookahead_auto(self.cycle_dir)

    def compose(self) -> LiveDashboardState:
        """Settle the served shape onto ``state`` and return it — everything a reader needs that is
        derived from the fold alone. Idempotent; a replay calls it once, after its last record."""
        s = self.state
        # Stamp the moment this write is OF, before composing anything from it. The write is
        # debounced, so it lands after later records have already arrived — `at_offset` names
        # the fold, never the file's mtime, and a reader joins the event stream from here.
        s.at_offset = self.at_offset
        s.current_round = CurrentRound(
            # `state.round`, never the candidate buffer's, which only advances at
            # `propose:enter` — one behind from `round:enter` onward, so every reader
            # comparing the two concludes the round has already closed.
            round=s.round,
            active_node=self._active_node(),
            measurement_node=self._measurement_node,
            candidates=build_candidate_rows(self._buffer, self.short_formula_template),
            nodes=self._current_round_nodes(),
            racing=build_racing_block(self._buffer),
            overlap=self._buffer.overlap,
        )
        # Nothing scoring: the most the NEXT round could hold — every candidate's cells and one
        # catch-up per cell — so a press can be sized before the round it will apply to begins.
        if any(self._flight):
            s.in_flight, s.lookahead_allowed, s.lookahead_most = self._flight
        else:
            s.in_flight = s.lookahead_allowed = 0
            s.lookahead_most = (
                None if s.arms_per_round is None else (s.arms_per_round + 1) * s.sp_budget_round
            )
        # Kept whatever the phase state: between rounds these still say what the ceiling would
        # afford the next one, which is when a press is sized.
        s.lookahead_affordable = self._affordable
        s.cell_reserve_usd = self._cell_reserve_usd
        # Named off the launch that opened the cell, the one record that says whose it is.
        opened = self._open_samples.get(self._waiting[0]) if self._waiting else None
        if self._waiting is None or opened is None:
            s.waiting_on, s.waiting_since = None, None
        else:
            ci = opened[1]
            owner = "reference" if ci == NO_ROUND_SLOT else candidate_label(s.round, ci)
            s.waiting_on = f"{owner} · sample {self._waiting[0]}"
            s.waiting_since = self._waiting[1]
        s.backpressure = self._backpressure
        s.bench_lift_per_incurred_usd = (
            None if s.bench_score is None else s.bench_score.lift_per_usd(s.spend)
        )
        s.wallclock_serialized_at = utcnow_iso()
        # The typed model IS the on-disk shape, and `extra="forbid"` rejects an undeclared
        # attribute at the mutation site — so a field can neither silently vanish nor appear
        # undeclared.
        return s


def _best_on_shared_cells(rounds: Iterable[RoundSummary | RoundResult]) -> float | None:
    """``state.best`` for *rounds* — the rate the cycle index elects on."""
    best = best_round_on_shared_cells(
        [SharedCellPoint.of(r.round, r.accuracy, r.overlap) for r in rounds]
    )
    return None if best is None else best[0]


def resolve_resume_state(
    seed: Cut,
    active_cycle_dir: Path,
    resumed_from_round: int | None,
) -> LiveDashboardState:
    """The state a resuming or forking run carries forward, FOLDED from the seed cycle's ledger.

    It read the prior ``dashboard.json`` instead, which made a projection an input to itself: a
    file torn, stale or written by an earlier build seeded the run that was meant to recover it,
    and the trajectory it carried was whatever had last been flushed rather than what the records
    say. The ledger is the truth and the file is its cache, so this asks the ledger.

    **The one place the trajectory is CUT**: rounds at or past ``resumed_from_round`` drop and
    ``best`` is RE-DERIVED, because a carried rolling max keeps a peak the rewind just discarded."""
    prior = fold_at(seed, wiring=None)
    surviving = [
        r for r in prior.rounds if resumed_from_round is None or r.round < resumed_from_round
    ]
    best = _best_on_shared_cells(surviving)
    rounds_dir = CycleLayout(active_cycle_dir).rounds
    on_disk = max(
        (n for p in rounds_dir.glob(ROUND_GLOB) if (n := round_number(p)) is not None),
        default=0,
    )
    return prior.model_copy(
        update={"rounds": surviving, "round": max(prior.round, on_disk), "best": best}
    )


def fold_at(cut: Cut, *, wiring: Mapping[str, Any] | None) -> LiveDashboardState:
    """The cycle's dashboard state as of *cut* — the SAME fold the runner drives, off disk. *wiring*
    (``WIRING_FIELDS``) rides no record, so it is an INPUT taken BEFORE the fold composes."""
    view = LiveDashboardProjection(
        cut.cycle,
        state_path=None,
        hop=cut.hop,
        session_id="",
        arms_per_round=0,
        sp_budget_round=0,
        display_metric="accuracy",
    )
    for name, value in (wiring or {}).items():
        setattr(view.state, name, value)
    limit = None if cut.offset is None else cut.offset + 1
    for offset, record in open_with_history(cut.cycle).iter(limit):
        view.on_record(record, offset)
    return view.compose()


__all__ = ["LiveDashboardProjection", "fold_at", "resolve_resume_state"]
