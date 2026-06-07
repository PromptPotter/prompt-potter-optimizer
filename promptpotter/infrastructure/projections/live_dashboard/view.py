"""``LiveDashboardView`` — session-family ``dashboard.json`` writer.

This class is the **scalar-state mutation dispatcher**: phase / snapshot /
LLM-call / token-usage records mutate ``self.state`` (the on-disk scalar
shape) and the sticky LLM-call mirror, and the debounced ``_persist`` plumbing
flushes them to disk. The two other concerns it orchestrates live in sibling
modules:

- **Round buffer** — :class:`.round_buffer.RoundBuffer` owns the per-round
  candidate buffer feeding ``dashboard.json::current_round.nodes.l1_score``;
  ``_handle_snapshot`` routes per-candidate / per-sample writes to it.
- **Block builders** — :mod:`.render` (``build_l1_score_block`` /
  ``build_pobb_block`` / ``fmt_sample_line``) projects scalar state + round
  buffer to the dashboard.json output shape; called by ``_persist`` and the
  round-complete flush in ``_handle_phase``.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.results import OriginSummary, RoundSummary, candidate_label
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
from promptpotter.infrastructure.projections.live_dashboard.round_summary import build_round_summary
from promptpotter.infrastructure.projections.live_dashboard.state import (
    BackendWarning,
    BackfillLogEntry,
    DashboardError,
    InFlightCall,
    LiveDashboardState,
    LoopWarning,
    RunLimits,
    SpendRollup,
)
from promptpotter.infrastructure.projections.live_state import (
    LiveStateCore,
    apply_p_best_update,
    apply_phase,
    backfill_spend_rates,
    empty_spend,
)
from promptpotter.infrastructure.store import (
    cycle_dir_for,
    session_dir_for,
    write_json,
)
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
_PHASE_TO_STATE: dict[str, str] = {
    CampaignPhase.INIT: "init",
    CampaignPhase.ORIGIN: "origin",
    CampaignPhase.L1_GENERATE: "l1_generate",
    CampaignPhase.REFINE_STRATEGY: "l2_refining",
    CampaignPhase.MODIFY_PLAN: "l3_replanning",
    CampaignPhase.ESCALATION: "escalation",
}


class LiveDashboardView(DerivedView):
    """Per-cycle dashboard writer; not an optimizer checkpoint."""

    def __init__(
        self,
        cycle_dir: CycleDir,
        session_dir: Path,
        *,
        campaign_id: str,
        cycle_id: str,
        session_id: str,
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
        resume_from: dict[str, Any] | None = None,
        recorder: AuditTrailView | None = None,
        initial_llm_nodes: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        cycle_path = Path(cycle_dir)
        self.cycle_dir = cycle_path
        self.state_path = cycle_path / "dashboard.json"
        self.session_dir = session_dir
        self._recorder = recorder
        self.patience_max = l1_patience
        r = resume_from or {}
        # The schema (LiveDashboardState) IS the on-disk shape — _persist dumps
        # this instance directly, so every field's presence + type is enforced
        # at write time. Pass identity + resume-inherited values; schema defaults
        # supply the rest (run_phase="running", empty lists, None markers). The
        # view only exists while a run is actively writing, so "running" is the
        # right default — mark_stopped flips it to terminal, pause/resume declare
        # transitions; it is never inherited from a resumed terminal/paused snapshot.
        self.state = LiveDashboardState(
            # Self-identity stamp — never inherited from resume_from / phase event.
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            session_id=session_id,
            state=r.get("state", "init"),
            state_since=utcnow_iso(),
            stop_reason=r.get("stop_reason"),
            # Inherit round so re-instantiation doesn't zero the operator-visible pointer.
            round=int(r.get("round") or 0),
            patience=f"0/{l1_patience}",
            # Merge onto defaults: a resumed snapshot may carry a partial origin
            # (older data wrote accuracy without samples); fill the required field.
            origin=OriginSummary.model_validate(
                {"accuracy": 0.0, "samples": 0, **(r.get("origin") or {})}
            ),
            rounds=[RoundSummary.model_validate(x) for x in (r.get("rounds") or [])],
            best=float(r.get("best") or 0.0),
            composite_fitness_formula=r.get("composite_fitness_formula"),
            total_queries_scored=int(r.get("total_queries_scored") or 0),
            total_backend_calls=int(r.get("total_backend_calls") or 0),
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
            spend=SpendRollup.model_validate(backfill_spend_rates(r.get("spend") or empty_spend())),
        )
        self.short_formula_template: str | None = None
        self._buffer = RoundBuffer()
        # Sticky LLM-call mirror for ``current_round.nodes`` — owned here, not on the
        # audit-trail. Each ``LLMCallRecord`` mutates the matching phase-keyed slot;
        # the audit-trail records the same event independently into its round flush.
        self._sticky_llm_calls: dict[str, dict[str, Any]] = dict(initial_llm_nodes or {})
        self._core = LiveStateCore(
            round_num=self.state.round,
            origin_acc=self.state.origin.accuracy,
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
        origin_accuracy: float,
        cycle_id: str | None,
        *,
        project_root: str,
        session_id: str,
        campaign_id: str,
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
        resumed_from_round: int | None = None,
        recorder: AuditTrailView | None = None,
        seed_from_cycle_id: str | None = None,
    ) -> LiveDashboardView | None:
        """Build projection, or ``None`` if ids missing.

        Writes ``cycles/{cycle_id}/dashboard.json`` — each cycle (root, fork,
        sweep, diag) owns its own live file, stamped with its own ``cycle_id``.
        ``seed_from_cycle_id`` (set for a fork) names the cycle to read the
        prior dashboard from — the fork inherits the parent's trajectory up to
        the cut while counting its own (copied) round files; ``None`` seeds from
        the cycle's own dir (root / resume).
        """
        if not (project_root and session_id and campaign_id and cycle_id):
            return None

        tenant_root = Path(project_root)
        session_dir = session_dir_for(tenant_root, session_id)
        cycle_dir = CycleDir(cycle_dir_for(tenant_root, campaign_id, cycle_id))
        seed_dir = (
            cycle_dir_for(tenant_root, campaign_id, seed_from_cycle_id)
            if seed_from_cycle_id
            else Path(cycle_dir)
        )

        resume_from = resolve_resume_state(
            seed_dir,
            Path(cycle_dir),
            origin_accuracy,
            resumed_from_round,
        )
        # Resume seed for the sticky LLM-call mirror — surfaces prior rounds' L1/L2/L3
        # outputs on the dashboard before the first new call lands.
        initial_llm_nodes = read_most_recent_round_nodes(audit_rounds_dir(Path(cycle_dir)))

        return cls(
            cycle_dir,
            session_dir,
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            session_id=session_id,
            l1_patience=l1_patience,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
            resume_from=resume_from,
            recorder=recorder,
            initial_llm_nodes=initial_llm_nodes,
        )

    # -- State transitions ----------------------------------------------------

    def _set_state(self, name: str) -> None:
        """Liveness transition — keeps ``state`` and ``state_since`` in lockstep."""
        self.state.state = name
        self.state.state_since = utcnow_iso()

    def mark_stopped(self, reason: str) -> None:
        """Finalize hook — writes terminal state + reason so dashboard tail-readers see it without index.json.

        The operator-facing ``error`` block (``kind`` + ``message``) is owned
        by :meth:`_handle_error` which subscribes to ``ErrorRecord`` on the
        canonical ledger; ``mark_stopped`` only flips the liveness state.
        """
        self.state.stop_reason = reason
        self.state.run_phase = "terminal"
        self._set_state("stopped")
        self._flush_pending_persist()

    # -- Write coalesce -------------------------------------------------------
    # Snapshot / token / LLM-call handlers fire hundreds of times per round;
    # serialising 90 KB of JSON on each one buys nothing the 2 s polling
    # webapp can see. `_schedule_persist` arms a debounced flush; phase
    # boundaries call `_flush_pending_persist` so round/origin/L1 transitions
    # land on disk instantly.

    def on_record(self, record: CycleRecord, offset: int) -> None:
        """Serialise every event under `_persist_lock` so the Timer-thread
        flush can't observe mid-mutation `state` / `_buffer.candidates`."""
        with self._persist_lock:
            super().on_record(record, offset)

    def _schedule_persist(self) -> None:
        """Mark dirty and arm a 250 ms debounce. Caller must hold `_persist_lock`."""
        self._persist_dirty = True
        if self._persist_timer is not None:
            self._persist_timer.cancel()
        timer = threading.Timer(_DASHBOARD_DEBOUNCE_S, self._fire_debounced_persist)
        timer.daemon = True
        self._persist_timer = timer
        timer.start()

    def _fire_debounced_persist(self) -> None:
        """Timer callback — runs on a Timer thread. Swallows exceptions so a
        torn-down cycle dir (test cleanup, mid-shutdown disk error) can't
        propagate into the daemon-thread default handler."""
        try:
            with self._persist_lock:
                if not self._persist_dirty:
                    return
                self._persist_dirty = False
                self._persist_timer = None
                self._persist()
        except Exception:
            logger.exception("debounced dashboard persist failed")

    def _flush_pending_persist(self) -> None:
        """Cancel any pending debounce and write immediately. Called from
        phase-boundary handlers, `mark_stopped`, and `drain`."""
        with self._persist_lock:
            if self._persist_timer is not None:
                self._persist_timer.cancel()
                self._persist_timer = None
            self._persist_dirty = False
            self._persist()

    def drain(self) -> None:
        """Teardown hook — flush any pending debounced write so the on-disk
        snapshot mirrors the final ledger truth before the cycle closes."""
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
            if self.state.run_phase not in ("terminal", record.event):
                self.state.run_phase = record.event
                self._flush_pending_persist()
            return

        if record.phase == "backend" and record.event == "warning":
            # Surface backend retries (429 / 5xx / transport) — retry behaviour itself is unchanged.
            payload = dict(record.payload or {})
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
            payload = record.payload or {}
            round_result = payload.get("round_result")
            l1_stall = int(payload.get("l1_stall_count") or 0)
            if round_result is not None:
                self._absorb_round_complete(round_result.accuracy, l1_stall)
                if self._recorder is not None:
                    self._recorder.set_l1_score(self._l1_score_block(round_result))
                # Append round summary; re-firing the same round (replay / sweep) replaces in place.
                summary = build_round_summary(round_result)
                rounds_list = [r for r in self.state.rounds if r.round != round_result.round]
                rounds_list.append(summary)
                rounds_list.sort(key=lambda r: r.round)
                self.state.rounds = rounds_list
                self._flush_pending_persist()
            return

        # Origin exit: push the live l1_score block to the recorder so `round_0000.json`
        # carries the origin candidate snapshot when audit_trail flushes (dashboard → audit order).
        if (
            record.phase == "origin"
            and record.event == "exit"
            and self._recorder is not None
            and self._buffer.candidates
        ):
            self._recorder.set_l1_score(self._l1_score_block())

        payload = record.payload or {}
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
        payload = record.payload or {}
        ci = int(record.candidate_idx or 0)
        ct = int(record.candidate_total or 0)
        qi = int(record.sample_idx or 0)
        qt = int(record.sample_total or 0)
        if ev == "sample_started":
            self._update_sample_markers(ci, ct, qi, qt)
            self.state.current_query_payload = (payload.get("query_text") or "")[:120]
            sid = payload.get("sample_id")
            self.state.current_sample_id = int(sid) if sid is not None else None
            self._set_state("scoring")
            self._schedule_persist()
        elif ev == "sample_scored":
            result = payload.get("result") or {}
            self._update_sample_markers(ci, ct, qi, qt)
            self._absorb_sample_scored(result, last_in_candidate=(qi + 1 >= qt))
            self._buffer.append_sample(ci, ct, qi, qt, result)
            self._schedule_persist()
        elif ev == "candidate_started":
            self._buffer.seed_candidate(
                ci,
                ct,
                payload.get("changes_description") or "",
                payload.get("pp_override"),
                payload.get("prompt_fields"),
            )
            # Persist the bare seed now (samples empty, scores None) so the
            # lineage draws the round's path the instant a candidate is known —
            # the round is certain, it's only waiting on the first sample. The
            # candidate renders as a pending node (null accuracy) until
            # sample_scored fills it in.
            self._schedule_persist()
        elif ev == "candidate_scored":
            scores = payload.get("scores") or {}
            self._update_current_acc(scores)
            self._buffer.set_candidate_scores(ci, ct, scores)
            # flushed by next sample_scored or by round_complete.
        elif ev == "p_best_update":
            current_id = payload.get("current_id") or ""
            n_samples = int(payload.get("n_samples") or 0)
            p_best = {str(k): float(v) for k, v in (payload.get("p_best") or {}).items()}
            self._buffer.update_p_best(ci, ct, current_id, n_samples, p_best)
            # Mirror the latest snapshot into the shared core so LiveDisplay sees
            # the same round-wide P(best) state.
            apply_p_best_update(self._core, current_id, n_samples, p_best)
            self._schedule_persist()
        elif ev == "sample_order_preview":
            order_raw = payload.get("sample_order")
            if isinstance(order_raw, list):
                self.state.hard_sample_order = [int(sid) for sid in order_raw]
                self._schedule_persist()
        elif ev == "pobb_backfill":
            self._append_backfill(
                int(record.round or 0),
                ci,
                ct,
                int(payload.get("sample_id") or 0),
                [str(p) for p in (payload.get("prior_ids") or [])],
            )
            self._schedule_persist()

    # -- Scalar mutations -----------------------------------------------------

    def _apply_phase(self, event: PhaseEvent, view: Any) -> None:
        s = self.state
        if event.round is not None:
            s.round = event.round
        # L1_SCORE has no _PHASE_TO_STATE entry — sample_started/scored own its transitions.
        if event.event == "enter" and s.state != "stopped":
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
            # ``env`` and ``state`` are runtime-only keys stripped by
            # ``RunCallbacks.on_phase`` before the record is persisted/streamed
            # (the live ``Cycle``/``Session`` hold the BackendStore the JSON
            # serializer can't walk). Origin accuracy + sample count ride the
            # typed view instead (read by attribute — see ``apply_phase``).
            if view is not None:
                s.origin = OriginSummary(
                    accuracy=float(getattr(view, "origin_acc", 0.0) or 0.0),
                    samples=int(getattr(view, "origin_samples", 0) or 0),
                )
                s.composite_fitness_formula = getattr(view, "composite_fitness_formula", None)
                self.short_formula_template = getattr(view, "composite_fitness_formula_short", None)
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

        # Mirror origin/best/round into the shared core for LiveDisplay parity.
        apply_phase(self._core, event, view)
        if self._core.best_acc > s.best:
            s.best = self._core.best_acc

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
        self._set_state("between_candidates" if last_in_candidate else "between_samples")

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        """Sole writer for ``state['spend']``: optimizer → ``loop``, backend → ``backend``.

        Bucket-add + total recompute inline in one method. ``record.cost_usd``
        (provider-reported wire cost, e.g. OpenRouter ``usage.cost``) short-
        circuits the rate-table when present; absent ⇒ ``compute_usd`` rate-
        tables the tokens. Per-record write keeps the projection self-contained:
        no parallel writer, no helper chain.
        """
        spend = self.state.spend
        bucket = spend.loop if record.kind == "optimizer" else spend.backend
        in_tok = int(record.input_tokens)
        out_tok = int(record.output_tokens)
        bucket.input_tokens += in_tok
        bucket.output_tokens += out_tok
        if record.model and not bucket.model:
            bucket.model = record.model
        usd = compute_usd(record.model, in_tok, out_tok, override_usd=record.cost_usd)
        if usd is not None:
            bucket.used_usd = round(bucket.used_usd + usd, 6)
            bucket.rate_known = True
        spend.total_used_usd = round(spend.backend.used_usd + spend.loop.used_usd, 6)
        self._schedule_persist()

    @property
    def spend_total_used_usd(self) -> float:
        """Clean accessor for the spend halt probe.

        Reads the live spend rollup the projection owns; the halt loop goes
        through this property so the dashboard remains the single owner of
        spend semantics (no probe reaching into ``self.state.spend``)."""
        return self.state.spend.total_used_usd

    @property
    def spend_total_tokens(self) -> int:
        """Token twin of ``spend_total_used_usd`` — the token halt probe's
        source. Same single-owner discipline: the gate reads this accessor, not
        ``self.state.spend`` directly."""
        return self.state.spend.total_tokens_used

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        """Publish the in-flight optimizer LLM call to `dashboard.json::in_flight` —
        kills the multi-minute blind spot during reasoning-heavy critique calls.
        Cleared by the paired `LLMCallRecord` in `_handle_llm_call`.
        """
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
        """Mirror the call into the sticky LLM-node store + clear the in-flight slot.

        The sticky store backs ``dashboard.json::current_round.nodes`` and survives
        round transitions (most-recent fire per phase-keyed slot). In-flight match
        is by ``call_id`` so out-of-order delivery can't clear the wrong call.
        """
        self._sticky_llm_calls[record.node] = {
            **build_node_block(record),
            "round": self.state.round,
        }
        in_flight = self.state.in_flight
        if in_flight is not None and record.call_id and in_flight.call_id == record.call_id:
            self.state.in_flight = None
        self._schedule_persist()

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        """Heartbeat → re-persist so `wallclock_serialized_at` stays fresh during 30-90s
        optimizer LLM phases that fire no other records. No state mutation, only the timestamp moves.
        """
        del record
        self._schedule_persist()

    def _handle_error(self, record: ErrorRecord) -> None:
        """Sole writer of ``dashboard.json::error``.

        Subscribed to the ledger ``ErrorRecord`` emitted by the runner's
        three ``except`` sites in ``application/runner/{entry,loop}.py``;
        nothing else writes this block. The webapp reads it to render the
        operator-facing crash summary without parsing the traceback out
        of ``index.json``.
        """
        self.state.error = DashboardError(
            kind=record.kind,
            message=record.message,
            stop_reason=record.stop_reason,
        )
        self._schedule_persist()

    def _handle_round_warning(self, record: RoundWarningRecord) -> None:
        """Sole writer of ``dashboard.json::recent_loop_warnings``.

        Mirrors ``recent_backend_warnings`` — a rolling, capped list of the
        optimizer-loop degradations that previously logged only to stdout.
        Flushed immediately: a zero-candidate round is a material fact the
        operator (or the file-tree reader) must see without waiting on the
        debounce.
        """
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
        self.state.current_acc = round(scores.get("accuracy", 0.0), 4)

    def _absorb_round_complete(self, accuracy: float, l1_stall_count: int) -> None:
        s = self.state
        if accuracy > s.best:
            s.best = round(accuracy, 4)
        s.patience = f"{l1_stall_count}/{self.patience_max}"

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
        """Append a per-sample paired-PoBB backfill event to ``state["backfill_log"]``.

        Webapp + notebook readers see this under ``dashboard.json::backfill_log``.
        Each entry names the round/candidate the backfill fired during, the
        sample the priors were caught up on, and which priors gained a
        measurement — absence of an entry for a given sample means every
        prior was already cached for it. Capped at 256 entries (per-sample
        events can accumulate quickly: ~N samples × M priors × K candidates
        per round).
        """
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
        """Thread the view's live state into the pure
        :func:`~promptpotter.infrastructure.projections.live_dashboard.render.build_l1_score_block`
        projection — the only state coupling the builder needs."""
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
