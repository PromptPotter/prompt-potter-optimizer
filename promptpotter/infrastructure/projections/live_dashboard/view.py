from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.phases import CampaignPhase, DashboardState, PhaseEvent, RunPhase
from promptpotter.domain.results import HeadlineMetric, candidate_label
from promptpotter.domain.run_records import (
    CycleRecord,
    ErrorRecord,
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    RoundWarningRecord,
    SnapshotRecord,
    TokenUsageRecord,
)
from promptpotter.infrastructure.projections.audit_trail import (
    audit_rounds_dir,
    build_node_block,
    read_most_recent_round_nodes,
)
from promptpotter.infrastructure.projections.base import DerivedView
from promptpotter.infrastructure.projections.live_dashboard.factory import resolve_resume_state
from promptpotter.infrastructure.projections.live_dashboard.render import (
    build_l1_score_block,
    build_pobb_block,
)
from promptpotter.infrastructure.projections.live_dashboard.round_buffer import RoundBuffer
from promptpotter.infrastructure.projections.live_dashboard.round_summary import (
    build_round_summary,
    origin_rows_from_disk,
)
from promptpotter.infrastructure.projections.live_dashboard.state import (
    BackendWarning,
    BackfillLogEntry,
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
)
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for, session_dir_for
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import has_pipeline_warnings
from promptpotter.shared.spend import compute_usd

if TYPE_CHECKING:
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.projections.audit_trail import AuditTrailView

logger = logging.getLogger(__name__)


# Coalesce burst writes from the per-sample / per-token / per-LLM-call hot
# paths onto one debounced disk write. The 2 s webapp poll cadence means
# sub-second writes are wasted; phase boundaries still flush immediately so
# `dashboard.json` is current at every round/origin/L1 transition.
_DASHBOARD_DEBOUNCE_S = 0.25


# L1_SCORE absent: driven by sample_started / sample_scored.
# The VALUES are the sole declaration of the `dashboard.json::state` vocabulary — keep them
# typed, or the webapp mirrors bare strings against no source. The key stays `str` because
# `PhaseEvent.phase` is wider than `CampaignPhase` (a round-boundary event carries "round").
_PHASE_TO_STATE: dict[str, DashboardState] = {
    CampaignPhase.INIT: DashboardState.INIT,
    CampaignPhase.ORIGIN: DashboardState.ORIGIN,
    CampaignPhase.L1_GENERATE: DashboardState.L1_GENERATE,
    CampaignPhase.REFINE_STRATEGY: DashboardState.L2_REFINING,
    CampaignPhase.MODIFY_PLAN: DashboardState.L3_REPLANNING,
    CampaignPhase.ESCALATION: DashboardState.ESCALATION,
}


