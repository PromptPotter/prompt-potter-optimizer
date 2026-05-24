"""``LiveDashboardView`` — session-family ``dashboard.json`` writer."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import SessionFamilyDir
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.results import candidate_label
from promptpotter.domain.run_records import (
    LLMCallProgressRecord,
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    SnapshotRecord,
    TokenUsageRecord,
)
from promptpotter.infrastructure.projections.base import DerivedView
from promptpotter.infrastructure.projections.live_dashboard import candidate_block
from promptpotter.infrastructure.projections.live_dashboard.factory import resolve_resume_state
from promptpotter.infrastructure.projections.live_dashboard.pobb import build_pobb_block
from promptpotter.infrastructure.projections.live_dashboard.round_summary import build_round_summary
from promptpotter.infrastructure.projections.live_dashboard.score import build_l1_score_block
from promptpotter.infrastructure.projections.live_state import (
    LiveStateCore,
    accumulate_backend_spend,
    apply_phase,
    apply_token_usage,
    backfill_spend_rates,
    empty_spend,
)
from promptpotter.infrastructure.store import (
    cycle_dir_for,
    root_cycle_id,
    session_dir_for,
    write_json,
)
from promptpotter.shared.errors import is_degraded

if TYPE_CHECKING:
    from promptpotter.infrastructure.projections.audit_trail import AuditTrailView

logger = logging.getLogger(__name__)


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
        family_dir: SessionFamilyDir,
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
    ) -> None:
        family_path = Path(family_dir)
        self.family_dir = family_path
        self.state_path = family_path / "dashboard.json"
        self.session_dir = session_dir
        self._recorder = recorder
        self.patience_max = l1_patience
        r = resume_from or {}
        self.state: dict[str, Any] = {
            # Self-identity stamp — never inherited from resume_from / phase event.
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
            "session_id": session_id,
            "state": r.get("state", "init"),
            "state_since": datetime.now(UTC).isoformat(),
            "stop_reason": r.get("stop_reason"),
            # Inherit round so re-instantiation doesn't zero the operator-visible pointer.
            "round": int(r.get("round") or 0),
            "candidate": "",
            "query": "",
            "patience": f"0/{l1_patience}",
            "origin": dict(r.get("origin") or {"accuracy": 0.0, "samples": 0}),
            "rounds": list(r.get("rounds") or []),
            "best": r.get("best", 0.0),
            "current_acc": 0.0,
            "composite_fitness_formula": r.get("composite_fitness_formula"),
            "degraded_count": 0,
            "error_count": 0,
            "backend_retry_count": 0,
            "recent_backend_warnings": [],
            "total_queries_scored": r.get("total_queries_scored", 0),
            "total_backend_calls": r.get("total_backend_calls", 0),
            "current_query_payload": None,
            "current_sample_id": None,
            "hard_sample_order": None,
            "last_query_elapsed_s": 0.0,
            "wallclock_serialized_at": None,
            "n_variants": n_variants,
            "sp_budget_ttest": sp_budget_ttest,
            "spend": backfill_spend_rates(r.get("spend") or empty_spend()),
            "in_flight": None,
        }
        self.short_formula_template: str | None = None
        self._round: dict[str, Any] = {"round": 0, "candidates": {}}
        self._core = LiveStateCore(
            round_num=int(self.state.get("round") or 0),
            origin_acc=float((self.state.get("origin") or {}).get("accuracy") or 0.0),
            best_acc=float(self.state.get("best") or 0.0),
        )

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
    ) -> LiveDashboardView | None:
        """Build projection, or ``None`` if ids missing."""
        if not (project_root and session_id and campaign_id and cycle_id):
            return None

        tenant_root = Path(project_root)
        session_root = root_cycle_id(cycle_id)
        family_dir = SessionFamilyDir(cycle_dir_for(tenant_root, campaign_id, session_root))
        session_dir = session_dir_for(tenant_root, session_id)

        resume_from = resolve_resume_state(
            Path(family_dir),
            cycle_dir_for(tenant_root, campaign_id, cycle_id),
            origin_accuracy,
            resumed_from_round,
        )

        return cls(
            family_dir,
            session_dir,
            # `cycle_id` stamps the family ROOT; forks share this file and must match the stamp.
            campaign_id=campaign_id,
            cycle_id=session_root,
            session_id=session_id,
            l1_patience=l1_patience,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
            resume_from=resume_from,
            recorder=recorder,
        )

    # -- State transitions ----------------------------------------------------

    def _set_state(self, name: str) -> None:
        """Liveness transition — keeps ``state`` and ``state_since`` in lockstep."""
        self.state["state"] = name
        self.state["state_since"] = datetime.now(UTC).isoformat()

    def mark_stopped(self, reason: str) -> None:
        """Finalize hook — writes terminal state + reason so dashboard tail-readers see it without index.json."""
        self.state["stop_reason"] = reason
        self._set_state("stopped")
        self._persist()

    def log_fork(self, *, old_cycle_id: str, new_cycle_id: str, from_round: int) -> None:
        """No-op hook — active-cycle identity now lives in `active_session.json`; kept so the
        runner's `_fork_sibling_setup` call site doesn't have to change.
        """
        del old_cycle_id, new_cycle_id, from_round

    # -- Ledger subscription (sole ingress) -----------------------------------
    # Phases → scalars; snapshots → per-round candidate structures. No second dispatch.

    def _handle_phase(self, record: PhaseRecord) -> None:
        if record.phase == "backend" and record.event == "warning":
            # Surface backend retries (429 / 5xx / transport) — retry behaviour itself is unchanged.
            payload = dict(record.payload or {})
            self.state["backend_retry_count"] = int(self.state.get("backend_retry_count") or 0) + 1
            warning = {
                "ts": datetime.now(UTC).isoformat(),
                "kind": payload.get("kind", "unknown"),
                "attempt": payload.get("attempt"),
                "max_attempts": payload.get("max_attempts"),
                "wait_s": payload.get("wait_s"),
                "error_class": payload.get("error_class"),
                "status_code": payload.get("status_code"),
                "final": bool(payload.get("final", False)),
                "query": payload.get("query"),
            }
            recent: list[dict[str, Any]] = list(self.state.get("recent_backend_warnings") or [])
            recent.append(warning)
            self.state["recent_backend_warnings"] = recent[-10:]
            self._persist()
            return

        if record.phase == "round" and record.event == "display":
            payload = record.payload or {}
            round_result = payload.get("round_result")
            l1_stall = int(payload.get("l1_stall_count") or 0)
            if round_result is not None:
                self._absorb_round_complete(round_result.accuracy, l1_stall)
                if self._recorder is not None:
                    self._recorder.set_l1_score(
                        build_l1_score_block(
                            self.state, self._round, self.short_formula_template, round_result
                        )
                    )
                # Append round summary; re-firing the same round (replay / sweep) replaces in place.
                summary_dict = build_round_summary(round_result).model_dump()
                rounds_list: list[dict[str, Any]] = list(self.state.get("rounds") or [])
                rounds_list = [r for r in rounds_list if r.get("round") != round_result.round]
                rounds_list.append(summary_dict)
                rounds_list.sort(key=lambda r: int(r.get("round") or 0))
                self.state["rounds"] = rounds_list
                self._persist()
            return

        # Origin exit: push the live l1_score block to the recorder so `round_0000.json`
        # carries the origin candidate snapshot when audit_trail flushes (dashboard → audit order).
        if (
            record.phase == "origin"
            and record.event == "exit"
            and self._recorder is not None
            and self._round.get("candidates")
        ):
            self._recorder.set_l1_score(
                build_l1_score_block(self.state, self._round, self.short_formula_template)
            )

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
            self._round = {"round": self.state["round"], "candidates": {}}
        self._persist()

    def _handle_snapshot(self, record: SnapshotRecord) -> None:
        ev = record.event
        payload = record.payload or {}
        ci = int(record.candidate_idx or 0)
        ct = int(record.candidate_total or 0)
        qi = int(record.sample_idx or 0)
        qt = int(record.sample_total or 0)
        if ev == "sample_started":
            self._update_sample_markers(ci, ct, qi, qt)
            self.state["current_query_payload"] = (payload.get("query_text") or "")[:120]
            sid = payload.get("sample_id")
            self.state["current_sample_id"] = int(sid) if sid is not None else None
            self._set_state("scoring")
            self._persist()
        elif ev == "sample_scored":
            result = payload.get("result") or {}
            self._update_sample_markers(ci, ct, qi, qt)
            self._absorb_sample_scored(result, last_in_candidate=(qi + 1 >= qt))
            candidate_block.append_sample(self._round, ci, ct, qi, qt, result)
            self._persist()
        elif ev == "candidate_started":
            candidate_block.seed_candidate(
                self._round,
                ci,
                ct,
                payload.get("changes_description") or "",
                payload.get("pp_override"),
            )
            # placeholder seed; next sample_scored / round_complete persists.
        elif ev == "candidate_scored":
            scores = payload.get("scores") or {}
            self._update_current_acc(scores)
            candidate_block.set_candidate_scores(self._round, ci, ct, scores)
            # flushed by next sample_scored or by round_complete.
        elif ev == "p_best_update":
            candidate_block.update_p_best(
                self._round,
                self._core,
                ci,
                ct,
                payload.get("current_id") or "",
                int(payload.get("n_samples") or 0),
                {str(k): float(v) for k, v in (payload.get("p_best") or {}).items()},
            )
            self._persist()
        elif ev == "sample_order_preview":
            order_raw = payload.get("sample_order")
            if isinstance(order_raw, list):
                self.state["hard_sample_order"] = [int(sid) for sid in order_raw]
                self._persist()
        elif ev == "pobb_backfill":
            candidate_block.append_backfill(
                self.state,
                int(record.round or 0),
                ci,
                ct,
                int(payload.get("sample_id") or 0),
                [str(p) for p in (payload.get("prior_ids") or [])],
            )
            self._persist()

    # -- Scalar mutations -----------------------------------------------------

    def _apply_phase(self, event: PhaseEvent, view: dict[str, Any] | None) -> None:
        s = self.state
        if event.round is not None:
            s["round"] = event.round
        # L1_SCORE has no _PHASE_TO_STATE entry — sample_started/scored own its transitions.
        if event.event == "enter" and s.get("state") != "stopped":
            mapped = _PHASE_TO_STATE.get(event.phase)
            if mapped is not None:
                self._set_state(mapped)

        phase, data = event.phase, event.data
        if phase == CampaignPhase.INIT and event.event == "enter":
            # Stamp the formula early — origin scoring runs before INIT:exit; What-If needs a ref.
            if view is not None:
                formula = view.get("composite_fitness_formula")
                if formula is not None:
                    s["composite_fitness_formula"] = formula
                short = view.get("composite_fitness_formula_short")
                if short is not None:
                    self.short_formula_template = short
        elif phase == CampaignPhase.INIT and event.event == "exit":
            cycle = data["state"]
            loop_env = data["env"]
            config = data["config"]
            # Identity stamp (campaign/cycle/session) is set once at construction.
            del loop_env
            s["origin"] = {
                "accuracy": float(cycle.tracking.current_accuracy),
                "samples": len(cycle.tracking.origin_per_sample_results),
            }
            self.patience_max = config.optimization.l1_patience
            s["patience"] = f"0/{self.patience_max}"
            if view is not None:
                s["composite_fitness_formula"] = view.get("composite_fitness_formula")
                self.short_formula_template = view.get("composite_fitness_formula_short")
        elif phase == "scoring_steer" and event.event == "applied":
            # Operator hot-swap — custom formulas render verbatim (no short form).
            new_formula = data.get("formula")
            if new_formula:
                s["composite_fitness_formula"] = new_formula
                self.short_formula_template = None
        elif phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            new_round = int(data.get("round", s["round"]) or 0)
            s["round"] = new_round
            s["degraded_count"] = 0
            # Rewind/fork-in-place clamp: drop rounds this run will overwrite. Sole clamp writer;
            # `round:display` is the sole growth site.
            prior = list(s.get("rounds") or [])
            s["rounds"] = [r for r in prior if int(r.get("round") or 0) < new_round]

        # Mirror origin/best/round into the shared core for LiveDisplay parity.
        apply_phase(self._core, event, view)
        if self._core.best_acc > float(s.get("best") or 0.0):
            s["best"] = self._core.best_acc

    def _update_sample_markers(self, ci: int, ct: int, qi: int, qt: int) -> None:
        s = self.state
        round_num = int(s.get("round") or 0)
        s["candidate"] = f"{candidate_label(round_num, ci)}/{ct}"
        s["query"] = f"{qi + 1}/{qt}"

    def _absorb_sample_scored(self, result: dict[str, Any], *, last_in_candidate: bool) -> None:
        s = self.state
        pd = result.get("pipeline_data") or {}
        query_time = float(pd.get("total_time", 0.0) or 0.0)
        is_cached = bool(result.get("cached", False))

        if result.get("error") or pd.get("error"):
            s["error_count"] += 1
        if is_degraded(result):
            s["degraded_count"] += 1

        s["total_queries_scored"] += 1
        if not is_cached:
            s["total_backend_calls"] += 1
            accumulate_backend_spend(s["spend"], pd)

        s["current_query_payload"] = None
        s["current_sample_id"] = None
        s["last_query_elapsed_s"] = round(query_time, 2)
        self._set_state("between_candidates" if last_in_candidate else "between_samples")

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        """Route an optimizer LLM call into the spend rollup, then persist."""
        apply_token_usage(self.state["spend"], record)
        self._persist()

    def _handle_llm_call_start(self, record: LLMCallStartRecord) -> None:
        """Publish the in-flight optimizer LLM call to `dashboard.json::in_flight` —
        kills the multi-minute blind spot during reasoning-heavy critique calls.
        Cleared by the paired `LLMCallRecord` in `_handle_llm_call`.
        """
        self.state["in_flight"] = {
            "call_id": record.call_id,
            "node": record.node,
            "model": record.model,
            "round": record.round,
            "candidate_idx": record.candidate_idx,
            "started_at_ms": record.started_at_ms,
        }
        self._persist()

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        """Clear the in-flight slot; matches by `call_id` so out-of-order delivery can't clear the wrong call."""
        in_flight = self.state.get("in_flight")
        if (
            isinstance(in_flight, dict)
            and record.call_id
            and in_flight.get("call_id") == record.call_id
        ):
            self.state["in_flight"] = None
            self._persist()

    def _handle_llm_call_progress(self, record: LLMCallProgressRecord) -> None:
        """Heartbeat → re-persist so `wallclock_serialized_at` stays fresh during 30-90s
        optimizer LLM phases that fire no other records. No state mutation, only the timestamp moves.
        """
        del record
        self._persist()

    def _update_current_acc(self, scores: dict[str, Any]) -> None:
        self.state["current_acc"] = round(scores.get("accuracy", 0.0), 4)

    def _absorb_round_complete(self, accuracy: float, l1_stall_count: int) -> None:
        s = self.state
        if accuracy > s["best"]:
            s["best"] = round(accuracy, 4)
        s["patience"] = f"{l1_stall_count}/{self.patience_max}"

    # -- Internal --------------------------------------------------------------

    def _persist(self) -> None:
        # Atomic tmp+rename via write_json — polling readers never see a torn payload.
        # `nodes` mirrors round_NNNN.json::nodes but only for the current round.
        nodes: dict[str, Any] = {}
        if self._recorder is not None:
            nodes.update(self._recorder.snapshot_nodes())
        if self._round.get("candidates"):
            nodes["l1_score"] = build_l1_score_block(
                self.state, self._round, self.short_formula_template
            )
        ordered = {
            k: nodes.pop(k) for k in ("l1_generate", "l1_critique", "l1_score") if k in nodes
        }
        ordered.update(nodes)
        s = self.state
        s["current_round"] = {
            "round": self._round.get("round", 0),
            "nodes": ordered,
            "pobb": build_pobb_block(self._core, self._round),
        }

        s["wallclock_serialized_at"] = datetime.now(UTC).isoformat()
        write_json(self.state_path, s, default=str)


__all__ = ["LiveDashboardView"]
