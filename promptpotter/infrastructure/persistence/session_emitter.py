"""Campaign session emitter — writes the live dashboard + audit log.

Owns the per-cycle operational artifacts in ``CAMPAIGN_ARTIFACTS`` and
ensures the per-session narrative artifacts in ``SESSION_ARTIFACTS``
exist. ``tests/test_artifact_parity.py`` enforces both sets across entry
points. Instantiated by ``run_optimization()`` so every entry point
produces identical artifacts.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from promptpotter.application.optimization.phases import CampaignPhase
from promptpotter.application.scoring.metrics import is_degraded
from promptpotter.infrastructure.persistence.control import ensure_control_file
from promptpotter.infrastructure.store.base import write_json
from promptpotter.infrastructure.store.campaign_store import campaign_dir_for
from promptpotter.infrastructure.store.session_store import session_dir_for

if TYPE_CHECKING:
    from promptpotter.application.optimization.phases import PhaseEvent
    from promptpotter.application.optimization.results import RoundResult, RunResult

__all__ = [
    "CAMPAIGN_ARTIFACTS",
    "SESSION_ARTIFACTS",
    "CampaignPersistenceEmitter",
    "append_journal",
    "read_claude_notes",
]


# Per-cycle operational artifacts — produced by the emitter under
# ``campaigns/{cycle_id}/``. Consumed by resume, status, UI.
CAMPAIGN_ARTIFACTS = {
    "index.json",  # campaign metadata + trial index + parent_session_id
    "dashboard.json",  # live scalar counters
    "output.log",  # per-query audit log
    "log.md",  # round-by-round markdown summary
    "optimize_result.json",  # final RunResult snapshot
}

# Per-session artifacts — produced under ``sessions/{session_id}/``.
# session.json is owned by SessionStore; the emitter ensures the
# free-form narrative pair + control.json exist for parity from mint.
SESSION_ARTIFACTS = {
    "session.json",  # session metadata
    "journal.md",  # operator narrative (notebook ↔ Claude exchange)
    "notes.md",  # Claude notes
    "control.json",  # HITL control signals
}


def append_journal(session_dir: Path, action: str, body: str = "") -> None:
    """Append a timestamped user note to ``journal.md`` (notebook ↔ Claude narrative channel)."""
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    entry = f"## {ts} \u2014 {action}\n"
    if body:
        entry += f"\n{body}\n"
    with (session_dir / "journal.md").open("a", encoding="utf-8") as f:
        f.write(entry + "\n")


def read_claude_notes(session_dir: Path) -> str:
    """Read ``notes.md`` or return ``''`` if missing/empty."""
    path = session_dir / "notes.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _make_initial_state(
    resume_from: dict[str, Any] | None,
    cycle_id: str | None,
    *,
    max_rounds: int,
    patience_max: int,
    active_nodes: list[str],
    model: str,
    n_variants: int,
    sp_budget_ttest: int,
) -> dict[str, Any]:
    """Build the scalar-only dashboard dict. Resume keys carry across cycles."""
    r = resume_from or {}
    return {
        # Execution
        "workflow": "optimize",
        "phase": "init",
        "round": 0,
        "max_rounds": max_rounds,
        "candidate": "",
        "query": "",
        "patience": f"0/{patience_max}",
        "layer": "L1",
        "baseline": r.get("baseline", 0.0),
        "best": r.get("best", 0.0),
        "current_acc": 0.0,
        "cycle_id": cycle_id,
        "stop_reason": None,
        "pause_point": None,
        # Timing
        "elapsed_s": 0.0,
        "round_elapsed_s": 0.0,
        "avg_query_time_s": 0.0,
        "eta_s": 0.0,
        # Pipeline
        "active_nodes": list(active_nodes),
        "excluded_nodes": [],
        "terminated_at": None,
        "cache_hit_rate": 0.0,
        # Quality
        "hit_rate": 0.0,
        "degraded_count": 0,
        "error_count": 0,
        "best_round": r.get("best_round", 0),
        "improvement_streak": 0,
        # Most recent scoring/escalation event — populated when an
        # escalation check fires, or when a candidate is scored invalid
        # by parse-time validation (Workstream A). Resets on each round.
        "last_scoring_metadata": None,
        # Named evaluator values for the most recent round. Refreshed on
        # every round_complete callback — history lives in events.jsonl.
        "round_evaluators": {},
        # Historical (carried across resumes for display continuity)
        "rounds_completed": r.get("rounds_completed", 0),
        "total_queries_scored": r.get("total_queries_scored", 0),
        "total_backend_calls": r.get("total_backend_calls", 0),
        # Liveness — per-query in-flight markers so a dashboard viewer
        # can tell the loop is alive during a long LLM call.  Set by
        # on_sample_started, cleared by on_sample_scored.
        "query_in_flight": False,
        "query_started_at": None,
        "query_elapsed_s": 0.0,
        "current_query_text": None,
        "last_query_elapsed_s": 0.0,
        "wallclock_serialized_at": None,
        # Config
        "model": model,
        "n_variants": n_variants,
        "sp_budget_ttest": sp_budget_ttest,
    }


@dataclass
class _RoundCounters:
    """Round-scoped counters — reset on each L1_GENERATE enter event."""

    query_times: list[float] = field(default_factory=list)
    cache_hits: int = 0
    queries: int = 0
    degraded: int = 0
    candidate_hits: int = 0

    def reset(self) -> None:
        self.query_times.clear()
        self.cache_hits = 0
        self.queries = 0
        self.degraded = 0
        self.candidate_hits = 0


class CampaignPersistenceEmitter:
    """Writes per-cycle dashboard + audit log under ``campaigns/{cycle_id}/``,
    and ensures per-session narrative + control files exist under
    ``sessions/{session_id}/``. Not an optimizer checkpoint — resume uses
    ``campaigns/{cycle_id}/trials/trial_NNNN.json``; counters here are display
    continuity only."""

    def __init__(
        self,
        campaign_dir: Path,
        session_dir: Path,
        *,
        max_rounds: int,
        l1_patience: int,
        active_nodes: list[str],
        model: str,
        n_variants: int,
        sp_budget_ttest: int,
        pause_before_scoring: bool,
        resume_from: dict[str, Any] | None = None,
        cycle_id: str | None = None,
    ) -> None:
        self.campaign_dir = campaign_dir
        self.state_path = campaign_dir / "dashboard.json"
        self.log_path = campaign_dir / "output.log"
        self.log_md_path = campaign_dir / "log.md"
        self.result_path = campaign_dir / "optimize_result.json"
        self.session_dir = session_dir

        self._patience_max: int = l1_patience
        self._state: dict[str, Any] = _make_initial_state(
            resume_from,
            cycle_id,
            max_rounds=max_rounds,
            patience_max=self._patience_max,
            active_nodes=active_nodes,
            model=model,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
        )
        self._workflow_start = time.monotonic()
        self._round_start = time.monotonic()
        self._query_start: float | None = None
        self._rc = _RoundCounters()

        self._persist()

        # Per-session narrative + control. ``ensure_control_file`` is
        # idempotent; narrative pair is touched so parity holds from mint
        # even if SessionStore.ensure_narrative_files wasn't called yet.
        session_dir.mkdir(parents=True, exist_ok=True)
        ensure_control_file(session_dir, pause_before_scoring=pause_before_scoring)
        (session_dir / "journal.md").touch()
        (session_dir / "notes.md").touch()

        # Append-only log — hold one handle for the emitter's lifetime so
        # 100+ writes/round don't each open() the file.  Closed in ``finalize``.
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh: IO[str] = open(  # noqa: SIM115
            self.log_path, "a", encoding="utf-8", buffering=1
        )
        if resume_from:
            r = resume_from
            self._log_fh.write(
                f"\n{'=' * 70}\n"
                f"  RESUMED — prior: {r.get('rounds_completed', 0)} rounds, "
                f"best={r.get('best', 0):.1%}\n"
                f"{'=' * 70}\n\n"
            )

    @classmethod
    def for_session(
        cls,
        baseline_accuracy: float,
        cycle_id: str | None,
        *,
        project_root: str,
        session_id: str,
        max_rounds: int,
        l1_patience: int,
        active_nodes: list[str],
        model: str,
        n_variants: int,
        sp_budget_ttest: int,
        pause_before_scoring: bool,
        resumed_from_round: int | None = None,
    ) -> CampaignPersistenceEmitter | None:
        """Construct the emitter for a run, or ``None`` if ids are unknown.

        Reads prior ``dashboard.json`` (if present) to carry UI counters
        across resumes — optimizer resume is a separate concern
        via ``LoopState.restore_from_trial``.

        On a mid-cycle rewind (``--from N``), ``resumed_from_round`` is the
        round the runner will execute next; dashboard counters that outran
        the surviving trials are clamped so the UI doesn't show phantom
        rounds.
        """
        if not (project_root and session_id and cycle_id):
            return None

        tenant_root = Path(project_root)
        campaign_dir = campaign_dir_for(tenant_root, cycle_id)
        session_dir = session_dir_for(tenant_root, session_id)

        resume_from: dict[str, Any] | None = None
        prior_state = campaign_dir / "dashboard.json"
        if prior_state.exists():
            try:
                resume_from = json.loads(prior_state.read_text(encoding="utf-8"))
                resume_from["baseline"] = baseline_accuracy
            except (json.JSONDecodeError, OSError):
                resume_from = None

        if resume_from is not None and resumed_from_round is not None:
            # resumed_from_round is the next round to execute; the last
            # completed round on disk is resumed_from_round - 1. Clamp
            # display counters that overshoot that horizon.
            completed = max(resumed_from_round - 1, 0)
            if resume_from.get("rounds_completed", 0) > completed:
                resume_from["rounds_completed"] = completed
            if resume_from.get("best_round", 0) > completed:
                resume_from["best_round"] = 0
                resume_from["best"] = 0.0

        return cls(
            campaign_dir,
            session_dir,
            max_rounds=max_rounds,
            l1_patience=l1_patience,
            active_nodes=active_nodes,
            model=model,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
            pause_before_scoring=pause_before_scoring,
            resume_from=resume_from,
            cycle_id=cycle_id,
        )

    # -- Callbacks -------------------------------------------------------------

    def on_phase(self, event: PhaseEvent) -> None:
        s = self._state
        s["phase"] = event.phase
        if event.round is not None:
            s["round"] = event.round

        phase, data = event.phase, event.data
        if phase == CampaignPhase.INIT and event.event == "exit":
            loop_state = data["state"]
            loop_env = data["env"]
            config = data["config"]
            s["cycle_id"] = loop_env.cycle_id
            s["baseline"] = loop_state.current_accuracy
            self._patience_max = config.optimization.l1_patience
            s["patience"] = f"0/{self._patience_max}"
        elif phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            s["round"] = data.get("round", s["round"])
            self._round_start = time.monotonic()
            self._rc.reset()
            s["degraded_count"] = 0
            s["round_elapsed_s"] = 0.0
            s["last_scoring_metadata"] = None
        elif phase == CampaignPhase.ESCALATION and event.event == "enter":
            s["last_scoring_metadata"] = {
                "kind": "escalation",
                "round": data.get("round"),
                "check_name": data.get("check_name"),
                "target": str(data.get("target")) if data.get("target") is not None else None,
                "degraded_rate": data.get("degraded_rate"),
                "warning_types": data.get("warning_types"),
                "ts": datetime.now(UTC).isoformat(),
            }
        elif phase == CampaignPhase.REFINE_STRATEGY:
            s["layer"] = "L2"
        elif phase == CampaignPhase.MODIFY_PLAN:
            s["layer"] = "L3"

        self._log_fh.write(f"--- {event.phase} {event.event} (round {event.round}) ---\n")
        self._persist()

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pp_override: dict | None,
    ) -> None:
        # Display-only event — the candidate's mutation is already archived
        # on the OptSearchPoint and in the trial JSON. The emitter ignores
        # it so RunListener.on_candidate_started fan-out doesn't need None
        # guards at the callsite.
        return

    def _update_sample_markers(self, ci: int, ct: int, qi: int, qt: int) -> None:
        """Refresh dashboard candidate/query labels; reset per-candidate counter on switch."""
        s = self._state
        candidate_label = f"C{ci + 1}/{ct}"
        if s["candidate"] != candidate_label:
            self._rc.candidate_hits = 0
            s["candidate"] = candidate_label
        s["query"] = f"{qi + 1}/{qt}"

    def on_sample_started(
        self,
        ci: int,
        ct: int,
        qi: int,
        qt: int,
        query_text: str,
    ) -> None:
        s = self._state
        self._update_sample_markers(ci, ct, qi, qt)

        self._query_start = time.monotonic()
        s["query_in_flight"] = True
        s["query_started_at"] = datetime.now(UTC).isoformat()
        s["query_elapsed_s"] = 0.0
        s["current_query_text"] = (query_text or "")[:120]

    def on_sample_scored(
        self,
        ci: int,
        ct: int,
        qi: int,
        qt: int,
        result: dict,
    ) -> None:
        s = self._state
        rc = self._rc
        self._update_sample_markers(ci, ct, qi, qt)

        pd = result.get("pipeline_data") or {}
        query_time = float(pd.get("total_time", 0.0) or 0.0)
        hit = bool(result.get("hit"))
        is_cached = bool(result.get("cached", False))
        terminated = pd.get("terminated_at") or ""

        # Timing
        rc.query_times.append(query_time)
        avg = sum(rc.query_times) / len(rc.query_times)
        s["avg_query_time_s"] = round(avg, 2)
        s["elapsed_s"] = round(time.monotonic() - self._workflow_start, 1)
        s["round_elapsed_s"] = round(time.monotonic() - self._round_start, 1)
        remaining = (qt - qi - 1) + (ct - ci - 1) * qt
        s["eta_s"] = round(remaining * avg, 1)

        # Pipeline
        if terminated:
            s["terminated_at"] = terminated
        rc.queries += 1
        if is_cached:
            rc.cache_hits += 1
        s["cache_hit_rate"] = round(rc.cache_hits / rc.queries, 3)

        # Quality
        if hit:
            rc.candidate_hits += 1
        s["hit_rate"] = round(rc.candidate_hits / (qi + 1), 3)
        if result.get("error") or pd.get("error"):
            s["error_count"] += 1
        if is_degraded(result):
            rc.degraded += 1
            s["degraded_count"] = rc.degraded

        # Historical
        s["total_queries_scored"] += 1
        s["total_backend_calls"] += 0 if is_cached else 1

        # Clear in-flight markers — query landed.
        s["query_in_flight"] = False
        s["query_started_at"] = None
        s["query_elapsed_s"] = 0.0
        s["current_query_text"] = None
        s["last_query_elapsed_s"] = round(query_time, 2)
        self._query_start = None

        # Audit log line — the only live record of per-query detail.
        q_text = (result.get("query") or "")[:45]
        pred = (result.get("prediction") or "")[:35]
        mark = "HIT" if hit else "MISS"
        cache_mark = " CACHED" if is_cached else ""
        self._log_fh.write(
            f"  [{s['total_queries_scored']:>3d}] {query_time:5.1f}s "
            f"{mark}{cache_mark} {q_text} -> {pred}\n"
        )
        self._persist()

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        s = self._state
        acc = scores.get("accuracy", 0.0)
        hits = scores.get("hits", 0)
        n = scores.get("total", 0)
        comp = scores.get("composite")

        s["current_acc"] = round(acc, 4)
        s["hit_rate"] = 0.0
        self._rc.candidate_hits = 0

        # Surface parse-time validation failures to the diagnostic file.
        # Workstream A flags these on the candidate report; users read them
        # from dashboard.last_scoring_metadata without grepping logs.
        if scores.get("invalid") and scores.get("validation_failures"):
            failures = scores["validation_failures"]
            first = failures[0] if failures else {}
            s["last_scoring_metadata"] = {
                "kind": "validation_failure",
                "round": s.get("round"),
                "candidate": f"C{idx + 1}/{total}",
                "axis": first.get("axis"),
                "value": first.get("value"),
                "allowed": first.get("allowed"),
                "reason": first.get("reason"),
                "n_failures": len(failures),
                "ts": datetime.now(UTC).isoformat(),
            }

        comp_str = f"  composite={comp:.3f}" if comp is not None else ""
        invalid_mark = "  INVALID" if scores.get("invalid") else ""
        self._log_fh.write(
            f"  === C{idx + 1}/{total}: {acc:.1%} ({hits}/{n}){comp_str}{invalid_mark} ===\n"
        )
        self._persist()

    def on_round_complete(self, round_result: RoundResult, l1_stall_count: int) -> None:
        s = self._state
        acc = round_result.accuracy
        improved = round_result.improved

        if acc > s["best"]:
            s["best"] = round(acc, 4)
            s["best_round"] = round_result.round

        s["improvement_streak"] = s["improvement_streak"] + 1 if improved else 0
        s["patience"] = f"{l1_stall_count}/{self._patience_max}"
        s["layer"] = "L1"
        s["rounds_completed"] = round_result.round
        s["elapsed_s"] = round(time.monotonic() - self._workflow_start, 1)
        s["round_evaluators"] = dict(round_result.evaluators)

        mark = "IMPROVED" if improved else "no improvement"
        self._log_fh.write(
            f"\n  >>> Round {round_result.round}: "
            f"{round_result.label} {acc:.1%} — {mark} "
            f"(patience {l1_stall_count}) <<<\n\n"
        )
        self._persist()

        self._append_log_md(
            f"## Round {round_result.round} — Evaluated\n"
            f"- {round_result.label}: {acc:.1%} — {mark}\n"
            f"- Hits: {round_result.hits}/{round_result.total}, stall: {l1_stall_count}"
        )

    # -- Lifecycle -------------------------------------------------------------

    def set_stop_reason(self, reason: str | None, pause_point: str | None = None) -> None:
        self._state["stop_reason"] = reason
        self._state["pause_point"] = pause_point
        self._state["workflow"] = "idle"
        self._persist()

    def finalize(
        self,
        n_rounds: int,
        best_accuracy: float,
        best_round: int,
        stop_reason: str,
        cycle_id: str | None = None,
    ) -> None:
        """Write final summary, set stop reason, close log handle."""
        self.set_stop_reason(stop_reason)
        if n_rounds > 0:
            self._append_log_md(
                f"## Optimization Complete\n"
                f"- Rounds: {n_rounds}, best: {best_accuracy:.1%} (round {best_round})\n"
                f"- Stop reason: {stop_reason}\n"
                f"- Cycle ID: {cycle_id or 'N/A'}"
            )
        self._log_fh.close()

    def write_result(self, result: RunResult) -> None:
        """Persist the final ``RunResult`` as ``optimize_result.json``."""
        write_json(self.result_path, result.model_dump(), default=str)

    # -- Internal --------------------------------------------------------------

    def _persist(self) -> None:
        # Wall-clock fields are overwritten here instead of only inside
        # per-query callbacks so a dashboard reader always sees a fresh
        # elapsed_s / round_elapsed_s / query_elapsed_s, even when the
        # loop is blocked inside a long LLM call between callbacks.
        #
        # Direct write (no tempfile+rename) — dashboard.json is a pure
        # display file; all readers already suppress JSONDecodeError on
        # partial reads, and the file is rewritten on the next callback.
        now = time.monotonic()
        s = self._state
        s["elapsed_s"] = round(now - self._workflow_start, 1)
        s["round_elapsed_s"] = round(now - self._round_start, 1)
        if self._query_start is not None:
            s["query_elapsed_s"] = round(now - self._query_start, 2)
        s["wallclock_serialized_at"] = datetime.now(UTC).isoformat()
        self.state_path.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _append_log_md(self, section: str) -> None:
        with self.log_md_path.open("a", encoding="utf-8") as f:
            f.write(section + "\n\n")