class LiveDashboardView(DerivedView):
    """Per-cycle dashboard writer; not an optimizer checkpoint."""

    def __init__(
        self,
        cycle_dir: CycleDir,
        session_dir: Path,
        *,
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
        self.state_path = CycleLayout(cycle_path).dashboard
        self.session_dir = session_dir
        self._recorder = recorder
        self.patience_max = l1_patience
        # The schema IS the on-disk shape — _persist dumps this instance directly — so
        # it also owns which of its fields a resume inherits and which this process
        # stamps fresh. See LiveDashboardState.for_run.
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
        # Sticky LLM-call mirror for ``current_round.nodes`` — owned here, not on the
        # audit-trail. Each ``LLMCallRecord`` mutates the matching phase-keyed slot;
        # the audit-trail records the same event independently into its round flush.
        self._sticky_llm_calls: dict[str, dict[str, Any]] = dict(initial_llm_nodes or {})
        self._core = LiveStateCore(
            round_num=self.state.round,
            # Origin anchor seeds from round 0 if it's already on disk (resume);
            # otherwise apply_phase sets it at INIT:exit. Origin is just round 0.
            origin_acc=next((r.accuracy for r in self.state.rounds if r.round == 0), 0.0),
            best_acc=self.state.best,
        )
        # Debounce coalesces snapshot/token/LLM-call writes onto one disk
        # flush per ~250 ms (well under the 2 s poll cadence). RLock so the
        # boundary-flush path can be called from inside a handler that
        # already holds the lock via `on_record`. The Timer thread acquires
        # the same lock so it can never race a mutating handler.
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
    ) -> LiveDashboardView | None:
        """``seed_from_cycle_id`` names the cycle to read the prior dashboard from — a fork inherits
        the parent's trajectory up to the cut while counting its own copied round files."""
        if not (tenant_root and session_id and hop.campaign_id and hop.cycle_id):
            return None

        root = WorkspaceDir(Path(tenant_root))
        session_dir = session_dir_for(root, session_id)
        cycle_dir = CycleDir(cycle_dir_for(root, hop))
        seed_dir = (
            cycle_dir_for(root, CycleHop(campaign_id=hop.campaign_id, cycle_id=seed_from_cycle_id))
            if seed_from_cycle_id
            else Path(cycle_dir)
        )

        resume_from = resolve_resume_state(
            seed_dir,
            Path(cycle_dir),
            resumed_from_round,
        )
        # Resume seed for the sticky LLM-call mirror — surfaces prior rounds' L1/L2/L3
        # outputs on the dashboard before the first new call lands.
        initial_llm_nodes = read_most_recent_round_nodes(audit_rounds_dir(Path(cycle_dir)))

        return cls(
            cycle_dir,
            session_dir,
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
        state = resolve_resume_state(cycle_path, cycle_path, None) or LiveDashboardState(
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            session_id=session_id,
            state_since=utcnow_iso(),
            n_variants=0,
            sp_budget_ttest=0,
        )
        if interrupted:
            state.run_phase = RunPhase.PAUSED
        else:
            state.error = DashboardError(
                kind="launch_failed",
                message=str(exc) or type(exc).__name__,
                stop_reason="launch_aborted",
            )
            state.stop_reason = f"{type(exc).__name__}: {exc}"
            state.run_phase = RunPhase.TERMINAL
            state.state = DashboardState.STOPPED
        state.state_since = utcnow_iso()
        write_json(CycleLayout(cycle_path).dashboard, state.model_dump(), default=str)

    # -- State transitions ----------------------------------------------------

    def _set_state(self, name: DashboardState) -> None:
        self.state.state = name
        self.state.state_since = utcnow_iso()

    def mark_stopped(self, reason: str) -> None:
        self.state.stop_reason = reason
        self.state.run_phase = RunPhase.TERMINAL
        self._set_state(DashboardState.STOPPED)
        self._flush_pending_persist()

    # -- Write coalesce -------------------------------------------------------
    # Snapshot / token / LLM-call handlers fire hundreds of times per round;
    # serialising 90 KB of JSON on each one buys nothing the 2 s polling
    # webapp can see. `_schedule_persist` arms a debounced flush; phase
    # boundaries call `_flush_pending_persist` so round/origin/L1 transitions
    # land on disk instantly.

    def on_record(self, record: CycleRecord, offset: int) -> None:
        with self._persist_lock:
            super().on_record(record, offset)

    def _schedule_persist(self) -> None:
        """An already-armed timer is left to run rather than replaced — the flush reads
        ``_persist_dirty``, not the event, so staleness is bounded from the FIRST mutation."""
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
                # Unconditionally, and before the dirty test: this slot is what `_schedule_persist`
                # reads to decide whether a flush is already armed, so leaving it set on the
                # not-dirty path would disarm the debounce for the rest of the run.
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
            # Control transition declared by the runner (running / paused /
            # stopping). The sole writer of dashboard.json::run_phase short of
            # the terminal `mark_stopped`. Flushing here bumps the file mtime at
            # the transition, so the 304-cached dashboard route serves the new
            # phase and a stale (paused) file still reads as paused.
            event_phase = RunPhase(record.event)
            if self.state.run_phase not in (RunPhase.TERMINAL, event_phase):
                self.state.run_phase = event_phase
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

        if record.phase == "round" and record.event == "display":
            payload = record.payload
            # Full RoundResult rides the in-memory-only field (the persisted
            # payload['round_result'] is the lean 3-scalar form for the SSE tail).
            round_result = record.live_round_result
            l1_stall = int(payload.get("l1_stall_count") or 0)
            hearts_raw = payload.get("hearts")
            hearts = None if hearts_raw is None else int(hearts_raw)
            if round_result is not None:
                self._absorb_round_complete(round_result.accuracy, l1_stall, hearts)
                if self._recorder is not None:
                    self._recorder.set_l1_score(self._l1_score_block(round_result))
                # Append round summary; re-firing the same round (replay / sweep) replaces in place.
                origin_rows = (
                    [] if round_result.round == 0 else origin_rows_from_disk(self.cycle_dir)
                )
                summary = build_round_summary(round_result, origin_rows)
                rounds_list = [r for r in self.state.rounds if r.round != round_result.round]
                rounds_list.append(summary)
                rounds_list.sort(key=lambda r: r.round)
                self.state.rounds = rounds_list
                # Settle the served headline lift AFTER the append so round 0's own
                # summary is present when it computes its first value.
                origin = next((r.accuracy for r in self.state.rounds if r.round == 0), None)
                self.state.headline_delta = (
                    round(self.state.best - origin, 4) if origin is not None else None
                )
                self._flush_pending_persist()
            return

        payload = record.payload
        view = payload.get("view")
        data = payload.get("data") or {}
        event = PhaseEvent(
            phase=record.phase,
            event=record.event,
            round=record.round,
            data=data,
        )
        self._apply_phase(event, view)
        # L1_GENERATE:enter wipes the live candidate buffer (current_round.nodes.l1_score) —
        # historical `rounds[]` is untouched.
        if event.phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            self._buffer.reset(self.state.round)
        self._flush_pending_persist()

    def _handle_snapshot(self, record: SnapshotRecord) -> None:
        ev = record.event
        payload = record.payload
        ci = int(record.candidate_idx or 0)
        ct = int(record.candidate_total or 0)
        qi = int(record.sample_idx or 0)
        qt = int(record.sample_total or 0)
        if ev == "sample_started":
            self._update_sample_markers(ci, ct, qi, qt)
            self.state.current_query_payload = (payload.get("query_text") or "")[:120]
            sid = payload.get("sample_id")
            self.state.current_sample_id = int(sid) if sid is not None else None
            self._set_state(DashboardState.SCORING)
        elif ev == "sample_scored":
            result = payload.get("result") or {}
            self._update_sample_markers(ci, ct, qi, qt)
            self._absorb_sample_scored(result, last_in_candidate=(qi + 1 >= qt))
            self._buffer.append_sample(ci, ct, qi, qt, result)
        elif ev == "candidate_started":
            # Seed the candidate (samples empty, scores None) so the lineage
            # draws the round's path the instant a candidate is known — it
            # renders as a pending node (null accuracy) until sample_scored
            # fills it in. The tail flush below persists the seed immediately.
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
            self._update_current_acc(scores)
            self._buffer.set_candidate_scores(ci, ct, scores)
        elif ev == "p_best_update":
            current_id = payload.get("current_id") or ""
            n_samples = int(payload.get("n_samples") or 0)
            p_best = float(payload.get("p_best") or 0.0)
            self._buffer.update_p_best(ci, ct, current_id, n_samples, p_best)
            # Mirror the latest snapshot into the shared core so LiveDisplay sees
            # the same round-wide P(best) state.
            apply_p_best_update(self._core, current_id, n_samples, p_best)
        elif ev == "pobb_backfill":
            self._append_backfill(
                int(record.round or 0),
                ci,
                ct,
                int(payload.get("sample_id") or 0),
                [str(p) for p in (payload.get("prior_ids") or [])],
            )
        # One debounced flush for EVERY snapshot event. Each branch mutates live state, so
        # the cadence is uniform and no branch can forget to flush — a branch that does
        # leaves a finished candidate's scores unwritten until the next event.
        self._schedule_persist()

    # -- Scalar mutations -----------------------------------------------------

    def _apply_phase(self, event: PhaseEvent, view: Any) -> None:
        s = self.state
        if event.round is not None:
            s.round = event.round
        # L1_SCORE has no _PHASE_TO_STATE entry — sample_started/scored own its transitions.
        if event.event == "enter" and s.state != DashboardState.STOPPED:
            mapped = _PHASE_TO_STATE.get(event.phase)
            if mapped is not None:
                self._set_state(mapped)

        phase, data = event.phase, event.data
        if phase == CampaignPhase.INIT and event.event == "enter":
            # Stamp the formula early — origin scoring runs before INIT:exit; What-If needs a ref.
            if view is not None:
                formula = getattr(view, "composite_fitness_formula", None)
                if formula is not None:
                    s.composite_fitness_formula = formula
                short = getattr(view, "composite_fitness_formula_short", None)
                if short is not None:
                    self.short_formula_template = short
        elif phase == CampaignPhase.INIT and event.event == "exit":
            config = data["config"]
            # The scoring formula is stamped once at INIT:enter (guarded, above) —
            # origin accuracy now rides round 0 in ``rounds[]`` via the standard
            # ``close_round`` path, so INIT:exit only carries the run-limit surface.
            opt = config.optimization
            self.patience_max = opt.l1_patience
            s.patience = f"0/{self.patience_max}"
            # Static run-limit surface — the operator-facing source for the
            # fork reconcile dialog ("3 of 6 rounds left"). `patience` above is
            # the live stall counter ("N/max"); this is the declared ceilings.
            # A fork re-emits this at its own INIT with the reconciled config,
            # so a steered fork's dashboard shows its own limits.
            s.run_limits = RunLimits(
                max_rounds=opt.max_rounds,
                l1_patience=opt.l1_patience,
                l2_patience=opt.l2_patience,
                l3_patience=opt.l3_patience,
                pobb_epsilon=opt.pobb_epsilon,
                spend_budget_usd=opt.spend_budget_usd,
                token_budget=opt.token_budget,
                lives_cap=opt.lives.cap if opt.lives else None,
            )
        elif phase == "scoring_steer" and event.event == "applied":
            # Operator hot-swap — custom formulas render verbatim (no short form).
            new_formula = data.get("formula")
            if new_formula:
                s.composite_fitness_formula = new_formula
                self.short_formula_template = None
        elif phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            new_round = int(data.get("round", s.round) or 0)
            s.round = new_round
            s.degraded_count = 0
            # Rewind/fork-in-place clamp: drop rounds this run will overwrite. Sole clamp writer;
            # `round:display` is the sole growth site.
            s.rounds = [r for r in s.rounds if r.round < new_round]

        # Mirror origin/round into the shared core for LiveDisplay parity. The core's
        # ``best_acc`` (winner's hard-first SUBSET score) must NOT feed ``s.best`` —
        # ``_absorb_round_complete`` is the sole ``s.best`` writer, on the honest
        # full-population cumulative basis.
        apply_phase(self._core, event, view)

    def _update_sample_markers(self, ci: int, ct: int, qi: int, qt: int) -> None:
        s = self.state
        s.candidate = f"{candidate_label(s.round, ci)}/{ct}"
        s.query = f"{qi + 1}/{qt}"

    def _absorb_sample_scored(self, result: dict[str, Any], *, last_in_candidate: bool) -> None:
        s = self.state
        pd = result.get("pipeline_data") or {}
        query_time = float(pd.get("total_time", 0.0) or 0.0)
        is_cached = bool(result.get("cached", False))

        if result.get("error") or pd.get("error"):
            s.error_count += 1
        if has_pipeline_warnings(result):
            s.degraded_count += 1

        s.total_queries_scored += 1
        if not is_cached:
            s.total_backend_calls += 1
            # Backend spend rides the canonical ledger via emit_token_usage
            # at measure_sample (one TokenUsageRecord per pipeline node per
            # uncached sample); _handle_token_usage is the sole writer.

        s.current_query_payload = None
        s.current_sample_id = None
        s.last_query_elapsed_s = round(query_time, 2)
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
        usd = compute_usd(
            record.model,
            in_tok,
            out_tok,
            override_usd=record.cost_usd,
            provider=record.provider,
        )

        if usd is not None:
            bucket.incurred_usd = round(bucket.incurred_usd + usd, 6)
        elif in_tok or out_tok:
            bucket.incurred_unpriced_tokens += in_tok + out_tok

        if not record.cached:
            bucket.input_tokens += in_tok
            bucket.output_tokens += out_tok
            bucket.reasoning_tokens += int(record.reasoning_tokens)
            if usd is not None:
                bucket.used_usd = round(bucket.used_usd + usd, 6)
                bucket.rate_known = True
            elif in_tok or out_tok:
                # Real tokens billed but no resolvable cost (provider returned no wire
                # cost AND the model isn't in the rate table) — the USD cap can't see
                # this spend. Track it so the dashboard flags the cap as inactive.
                bucket.unpriced_tokens += in_tok + out_tok

        spend.total_used_usd = round(spend.backend.used_usd + spend.loop.used_usd, 6)
        spend.total_incurred_usd = round(spend.backend.incurred_usd + spend.loop.incurred_usd, 6)
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
        # it to 0.0 dropped the live headline to 0% mid-round and then climbed back — the
        # operator read a regression that never happened. Hold the last measured value.
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
        if acc > s.best:
            s.best = acc
        s.patience = f"{l1_stall_count}/{self.patience_max}"
        s.hearts = hearts

    # -- Round-state mutations (snapshot-record fan-out) ----------------------
    # The per-candidate / per-sample / P(best) writes live on the ``RoundBuffer``
    # (``round_buffer.py``); ``_handle_snapshot`` above routes each snapshot kind
    # to the matching ``self._buffer.*`` method. ``_append_backfill`` stays here
    # because it writes to ``state["backfill_log"]`` (scalar state), not the
    # round buffer.

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

    def _l1_score_block(self, round_result: RoundResult | None = None) -> dict[str, Any]:
        return build_l1_score_block(
            self._buffer,
            self.state.composite_fitness_formula,
            self.short_formula_template,
            round_result,
        )

    # -- Internal --------------------------------------------------------------

    def _persist(self) -> None:
        # Atomic tmp+rename via write_json — polling readers never see a torn payload.
        # `nodes` mirrors round_NNNN.json::nodes but only for the current round.
        # `_sticky_llm_calls` is the sole source for non-l1_score blocks; the
        # audit-trail records the same events independently into round_NNNN.json.
        nodes: dict[str, Any] = dict(self._sticky_llm_calls)
        if self._buffer.candidates:
            nodes["l1_score"] = self._l1_score_block()
        ordered = {
            k: nodes.pop(k) for k in ("l1_generate", "l1_critique", "l1_score") if k in nodes
        }
        ordered.update(nodes)
        s = self.state
        s.current_round = {
            "round": self._buffer.round_num,
            "nodes": ordered,
            "pobb": build_pobb_block(self._core, self._buffer.p_best_top),
        }
        s.wallclock_serialized_at = utcnow_iso()
        # The typed model IS the on-disk shape: every scalar field's presence +
        # type is guaranteed because the schema constructs it and `extra="forbid"`
        # rejects setting any undeclared attribute at the mutation site — a field
        # can no longer silently vanish (the run_phase bug) or appear undeclared
        # (run_limits). `default=str` still coerces the free-form `current_round`
        # block (LLM-node payloads) the same way the dict writer did.
        write_json(self.state_path, s.model_dump(), default=str)


__all__ = ["LiveDashboardView"]
