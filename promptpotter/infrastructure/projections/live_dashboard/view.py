from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from promptpotter.domain.cycle_paths import CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.dashboard_rows import RoundSummary
from promptpotter.domain.phases import CampaignPhase, DashboardState, PhaseEvent, RunPhase
from promptpotter.domain.results import (
    HeadlineMetric,
    best_round_on_shared_cells,
    candidate_label,
    overlap_row,
)
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
    weighted_sum_weights,
)
from promptpotter.infrastructure.ledger import open_with_history
from promptpotter.infrastructure.projections.audit_trail import (
    audit_rounds_dir,
    build_node_block,
    read_most_recent_round_nodes,
)
from promptpotter.infrastructure.projections.base import DerivedView
from promptpotter.infrastructure.projections.live_dashboard.render import (
    build_candidate_rows,
    build_l1_score_block,
    build_pobb_block,
)
from promptpotter.infrastructure.projections.live_dashboard.round_buffer import RoundBuffer
from promptpotter.infrastructure.projections.live_dashboard.round_summary import (
    build_round_summary,
    origin_rows_from_disk,
    round_result_from_disk,
)
from promptpotter.infrastructure.projections.live_dashboard.state import (
    BackendWarning,
    BackfillLogEntry,
    CurrentRound,
    DashboardError,
    InFlightCall,
    LiveDashboardState,
    LoopWarning,
    RunLimits,
)
from promptpotter.infrastructure.projections.live_state import (
    LiveStateCore,
    apply_p_best_update,
    apply_phase,
    roll_p_best_at_round_complete,
)
from promptpotter.infrastructure.runtime_flags import read_armed_cells, read_spend_caps
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import (
    ROUND_GLOB,
    CycleLayout,
    cycle_dir_for,
    round_number,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import has_pipeline_warnings, is_error_result
from promptpotter.shared.pricing import compute_usd

if TYPE_CHECKING:
    from promptpotter.connectors.protocol import ConcurrencyArming, MeasuredUnit
    from promptpotter.infrastructure.projections.audit_trail import AuditTrailView

logger = logging.getLogger(__name__)


# Coalesces the per-sample / per-token / per-LLM-call hot paths onto one disk write. Phase
# boundaries still flush immediately, so `dashboard.json` is current at every transition.
_DASHBOARD_DEBOUNCE_S = 0.25


# L1_SCORE absent: driven by sample_started / sample_scored.
# The VALUES are the sole declaration of the `dashboard.json::state` vocabulary — keep them
# typed, or the webapp mirrors bare strings against no source. The key stays `str` because
# `PhaseEvent.phase` is wider than `CampaignPhase`.
_PHASE_TO_STATE: dict[str, DashboardState] = {
    CampaignPhase.INIT: DashboardState.INIT,
    CampaignPhase.ORIGIN: DashboardState.ORIGIN,
    CampaignPhase.L1_GENERATE: DashboardState.L1_GENERATE,
    CampaignPhase.REFINE_STRATEGY: DashboardState.L2_REFINING,
    CampaignPhase.MODIFY_PLAN: DashboardState.L3_REPLANNING,
    CampaignPhase.ESCALATION: DashboardState.ESCALATION,
}


# Which optimizer node each activity state is the work OF — the served `active_node`. TOTAL
# over `DashboardState`, asserted below: an omitted member reads as "nothing running" rather
# than failing, so a new state must name its node here instead of silently meaning idle.
_STATE_TO_NODE: dict[DashboardState, str | None] = {
    DashboardState.INIT: "checkin",
    DashboardState.ORIGIN: "checkin",
    DashboardState.SCORING: "l1_score",
    DashboardState.BETWEEN_SAMPLES: "l1_score",
    DashboardState.BETWEEN_CANDIDATES: "l1_score",
    DashboardState.L1_GENERATE: "l1_generate",
    DashboardState.L2_REFINING: "l2_context",
    DashboardState.L3_REPLANNING: "l3_plan",
    # The post-round escalation gate is the critique's own work.
    DashboardState.ESCALATION: "l1_critique",
    # The one honest `None`: a stopped cycle is running nothing.
    DashboardState.STOPPED: None,
}

if set(_STATE_TO_NODE) != set(DashboardState):
    raise RuntimeError(
        "_STATE_TO_NODE must cover every DashboardState — a missing member reads as "
        f"'nothing running': {set(DashboardState) - set(_STATE_TO_NODE)}"
    )


def _ability_delta(rounds: list[RoundSummary]) -> float | None:
    """Latest round carrying an ability, over the origin — never the max (winner's curse in logit
    space). Skipping only unfit rounds is ``parent_level_trajectory``'s own carry-forward rule."""
    origin = next((r.ability for r in rounds if r.round == 0 and r.ability is not None), None)
    if origin is None:
        return None
    latest = next(
        (
            r.ability
            for r in sorted(rounds, key=lambda r: r.round, reverse=True)
            if r.ability is not None
        ),
        None,
    )
    if latest is None or not latest.comparable_to(origin):
        return None
    return round(latest.theta - origin.theta, 4)


class LiveDashboardView(DerivedView):
    """Per-cycle dashboard writer; not an optimizer checkpoint."""

    def __init__(
        self,
        cycle_dir: CycleDir,
        *,
        state_path: Path | None,
        hop: CycleHop,
        session_id: str,
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
        headline_metric: HeadlineMetric,
        langfuse_trace_url: str | None = None,
        resume_from: LiveDashboardState | None = None,
        recorder: AuditTrailView | None = None,
        initial_llm_nodes: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        cycle_path = Path(cycle_dir)
        self.cycle_dir = cycle_path
        # Where this fold MATERIALIZES itself, or ``None`` for one that answers a question
        # instead — a replay serving a past moment must not overwrite the head's file with it.
        # Spelled by the caller rather than derived from `cycle_dir`, because both modes read
        # that same directory and only the write target tells them apart.
        self.state_path = state_path
        self._recorder = recorder
        self.patience_max = l1_patience
        # The schema IS the on-disk shape (`_persist` dumps this instance), so it also owns
        # which fields a resume inherits and which this process stamps fresh.
        self.state = LiveDashboardState.for_run(
            resume_from,
            hop=hop,
            session_id=session_id,
            l1_patience=l1_patience,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
            langfuse_trace_url=langfuse_trace_url,
            headline_metric=headline_metric,
        )
        self.short_formula_template: str | None = None
        self._buffer = RoundBuffer()
        # Launched, not yet absorbed, in launch order: HEAD drives the in-flight markers, and
        # whatever remains at candidate close was discarded.
        # sample_id -> (query_text, sample_idx, sample_total, candidate_idx, cand_total, depth)
        # The depth it LAUNCHED at is what separates look-ahead cost from a plain failure.
        self._open_samples: dict[int, tuple[str, int, int, int, int, int]] = {}
        # Sticky LLM-call mirror for ``current_round.nodes`` — owned here, not on the
        # audit-trail, which records the same event independently into its round flush.
        self._sticky_llm_calls: dict[str, dict[str, Any]] = dict(initial_llm_nodes or {})
        self._core = LiveStateCore(
            round_num=self.state.round,
            # Origin anchor seeds from round 0 if it's already on disk (resume);
            # otherwise apply_phase sets it at INIT:exit. Origin is just round 0.
            origin_acc=next((r.accuracy for r in self.state.rounds if r.round == 0), 0.0),
            # The core's own running MAX, seeded from the resumed high-water. 0.0 is the
            # identity element of that max, not a reported number — `s.best` stays `None`
            # until a round settles, and only it reaches the browser.
            best_acc=self.state.best if self.state.best is not None else 0.0,
        )
        # RLock so the boundary-flush path can be called from inside a handler already holding
        # it via `on_record`; the Timer thread takes the same lock and cannot race a mutation.
        self._persist_lock: threading.RLock = threading.RLock()
        self._persist_timer: threading.Timer | None = None
        self._persist_dirty: bool = False

        self._persist()

    @classmethod
    def for_session(
        cls,
        hop: CycleHop,
        *,
        tenant_root: str,
        session_id: str,
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
        headline_metric: HeadlineMetric,
        langfuse_trace_url: str | None = None,
        resumed_from_round: int | None = None,
        recorder: AuditTrailView | None = None,
        seed_from_cycle_id: str | None = None,
        max_cells_in_flight: int | None = None,
        concurrency_arming: ConcurrencyArming | None = None,
        measured_unit: MeasuredUnit | None = None,
    ) -> LiveDashboardView | None:
        """``seed_from_cycle_id`` names the cycle to read the prior dashboard from — a fork inherits
        the parent's trajectory up to the cut while counting its own copied round files."""
        if not (tenant_root and session_id and hop.campaign_id and hop.cycle_id):
            return None

        root = WorkspaceDir(Path(tenant_root))
        cycle_dir = CycleDir(cycle_dir_for(root, hop))
        seed_hop = (
            CycleHop(campaign_id=hop.campaign_id, cycle_id=seed_from_cycle_id)
            if seed_from_cycle_id
            else hop
        )
        resume_from = resolve_resume_state(
            cycle_dir_for(root, seed_hop),
            seed_hop,
            Path(cycle_dir),
            resumed_from_round,
        )
        # Surfaces prior rounds' L1/L2/L3 outputs before the first new call lands.
        initial_llm_nodes = read_most_recent_round_nodes(audit_rounds_dir(Path(cycle_dir)))

        view = cls(
            cycle_dir,
            state_path=CycleLayout(Path(cycle_dir)).dashboard,
            hop=hop,
            session_id=session_id,
            l1_patience=l1_patience,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
            headline_metric=headline_metric,
            langfuse_trace_url=langfuse_trace_url,
            resume_from=resume_from,
            recorder=recorder,
            initial_llm_nodes=initial_llm_nodes,
        )
        # Stamped at WIRING, not on a phase event: origin scoring runs before INIT fires, and the
        # browser reads the `1` default as "this backend holds one sample" and disables the
        # control. After `resume_from` — the live connector outranks a pair an older build wrote.
        if max_cells_in_flight is not None:
            view.state.max_cells_in_flight = max_cells_in_flight
        if concurrency_arming is not None:
            view.state.concurrency_arming = concurrency_arming
        if measured_unit is not None:
            view.state.measured_unit = measured_unit
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
        interrupted: bool,
    ) -> None:
        """Stamps a cycle whose run stopped BEFORE the projection bound, so the tree shows what
        happened. Pair with ``mark_finished`` only on a crash — a ``finished_at`` unresumes a pause."""
        cycle_path = Path(cycle_dir)
        state = resolve_resume_state(cycle_path, hop, cycle_path, None)
        if session_id:
            state.session_id = session_id
        if interrupted:
            state.declared_phase = RunPhase.PAUSED
        else:
            state.error = DashboardError(
                kind="launch_failed",
                message=str(exc) or type(exc).__name__,
                stop_reason="launch_aborted",
            )
            state.stop_reason = f"{type(exc).__name__}: {exc}"
            state.declared_phase = RunPhase.TERMINAL
            state.state = DashboardState.STOPPED
        state.state_since = utcnow_iso()
        write_json(CycleLayout(cycle_path).dashboard, state.model_dump(), default=str)

    # -- State transitions ----------------------------------------------------

    def _set_state(self, name: DashboardState) -> None:
        self.state.state = name
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
        timer = threading.Timer(_DASHBOARD_DEBOUNCE_S, self._fire_debounced_persist)
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
        if record.phase == "control":
            # The SOLE writer of `declared_phase` — and nothing here writes `run_phase`, which is
            # wire-only and derived at the read. Flushing bumps the mtime, so the 304-cached route
            # re-derives immediately. TERMINAL arrives here like every other phase since the stop
            # became a record; it used to be pushed in by `mark_stopped`, so a fold of the ledger
            # reported a stopped cycle as still running.
            event_phase = RunPhase(record.event)
            if self.state.declared_phase == event_phase:
                return
            self.state.declared_phase = event_phase
            if event_phase == RunPhase.TERMINAL:
                self.state.stop_reason = str(record.payload.get("stop_reason") or "")
                self._set_state(DashboardState.STOPPED)
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
            # Candidate ids are ROUND-SCOPED, so a P(best) map carried into the next round would
            # rank a candidate against a stranger.
            roll_p_best_at_round_complete(self._core)
            raw = record.payload.get("ability")
            if isinstance(raw, dict):
                ability = AbilityReading.model_validate(raw)
                self.state.rounds = [
                    self._restamp_ability(r, ability) if r.round == record.round else r
                    for r in self.state.rounds
                ]
                self.state.ability_delta = _ability_delta(self.state.rounds)
            self._flush_pending_persist()
            return

        if record.phase == "round" and record.event == "display":
            payload = record.payload
            l1_stall = int(payload.get("l1_stall_count") or 0)
            hearts_raw = payload.get("hearts")
            hearts = None if hearts_raw is None else int(hearts_raw)
            # The headline scalars come off the PERSISTED lean form — the same numbers the full
            # result carries, so they fold whether or not a live producer is behind the record.
            accuracy = (payload.get("round_result") or {}).get("accuracy")
            if accuracy is not None:
                self._absorb_round_complete(float(accuracy), l1_stall, hearts)
            # The trajectory row needs the WHOLE `RoundResult`. It rides the in-memory-only field
            # for a live producer and comes off `rounds/round_NNNN.json` for a fold with none —
            # one document, two carriers, resolved HERE so no caller has to know which it got.
            round_result = record.live_round_result
            if round_result is None and record.round is not None:
                round_result = round_result_from_disk(self.cycle_dir, record.round)
            if round_result is not None:
                if self._recorder is not None:
                    self._recorder.set_l1_score(self._l1_score_block())
                # Append round summary; re-firing the same round (replay / sweep) replaces in place.
                origin_rows = (
                    [] if round_result.round == 0 else origin_rows_from_disk(self.cycle_dir)
                )
                summary = build_round_summary(round_result, origin_rows)
                rounds_list = [r for r in self.state.rounds if r.round != round_result.round]
                rounds_list.append(summary)
                rounds_list.sort(key=lambda r: r.round)
                self.state.rounds = rounds_list
                # AFTER the append, so round 0's summary is present when it computes its first
                # value. Both ends come off the subset-invariant `cumulative_theta`, so the
                # difference is a real lift rather than the luckiest draw minus the fullest one.
                self.state.ability_delta = _ability_delta(self.state.rounds)
                self._flush_pending_persist()
            return

        # The view, never ``record.data``: the data bag is the phase builder's in-memory INPUT
        # (``exclude=True``, empty off disk) and the view is the persisted output built from it,
        # so every fact this fold needs is a declared field on the view or it does not survive.
        view = view_fields(record)
        event = PhaseEvent(phase=record.phase, event=record.event, round=record.round)
        self._apply_phase(event, view)
        # The buffer belongs to ONE round, so it clears when the round NUMBER moves, not at
        # `L1_GENERATE:enter` — `round:enter` advances the number first, which leaves the closed
        # round's candidates standing under the new number for the whole generate call.
        if self._buffer.round_num != self.state.round:
            self._buffer.reset(self.state.round)
        self._flush_pending_persist()

    def _handle_election(self, record: ElectionRecord) -> None:
        """The crown, from the record that IS the crown. It was read off ``L1_SCORE:exit``'s view
        instead, which cannot reach round 0 — the origin is ADOPTED rather than elected, runs no
        ``l1_score`` phase, and so folded with ``is_winner`` false on the one arm it has. The
        election record fires for round 0 too, saying exactly that it adopted ``C0``.

        Two channels for one fact, and this is the one with its own record. ``DerivedView`` had no
        branch for it at all, so the crown reached no fold.

        Everything the election stamps rides this record — the per-arm fit and the round's own
        readings — because the alternative carrier is ``rounds[]`` at the close, two LLM calls
        later. The round-level stamp is guarded on the live handle: a REPLAY carries none, and the
        round file it would fall back on does not exist yet at this offset."""
        self._buffer.mark_winner(record.winner_label)
        self._buffer.stamp_fit(record.fit)
        if (rr := record.live_round_result) is not None:
            self._buffer.stamp_overlap(rr.overlap)
        self._flush_pending_persist()

    def _handle_candidate_minted(self, record: CandidateMintedRecord) -> None:
        """A candidate exists the moment ``l1_generate`` mints it — and this fold learned of one
        only at ``candidate_started``, a snapshot ``l1_score`` fires one whole node later.

        So ``dashboard.json`` — the surface every reader polls — showed an empty round for the
        length of the generate call, while the ledger had recorded each candidate as it landed and
        the ray was already serving them. The record had no arm anywhere: ``_handle_candidate_minted``
        was the base no-op in EVERY projection, which is a writer with no reader rather than a
        state. Seeding here is what makes the round's shape appear as it is decided.

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

    @staticmethod
    def _restamp_ability(r: RoundSummary, ability: AbilityReading) -> RoundSummary:
        """The warm-ruler correction, applied to the round AND to round 0's candidate row.

        Round 0 holds no election fit of its own, so the round's θ IS its one candidate's; later
        rounds stamp per candidate at ``l1_score`` and are left alone. Only the frontier was
        patched here before, so ``rounds[0].candidates[0].theta`` stayed null forever while the
        round file carried the warm value."""
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
            sid = payload.get("sample_id")
            self.state.sample_lookahead = int(payload.get("sample_lookahead") or 1)
            if sid is not None:
                self._open_samples[int(sid)] = (
                    str(payload.get("query_preview") or ""),
                    qi,
                    qt,
                    ci,
                    ct,
                    self.state.sample_lookahead,
                )
            self._refresh_open_sample_markers()
            self._set_state(DashboardState.SCORING)
        elif ev == "sample_scored":
            result = payload.get("result") or {}
            # `is not None`, never `or`: sample_id 0 is falsy, and coercing it to a sentinel
            # leaves every candidate's first sample open, so it lands in the discard count.
            scored_sid = result.get("sample_id")
            if scored_sid is not None:
                self._open_samples.pop(int(scored_sid), None)
            self._absorb_sample_scored(result, last_in_candidate=(qi + 1 >= qt))
            self._buffer.append_sample(ci, ct, qi, qt, result)
        elif ev == "candidate_started":
            # Seed it empty so the lineage draws the round's path the instant a candidate is
            # known, rendering as a pending node until `sample_scored` fills it in.
            self._buffer.seed_candidate(
                ci,
                ct,
                payload.get("changes_description") or "",
                payload.get("pp_override"),
                payload.get("prompt_fields"),
                payload.get("resolved_pipeline_params"),
            )
        elif ev == "candidate_scored":
            scores = payload.get("scores") or {}
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
            order = payload.get("sample_order")
            if isinstance(order, list):
                self.state.declared_sample_order = [int(sid) for sid in order]
        elif ev == "p_best_update":
            current_id = payload.get("current_id") or ""
            n_samples = int(payload.get("n_samples") or 0)
            p_best = float(payload.get("p_best") or 0.0)
            self._buffer.update_p_best(ci, ct, current_id, n_samples, p_best)
            # Mirrored into the shared core so LiveDisplay sees the same round-wide state.
            apply_p_best_update(self._core, current_id, n_samples, p_best)
        elif ev == "pobb_backfill":
            self._append_backfill(
                int(record.round or 0),
                ci,
                ct,
                int(payload.get("sample_id") or 0),
                [str(p) for p in (payload.get("prior_ids") or [])],
            )
        # One flush for EVERY branch, so none can forget: a branch that does leaves a finished
        # candidate's scores unwritten until the next event.
        self._schedule_persist()

    # -- Scalar mutations -----------------------------------------------------

    def _apply_phase(self, event: PhaseEvent, view: dict[str, Any]) -> None:
        s = self.state
        if event.round is not None:
            s.round = event.round
        # L1_SCORE has no _PHASE_TO_STATE entry — sample_started/scored own its transitions.
        if event.event == "enter" and s.state != DashboardState.STOPPED:
            mapped = _PHASE_TO_STATE.get(event.phase)
            if mapped is not None:
                self._set_state(mapped)

        if event.phase == CampaignPhase.INIT and event.event == "enter" and view:
            # Everything INIT declares is stamped at ENTER, because origin scoring runs before
            # the exit fires: the mask editor needs the formula by then and the run strip its ceilings.
            formula = view.get("composite_fitness_formula")
            if formula is not None:
                s.composite_fitness_formula = formula
                # Derived here rather than carried beside the string through every view that
                # declares one: it is a pure function of the formula, so a second channel for it
                # is a second thing that can be stale.
                s.composite_fitness_weights = weighted_sum_weights(formula)
            short = view.get("composite_fitness_formula_short")
            if short is not None:
                self.short_formula_template = short
            # Re-seats `patience_max`, so the "N/M" reading survives a fold that never got the
            # constructor's copy — a replay off disk has only the ledger. The ceilings themselves
            # are WIRING (`run_limits`): they were declared at launch, and this record arrives
            # after the whole origin has already scored.
            self.patience_max = int(view.get("patience") or 0)
        elif event.phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            s.degraded_count = 0
            # Rewind/fork-in-place clamp: drop rounds this run will overwrite. Sole clamp
            # writer; `round:display` is the sole growth site.
            s.rounds = [r for r in s.rounds if r.round < s.round]

        # Mirrored for LiveDisplay parity. The core's ``best_acc`` is a hard-first SUBSET score
        # and must NOT feed ``s.best``, whose sole writer is ``_absorb_round_complete``.
        apply_phase(self._core, event, view)

    def _update_sample_markers(self, ci: int, ct: int, qi: int, qt: int) -> None:
        s = self.state
        s.candidate = f"{candidate_label(s.round, ci)}/{ct}"
        s.query = f"{qi + 1}/{qt}"

    def _refresh_open_sample_markers(self) -> None:
        """Point the in-flight scalars at the OLDEST open sample. Derived, not assigned: under
        look-ahead ``sample_started`` for *n+1* precedes ``sample_scored`` for *n*."""
        s = self.state
        if not self._open_samples:
            s.current_query_payload = None
            s.current_sample_id = None
            s.open_sample_ids = []
            return
        sid, (query_text, qi, qt, ci, ct, _depth) = next(iter(self._open_samples.items()))
        s.current_query_payload = query_text
        s.current_sample_id = sid
        s.open_sample_ids = list(self._open_samples)
        self._update_sample_markers(ci, ct, qi, qt)

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
        if has_pipeline_warnings(result):
            s.degraded_count += 1

        s.total_queries_scored += 1
        if not is_cached:
            s.total_backend_calls += 1

        # Not cleared outright: under look-ahead another sample is often still open when this
        # one lands, and blanking the panel would report "nothing in flight" mid-request.
        self._refresh_open_sample_markers()
        s.last_query_elapsed_s = None if query_time is None else round(query_time, 2)
        self._set_state(
            DashboardState.BETWEEN_CANDIDATES
            if last_in_candidate
            else DashboardState.BETWEEN_SAMPLES
        )

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        """EVERY call lands in ``incurred``; only one that reached the wire lands in the bill. A
        cached call spent nothing, so billing it would halt a run over money it never cost."""
        spend = self.state.spend
        bucket = spend.loop if record.kind == "optimizer" else spend.backend
        in_tok = int(record.input_tokens)
        out_tok = int(record.output_tokens)
        if record.model and not bucket.model:
            bucket.model = record.model
        cache_read = int(record.cache_read_tokens)
        cache_write = int(record.cache_write_tokens)
        usd = compute_usd(
            record.model,
            in_tok,
            out_tok,
            override_usd=record.cost_usd,
            provider=record.provider,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

        if usd is not None:
            bucket.incurred_usd = round(bucket.incurred_usd + usd, 6)
        elif in_tok or out_tok:
            bucket.incurred_unpriced_tokens += in_tok + out_tok

        if not record.cached:
            bucket.input_tokens += in_tok
            bucket.output_tokens += out_tok
            bucket.reasoning_tokens += int(record.reasoning_tokens)
            # Only the billed side: a reuse-cache hit reached no provider, so counting its
            # replayed cache tokens would report a prefix holding on calls never made.
            bucket.cache_read_tokens += cache_read
            bucket.cache_write_tokens += cache_write
            if usd is not None:
                bucket.used_usd = round(bucket.used_usd + usd, 6)
                bucket.rate_known = True
            elif in_tok or out_tok:
                # Billed but with no resolvable cost, so the USD cap cannot see this spend.
                # Tracked so the dashboard flags the cap as inactive.
                bucket.unpriced_tokens += in_tok + out_tok

        spend.total_used_usd = round(spend.backend.used_usd + spend.loop.used_usd, 6)
        spend.total_incurred_usd = round(spend.backend.incurred_usd + spend.loop.incurred_usd, 6)
        spend.total_tokens_used = sum(
            b.input_tokens + b.output_tokens for b in (spend.backend, spend.loop)
        )
        spend.unpriced_tokens = spend.backend.unpriced_tokens + spend.loop.unpriced_tokens
        self._schedule_persist()

    @property
    def spend_total_used_usd(self) -> float:
        return self.state.spend.total_used_usd

    @property
    def spend_total_tokens(self) -> int:
        return self.state.spend.total_tokens_used

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        """Publishes the in-flight optimizer call — the multi-minute blind spot during a
        reasoning-heavy critique, where no other record fires."""
        self.state.in_flight = InFlightCall(
            call_id=record.call_id,
            node=record.node,
            model=record.model,
            round=record.round,
            candidate_idx=record.candidate_idx,
            started_at_ms=record.started_at_ms,
        )
        self._schedule_persist()

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        """The sticky store backs ``current_round.nodes`` and survives round transitions —
        most-recent fire per phase-keyed slot."""
        self._sticky_llm_calls[record.node] = {
            **build_node_block(record),
            "round": self.state.round,
        }
        in_flight = self.state.in_flight
        if in_flight is not None and record.call_id and in_flight.call_id == record.call_id:
            self.state.in_flight = None
        self._schedule_persist()

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        """Heartbeat → re-persist, so ``wallclock_serialized_at`` stays fresh through an optimizer
        phase that fires no other record. No state mutation."""
        del record
        self._schedule_persist()

    def _handle_error(self, record: ErrorRecord) -> None:
        """Sole writer of ``dashboard.json::error``, fed by the ``ErrorRecord`` the runner's three
        ``except`` sites emit — so the webapp renders a crash without parsing ``index.json``."""
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

    def _absorb_round_complete(
        self, round_accuracy: float, l1_stall_count: int, hearts: int | None = None
    ) -> None:
        """Never settle to ``cumulative_accuracy`` — nothing rescores it, so that pooled series can
        exceed everything the cycle measured. The round's own ``total`` is the answer beside it."""
        s = self.state
        acc = round(round_accuracy, 4)
        s.current_acc = acc
        if s.best is None or acc > s.best:
            s.best = acc
        s.patience = f"{l1_stall_count}/{self.patience_max}"
        s.hearts = hearts

    # -- Round-state mutations (snapshot-record fan-out) ----------------------
    # Per-candidate / per-sample / P(best) writes live on the ``RoundBuffer``;
    # ``_append_backfill`` stays here because it writes scalar state instead.

    def _append_backfill(
        self,
        round_num: int,
        idx: int,
        total: int,
        sample_id: int,
        prior_ids: list[str],
    ) -> None:
        """Absence of an entry for a sample means every prior was already cached for it. Capped at
        256 — per-sample events accumulate as samples × priors × candidates."""
        log = list(self.state.backfill_log)
        log.append(
            BackfillLogEntry(
                round=int(round_num),
                candidate_idx=int(idx),
                candidate_total=int(total),
                sample_id=int(sample_id),
                prior_ids=list(prior_ids),
            )
        )
        self.state.backfill_log = log[-256:]

    # -- Block builders (delegated to render.py) ------------------------------

    def _l1_score_block(self) -> dict[str, Any]:
        return build_l1_score_block(self._buffer, self.short_formula_template)

    # -- Internal --------------------------------------------------------------

    def _active_node(self) -> str | None:
        """Which optimizer node is working. The in-flight LLM call names its node and wins;
        otherwise the phase does, which is what keeps a node lit across the gap between two
        calls in one phase."""
        in_flight = self.state.in_flight
        if in_flight is not None:
            return in_flight.node
        return _STATE_TO_NODE[self.state.state]

    def _current_round_nodes(self) -> dict[str, dict[str, Any]]:
        """THIS round's node blocks. ``_sticky_llm_calls`` is most-recent-fire-per-slot and
        survives round transitions, so it is filtered by each block's own ``round``: presence in
        this map is the client's whole definition of "this node has fired", and unfiltered, a new
        round opened showing the previous one's models as its own."""
        blocks = {
            node: block
            for node, block in self._sticky_llm_calls.items()
            if block.get("round") == self.state.round
        }
        if self._buffer.candidates:
            blocks["l1_score"] = self._l1_score_block()
        # Reading order, not execution order: generate and critique head the block because they
        # are what an operator opens the file for.
        ordered = {
            k: blocks.pop(k) for k in ("l1_generate", "l1_critique", "l1_score") if k in blocks
        }
        ordered.update(blocks)
        return ordered

    def _persist(self) -> None:
        """Compose, then swap atomically via ``write_json`` — polling readers never see a torn
        payload. A fold with no ``state_path`` composes on demand instead (``compose``), so
        nothing here fires per record for a replay that will be read once.

        A refused swap is reported, never raised: the ledger is the truth here, so the cost is one
        stale poll, while raising costs the caller whatever it was measuring."""
        if self.state_path is None:
            return
        self.compose()
        try:
            write_json(self.state_path, self.state.model_dump(), default=str)
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

    def compose(self) -> LiveDashboardState:
        """Settle the served shape onto ``state`` and return it — everything a reader needs that is
        derived rather than folded. Idempotent; the write path calls it, and a replay calls it once
        at the end rather than after every record."""
        s = self.state
        # Stamp the moment this write is OF, before composing anything from it. The write is
        # debounced, so it lands after later records have already arrived — `at_offset` names
        # the fold, never the file's mtime, and a reader joins the event stream from here.
        s.at_offset = self.at_offset
        s.current_round = CurrentRound(
            # `state.round`, never the candidate buffer's, which only advances at
            # `L1_GENERATE:enter` — one behind from `round:enter` onward, so every reader
            # comparing the two concludes the round has already closed.
            round=s.round,
            active_node=self._active_node(),
            candidates=build_candidate_rows(self._buffer),
            nodes=self._current_round_nodes(),
            pobb=build_pobb_block(self._core, self._buffer.p_best_top),
            overlap=self._buffer.overlap,
        )
        # The ARMED ceiling, never the one INIT declared. `_build_budget_gate` prefers
        # `spend_cap.json` over the launch-composed cap, so serving the stamped value left every
        # reader — the budget control's prefill, the run strip — quoting a number nothing would
        # enforce. Overlaid at the single write, so no reader has to join two sources.
        if s.run_limits is not None:
            armed_usd, armed_tokens = read_spend_caps(self.cycle_dir)
            armed: dict[str, float | int] = {}
            if armed_usd is not None:
                armed["spend_budget_usd"] = armed_usd
            if armed_tokens is not None:
                armed["token_budget"] = armed_tokens
            if armed:
                s.run_limits = s.run_limits.model_copy(update=armed)
        # Same shape as the spend caps above: the flag is the REQUEST and the loop reads it at
        # its own cadence, so the browser learns a press landed here or not at all.
        s.sample_lookahead_armed = read_armed_cells(self.cycle_dir)
        s.wallclock_serialized_at = utcnow_iso()
        # The typed model IS the on-disk shape, and `extra="forbid"` rejects an undeclared
        # attribute at the mutation site — so a field can neither silently vanish nor appear
        # undeclared.
        return s


def resolve_resume_state(
    seed_dir: Path,
    seed_hop: CycleHop,
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
    prior = fold_at(seed_dir, seed_hop)
    surviving = [
        r for r in prior.rounds if resumed_from_round is None or r.round < resumed_from_round
    ]
    # A `RoundSummary` carries the reading WHOLE and the election reads the flattened pair, so the
    # rows go through the same projection the cycle index does — handed the summary's own dump the
    # derivation sees no shared cell anywhere and collapses the whole trajectory onto round 0.
    best, _ = best_round_on_shared_cells(
        [{**r.model_dump(), **overlap_row(r.overlap)} for r in surviving]
    )
    rounds_dir = CycleLayout(active_cycle_dir).rounds
    on_disk = max(
        (n for p in rounds_dir.glob(ROUND_GLOB) if (n := round_number(p)) is not None),
        default=0,
    )
    return prior.model_copy(
        update={"rounds": surviving, "round": max(prior.round, on_disk), "best": best}
    )


def fold_at(cycle_dir: Path, hop: CycleHop, *, at_offset: int | None = None) -> LiveDashboardState:
    """The cycle's dashboard state as of ledger offset *at_offset* — ``None`` for the head. The
    SAME fold the runner drives, given records off disk instead of off an append.

    ``at_offset`` is a physical offset in THIS cycle's own ledger: the space
    ``DerivedView.at_offset``, ``ProjectionEnvelope.sequence`` and ``RayItem.offset`` share, so a
    ray item taken from this course addresses a fold directly. A fork's parent prefix is walked in
    front of it and is NOT in that space, which is why the cut rides ``iter(own_limit=…)`` rather
    than a comparison inside the loop.

    The ledger is the ONLY input. The wiring constants (``WIRING_FIELDS``) are stamped at run
    start and ride no record, so they come back at their model defaults here and the serving route
    stamps the live ones over them — the same overlay it already does for ``run_phase``. Reading
    them from the head ``dashboard.json`` instead made a projection depend on its own output, and
    it is what kept every OTHER consumer of a past round — the lineage tree, the mask spine — from
    folding rather than re-deriving."""
    view = LiveDashboardView(
        CycleDir(cycle_dir),
        state_path=None,
        hop=hop,
        # The wiring, at the model's own defaults — `WIRING_FIELDS` names what the caller stamps.
        session_id="",
        l1_patience=0,
        n_variants=0,
        sp_budget_ttest=0,
        headline_metric="accuracy",
    )
    limit = None if at_offset is None else at_offset + 1
    for offset, record in open_with_history(CycleDir(cycle_dir)).iter(limit):
        view.on_record(record, offset)
    return view.compose()


__all__ = ["LiveDashboardView", "fold_at", "resolve_resume_state"]
