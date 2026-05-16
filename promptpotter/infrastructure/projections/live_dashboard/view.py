"""``LiveDashboardView`` — operator-facing ``dashboard.json`` writer.

Family-root-bound: one ``dashboard.json`` per cycle family, shared
across all forks. The active cycle id is **NOT** stored in this file —
it lives in ``active_session.json`` alone (one writer, one source).
Readers that need to know "what cycle does this data describe?" read
the active pointer; this file describes whatever the active cycle's
loop has produced so far.

The constructor takes :data:`RootCycleDir` so a per-cycle audit block
cannot accidentally land here. A runtime assertion in ``__init__``
rejects any path that contains a ``forks/`` segment.

Single ingress: the projection consumes only via ``on_record`` from the
per-cycle ``CycleEventLog``. The runner emits typed ``PhaseRecord`` /
``SnapshotRecord`` / ``ResumeCheckpointRecord`` records; this class is
one flat router that mutates the ``state`` scalars + the ``_round``
candidate dict in place, then merges both into one ``dashboard.json``
write through ``_persist``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import RootCycleDir
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.results import candidate_label
from promptpotter.domain.run_records import (
    LLMCallRecord,
    LLMCallStartRecord,
    PhaseRecord,
    SnapshotRecord,
    TokenUsageRecord,
)
from promptpotter.infrastructure.projections.base import DerivedView
from promptpotter.infrastructure.projections.live_dashboard.pobb import build_pobb_block
from promptpotter.infrastructure.projections.live_dashboard.score import build_l1_score_block
from promptpotter.infrastructure.projections.live_state import (
    LiveStateCore,
    accumulate_backend_spend,
    apply_p_best_update,
    apply_phase,
    apply_token_usage,
    empty_spend,
    top_n_p_best,
)
from promptpotter.infrastructure.store import (
    campaign_dir_for,
    root_dir_for,
    session_dir_for,
)
from promptpotter.shared.errors import is_degraded

if TYPE_CHECKING:
    from promptpotter.infrastructure.projections.audit_trail import AuditTrailView

logger = logging.getLogger(__name__)


# Phase enter → ``state`` value. The L1_SCORE phase has no entry in this
# table because ``sample_started`` / ``sample_scored`` drive its
# transitions (``scoring`` / ``between_samples`` / ``between_candidates``).
_PHASE_TO_STATE: dict[str, str] = {
    CampaignPhase.INIT: "init",
    CampaignPhase.ORIGIN: "origin",
    CampaignPhase.L1_GENERATE: "l1_generate",
    CampaignPhase.REFINE_STRATEGY: "l2_refining",
    CampaignPhase.MODIFY_PLAN: "l3_replanning",
    CampaignPhase.ESCALATION: "escalation",
}


def _backfill_spend_rates(spend: dict[str, Any]) -> dict[str, Any]:
    """Recompute ``used_usd`` on buckets whose rate didn't resolve at
    write time but does resolve now.

    Optimizer cache hits short-circuit ``emit_token_usage`` (see
    ``llm_call.py``), so a re-run never fires fresh ``TokenUsageRecord``
    events for the loop bucket. The persisted bucket keeps its old
    ``rate_known: false`` even after ``shared/spend.py`` learns the
    model. This pass re-resolves the rate on load — if it now succeeds,
    ``used_usd`` is computed from the accumulated tokens and the bucket
    flips to ``rate_known: true``. Per-event historical rate drift is
    accepted as a tolerable approximation (the alternative is replaying
    the entire ledger).
    """
    from promptpotter.shared.spend import compute_usd

    for b in spend.values():
        if not isinstance(b, dict):
            continue
        if b.get("rate_known") is True:
            continue
        model = b.get("model")
        in_tok = int(b.get("input_tokens") or 0)
        out_tok = int(b.get("output_tokens") or 0)
        if not model or (in_tok == 0 and out_tok == 0):
            continue
        usd = compute_usd(model, in_tok, out_tok)
        if usd is None:
            continue
        b["used_usd"] = round(float(usd), 6)
        b["rate_known"] = True
    spend["total_used_usd"] = round(
        sum(
            float(b.get("used_usd") or 0.0)
            for b in spend.values()
            if isinstance(b, dict)
        ),
        6,
    )
    return spend


def _max_round_on_disk(rounds_dir: Path) -> int:
    """Highest ``round_NNNN.json`` index in ``rounds_dir``, or ``0`` if none.

    Used by ``LiveDashboardView.for_session`` to self-heal a stale
    ``round`` pointer when the prior ``dashboard.json`` was zeroed by an
    earlier re-init but completed-round checkpoints still exist on disk.
    """
    if not rounds_dir.is_dir():
        return 0
    highest = 0
    for path in rounds_dir.glob("round_*.json"):
        stem = path.stem  # ``round_0003``
        suffix = stem[len("round_") :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest


class LiveDashboardView(DerivedView):
    """Per-cycle dashboard writer. Routes ledger records to scalar +
    per-round mutations and persists the merged view to ``dashboard.json``.
    Not an optimizer checkpoint — resume reads ``rounds/round_NNNN.json``,
    counters here are display continuity only.
    """

    def __init__(
        self,
        root_dir: RootCycleDir,
        session_dir: Path,
        *,
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
        resume_from: dict[str, Any] | None = None,
        cycle_id: str | None = None,
        recorder: AuditTrailView | None = None,
    ) -> None:
        # Telemetry binds to the family root (the cycle with no parent_cycle_id).
        # Forks share one continuous dashboard.json; per-fork audit
        # (index.json, log.md, rounds/, .runtime/) stays in each cycle's
        # own dir, written through dynamic ``session.state.cycle_id`` paths.
        #
        # ``cycle_id`` is accepted for symmetry with the prior contract but
        # is no longer persisted into the file. Active-cycle identity comes
        # from ``active_session.json`` (one writer, one source); the file's
        # data describes whatever the active cycle's loop has produced.
        # Branch-tree rendering uses ``walk_cycle_lineage`` at API read
        # time, not a stale field embedded here.
        del cycle_id  # accepted for back-compat call sites; identity lives elsewhere
        root_path = Path(root_dir)
        sibling_kinds = {"forks", "diag", "sweeps"}
        if sibling_kinds & set(root_path.parts):
            raise ValueError(
                f"LiveDashboardView root_dir must be a family root, not a sibling dir; "
                f"got {root_path}"
            )
        self.root_dir = root_path
        self.state_path = root_path / "dashboard.json"
        self.session_dir = session_dir
        self._recorder = recorder
        self.patience_max = l1_patience
        r = resume_from or {}
        self.state: dict[str, Any] = {
            "state": r.get("state", "init"),
            "state_since": datetime.now(UTC).isoformat(),
            "stop_reason": r.get("stop_reason"),
            # Inherit ``round`` from prior dashboard so a re-instantiation
            # (re-run of ``init`` / ``optimize``) doesn't zero the operator-
            # visible round pointer before the first phase event lands and
            # restamps it. Without this, an interrupted cycle that never
            # resumes is permanently displayed at ``round=0`` and the webapp
            # polls a ``rounds/round_0000.json`` that may not exist.
            "round": int(r.get("round") or 0),
            "candidate": "",
            "query": "",
            "patience": f"0/{l1_patience}",
            "origin_accuracy": r.get("origin_accuracy", 0.0),
            "best": r.get("best", 0.0),
            "current_acc": 0.0,
            "composite_fitness_formula": r.get("composite_fitness_formula"),
            "degraded_count": 0,
            "error_count": 0,
            # Backend transport / retry visibility. Bumped by
            # ``_handle_phase`` on ``phase="backend", event="warning"``
            # records emitted from ``measure_sample`` whenever
            # ``BackendClient.run_query`` fires its retry loop. Operator and
            # webapp see retries land in real time; "silent retry then
            # forget" is the failure mode this surfaces against.
            "backend_retry_count": 0,
            "recent_backend_warnings": [],
            "total_queries_scored": r.get("total_queries_scored", 0),
            "total_backend_calls": r.get("total_backend_calls", 0),
            "current_query_payload": None,
            # Sample-id of the row the loop is scoring *right now*; cleared
            # on ``sample_scored``. Lets the webapp dataset table pulse the
            # in-flight row in lockstep with the per-sample dashboard rewrites.
            "current_sample_id": None,
            # Full hardest-first sample_id list from the most recent Rasch
            # fit (refreshed per-candidate in ``score_population``). The
            # webapp dataset table sorts on this when the operator's
            # "sync with live sort" toggle is on. ``None`` until the first
            # sorter fit lands (round 0 / pre-first-candidate fallback).
            "hard_sample_order": None,
            "last_query_elapsed_s": 0.0,
            "wallclock_serialized_at": None,
            "n_variants": n_variants,
            "sp_budget_ttest": sp_budget_ttest,
            "spend": _backfill_spend_rates(r.get("spend") or empty_spend()),
            # Filled by ``_handle_llm_call_start`` while a meta-prompt LLM
            # call is in progress; cleared on the paired ``LLMCallRecord``.
            # Stays ``None`` between calls — explicit so the webapp doesn't
            # have to guess "missing key vs cleared".
            "in_flight": None,
        }
        # Per-candidate value-inlined version of the active short formula;
        # set on INIT:exit, consumed in _build_l1_score_block for each row.
        self.short_formula_template: str | None = None
        self._round: dict[str, Any] = {"round": 0, "candidates": {}}
        # In-memory mirror of the round/origin/best scalars; LiveDisplay
        # holds the same shape so both subscribers share one accumulator.
        self._core = LiveStateCore(
            round_num=int(self.state.get("round") or 0),
            origin_acc=float(self.state.get("origin_accuracy") or 0.0),
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
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
        resumed_from_round: int | None = None,
        recorder: AuditTrailView | None = None,
    ) -> LiveDashboardView | None:
        """Build projection, or ``None`` if ids missing. Carries prior UI counters
        across resumes; optimizer resume is separate (``Cycle.replay_priors``).
        On ``--from N`` rewind, the ``best`` counter past the surviving rounds
        is clamped to avoid a phantom value.

        Telemetry binds to the family root (derived from ``cycle_id`` —
        the prefix before any ``_fork_`` segment). Forks of the same family
        share that root's dashboard.json."""
        if not (project_root and session_id and cycle_id):
            return None

        tenant_root = Path(project_root)
        root_dir = RootCycleDir(root_dir_for(tenant_root, cycle_id))
        session_dir = session_dir_for(tenant_root, session_id)

        resume_from: dict[str, Any] | None = None
        prior_state = Path(root_dir) / "dashboard.json"
        if prior_state.exists():
            try:
                resume_from = json.loads(prior_state.read_text(encoding="utf-8"))
                resume_from["origin_accuracy"] = origin_accuracy
            except (json.JSONDecodeError, OSError):
                resume_from = None

        # ``best`` is the rolling-max composite for the active cycle. The prior
        # ``dashboard.json`` may hold a number from an earlier campaign run
        # against the same family root (cycle_id is content-hashed, so a
        # re-init of the same campaign lands here). Don't surface that number
        # in the UI until the active cycle has produced an on-disk round to
        # back it; otherwise every percentage chip shows a value the loop will
        # never confirm. Self-heal the stale ``round`` pointer in the same
        # pass — both signals depend on ``disk_round``.
        if resume_from is not None:
            # ``campaign_dir_for`` resolves to the active fork's own dir
            # (forks route under ``forks/{cycle_id}``); ``root_dir_for``
            # would point at the family root, whose ``rounds/`` is the
            # parent's checkpoints, not this fork's.
            active_cycle_dir = campaign_dir_for(tenant_root, cycle_id)
            disk_round = _max_round_on_disk(active_cycle_dir / "rounds")
            if disk_round > int(resume_from.get("round") or 0):
                resume_from["round"] = disk_round
            if disk_round == 0 or (
                resumed_from_round is not None and max(resumed_from_round - 1, 0) == 0
            ):
                resume_from["best"] = 0.0

        return cls(
            root_dir,
            session_dir,
            l1_patience=l1_patience,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
            resume_from=resume_from,
            cycle_id=cycle_id,
            recorder=recorder,
        )

    # -- State transitions ----------------------------------------------------

    def _set_state(self, name: str) -> None:
        """Liveness transition — keeps ``state`` and ``state_since`` in lockstep."""
        self.state["state"] = name
        self.state["state_since"] = datetime.now(UTC).isoformat()

    def mark_stopped(self, reason: str) -> None:
        """Cycle finalize hook — terminal state + reason for ``dashboard.json`` readers.

        Called from ``runner._finalize_run`` so an operator tailing
        ``dashboard.json`` after Ctrl+C or natural halt can read the stop
        reason without opening ``index.json``.
        """
        self.state["stop_reason"] = reason
        self._set_state("stopped")
        self._persist()

    def log_fork(self, *, old_cycle_id: str, new_cycle_id: str, from_round: int) -> None:
        """No-op since active-cycle identity moved entirely to ``active_session.json``.

        Kept as a hook for the runner's fork bootstrap call site
        (``application/run_observers.py``) so the call doesn't have to
        change. The structured FORK_CUT decision is still appended to
        the ledger by the runner; the active pointer is retargeted by
        ``_fork_sibling_setup`` via ``save_active_pointer``. The webapp
        reads ``/api/v1/active`` for the live cycle id and walks
        lineage server-side via ``walk_cycle_lineage`` when it needs
        the branch tree.
        """
        del old_cycle_id, new_cycle_id, from_round

    # -- Ledger subscription (sole ingress) -----------------------------------
    #
    # ResumeCheckpointRecord records are persisted to ``ledger.jsonl`` by the
    # runner; this projection only mirrors the live state to
    # ``dashboard.json``. Phases drive scalar updates; snapshots drive
    # per-round candidate structures. Both fan-outs are explicit here —
    # no second dispatch path elsewhere.

    def _handle_phase(self, record: PhaseRecord) -> None:
        if record.phase == "backend" and record.event == "warning":
            # Surface backend transport / 429 / 5xx retries as they fire.
            # The retry behaviour is unchanged (still bounded, still honoring
            # Retry-After); this projection only makes them visible.
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
            recent: list[dict] = list(self.state.get("recent_backend_warnings") or [])
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
                # current_round.round = the just-completed round (= the
                # round_NNNN.json file the webapp should fetch). Webapp reads
                # this value directly with no arithmetic.
                self._round = {"round": round_result.round, "candidates": {}}
                self._persist()
            return

        # Origin completion → push live l1_score block to the audit recorder
        # so ``round_0000.json`` carries origin's candidate snapshot when
        # audit_trail flushes (subscriber order: dashboard → audit).
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
        # L1_GENERATE/enter resets the in-flight round block in lockstep
        # with the degraded_count clear in _apply_phase.
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
            self._append_sample(ci, ct, qi, qt, result)
            self._persist()
        elif ev == "candidate_started":
            self._seed_candidate(
                ci,
                ct,
                payload.get("changes_description") or "",
                payload.get("pp_override"),
            )
            # placeholder seed; next sample_scored / round_complete persists.
        elif ev == "candidate_scored":
            scores = payload.get("scores") or {}
            self._update_current_acc(scores)
            self._set_candidate_scores(ci, ct, scores)
            # flushed by next sample_scored or by round_complete.
        elif ev == "p_best_update":
            self._update_p_best(
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
            self._append_backfill(
                int(record.round or 0),
                ci,
                ct,
                {
                    str(k): [str(s) for s in (v or [])]
                    for k, v in (payload.get("backfilled") or {}).items()
                },
            )
            self._persist()

    # -- Scalar mutations -----------------------------------------------------

    def _apply_phase(self, event: PhaseEvent, view: dict | None) -> None:
        s = self.state
        if event.round is not None:
            s["round"] = event.round
        # L1_SCORE has no _PHASE_TO_STATE entry — sample_started/scored own
        # its transitions; on L1_SCORE:enter the prior state stays.
        if event.event == "enter" and s.get("state") != "stopped":
            mapped = _PHASE_TO_STATE.get(event.phase)
            if mapped is not None:
                self._set_state(mapped)

        phase, data = event.phase, event.data
        if phase == CampaignPhase.INIT and event.event == "enter":
            # Stamp the formula early so What-If has a reference during
            # origin scoring (which runs before INIT:exit).
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
            # No cycle_id write — active-cycle identity lives in
            # active_session.json; this projection only records what
            # the active cycle's loop produced.
            del loop_env
            s["origin"] = cycle.tracking.current_accuracy
            self.patience_max = config.optimization.l1_patience
            s["patience"] = f"0/{self.patience_max}"
            if view is not None:
                s["composite_fitness_formula"] = view.get("composite_fitness_formula")
                self.short_formula_template = view.get("composite_fitness_formula_short")
        elif phase == "scoring_steer" and event.event == "applied":
            # Operator-driven hot-swap; custom formulas render verbatim
            # (no short form / value inlining).
            new_formula = data.get("formula")
            if new_formula:
                s["composite_fitness_formula"] = new_formula
                self.short_formula_template = None
        elif phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            s["round"] = data.get("round", s["round"])
            s["degraded_count"] = 0

        # Mirror origin/best/round into the shared core for LiveDisplay parity.
        apply_phase(self._core, event, view)
        if self._core.best_acc > float(s.get("best") or 0.0):
            s["best"] = self._core.best_acc

    def _update_sample_markers(self, ci: int, ct: int, qi: int, qt: int) -> None:
        s = self.state
        round_num = int(s.get("round") or 0)
        s["candidate"] = f"{candidate_label(round_num, ci)}/{ct}"
        s["query"] = f"{qi + 1}/{qt}"

    def _absorb_sample_scored(self, result: dict, *, last_in_candidate: bool) -> None:
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
        """Publish an in-flight slot on ``dashboard.json::in_flight``.

        Lets the operator (and any AI reading the file) see *which*
        optimizer LLM call is currently in progress and *when* it
        started — addresses the multi-minute blind spot where a
        reasoning-heavy critique call would leave the dashboard frozen.
        Cleared when the paired :class:`LLMCallRecord` arrives via
        :meth:`_handle_llm_call`.
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
        """Clear the in-flight slot when the paired completion record lands.

        Match by ``call_id`` so out-of-order delivery (rare; same ledger,
        same writer) can't clear the wrong call. The actual audit-trail
        write happens in :class:`AuditTrailView`; this projection only
        manages the live ``in_flight`` field.
        """
        in_flight = self.state.get("in_flight")
        if (
            isinstance(in_flight, dict)
            and record.call_id
            and in_flight.get("call_id") == record.call_id
        ):
            self.state["in_flight"] = None
            self._persist()

    def _update_current_acc(self, scores: dict) -> None:
        self.state["current_acc"] = round(scores.get("accuracy", 0.0), 4)

    def _absorb_round_complete(self, accuracy: float, l1_stall_count: int) -> None:
        s = self.state
        if accuracy > s["best"]:
            s["best"] = round(accuracy, 4)
        s["patience"] = f"{l1_stall_count}/{self.patience_max}"

    # -- Per-round candidate mutations ---------------------------------------

    def _candidate(self, idx: int, total: int = 0) -> dict[str, Any]:
        """Lazy-init candidate slot. Sample/score/p_best callbacks may fire
        before candidate_started seeds the slot, so all mutators funnel here.

        ``changes_description`` holds the human-readable mutation text; the
        canonical display ``label`` is composed in ``build_l1_score_block``
        from the slot's round + idx via :func:`candidate_label`.
        """
        return self._round.setdefault("candidates", {}).setdefault(
            idx,
            {
                "idx": idx,
                "total": total,
                "changes_description": "",
                "samples": [],
                "scores": None,
            },
        )

    def _seed_candidate(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pp_override: dict | None,
    ) -> None:
        # Seed so CURRENT shows labelled pending slots before scoring lands.
        entry = self._candidate(idx, total)
        entry["total"] = total
        entry["changes_description"] = changes_description or ""
        entry["pp_override"] = pp_override

    def _append_sample(self, ci: int, ct: int, qi: int, qt: int, result: dict) -> None:
        pd = result.get("pipeline_data") or {}
        query_time = float(pd.get("total_time", 0.0) or 0.0)
        # Tokens may live on result or pd; prefer result, preserve 0 vs None.
        in_tok = result.get("input_tokens")
        out_tok = result.get("output_tokens")
        self._candidate(ci, ct)["samples"].append(
            {
                "qi": qi,
                "qt": qt,
                "sample_id": result.get("sample_id"),
                "hit": bool(result.get("hit")),
                "cached": bool(result.get("cached", False)),
                "query": result.get("query") or "",
                "prediction": result.get("prediction") or "",
                "ground_truth": result.get("ground_truth") or "",
                "time_s": round(query_time, 2),
                "terminated_at": pd.get("terminated_at") or "",
                "input_tokens": pd.get("input_tokens") if in_tok is None else in_tok,
                "output_tokens": pd.get("output_tokens") if out_tok is None else out_tok,
            }
        )

    def _set_candidate_scores(self, idx: int, total: int, scores: dict) -> None:
        # Store the report verbatim — single source of truth shared with
        # ``round_result.candidate_scores`` (same dict instance).
        self._candidate(idx, total)["scores"] = scores

    def _update_p_best(
        self,
        idx: int,
        total: int,
        current_id: str,
        n_samples: int,
        p_best: dict[str, float],
    ) -> None:
        """Merge per-sample P(best) into the candidate slot + top-5 leaderboard.

        Stores each candidate's ``p_best``, signed delta vs prior query, and
        a capped trajectory list. Also publishes the round-wide top-5 sorted
        view at ``self._round["p_best_top"]``.
        """
        cand = self._candidate(idx, total)
        current = float(p_best.get(current_id, 0.0))
        prev = float(cand.get("p_best", current))
        history: list[float] = list(cand.get("p_best_history") or [])
        history.append(current)
        # Cap history at 64 entries — round size rarely exceeds 40.
        if len(history) > 64:
            history = history[-64:]
        cand["p_best"] = current
        cand["p_best_delta"] = current - prev
        cand["p_best_history"] = history
        cand["p_best_n_samples"] = n_samples

        # Mirror the latest snapshot into the shared core so both renderers
        # see the same round-wide P(best) state.
        apply_p_best_update(self._core, current_id, n_samples, p_best)

        # Round-wide leaderboard (top-5 by P(best)).
        self._round["p_best_top"] = [{"id": cid, "p_best": p} for cid, p in top_n_p_best(p_best)]

    def _append_backfill(
        self,
        round_num: int,
        idx: int,
        total: int,
        backfilled: dict[str, list[str]],
    ) -> None:
        """Append a paired-PoBB backfill event to the dashboard's rolling log.

        Webapp + notebook readers see this under ``dashboard.json::backfill_log``.
        Each entry names the round/candidate the backfill ran ahead of and the
        per-prior list of newly-measured sample IDs — so the operator can tell
        when "the leader got measured on the hard samples" vs "everything was
        already cached" (no entry = cached). Capped at 64 entries for size.
        """
        log: list[dict[str, Any]] = list(self.state.get("backfill_log") or [])
        log.append(
            {
                "round": int(round_num),
                "candidate_idx": int(idx),
                "candidate_total": int(total),
                "backfilled": backfilled,
                "n_priors": len(backfilled),
                "n_measurements": sum(len(v) for v in backfilled.values()),
            }
        )
        if len(log) > 64:
            log = log[-64:]
        self.state["backfill_log"] = log

    # -- Internal --------------------------------------------------------------

    def _persist(self) -> None:
        # Direct write — dashboard.json is display-only; readers tolerate
        # partial reads and the file is rewritten on the next callback.

        # Mirror per-round node I/O live, same shape as round_NNNN.json::nodes.
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
        self.state_path.write_text(
            json.dumps(s, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )


__all__ = ["LiveDashboardView"]
