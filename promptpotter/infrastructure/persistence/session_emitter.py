"""Campaign session emitter — writes the live dashboard + audit log.

``CampaignPersistenceEmitter`` owns two session artifacts:

- ``campaign_state.json`` — the live UI dashboard, atomically rewritten on
  every phase / sample / candidate / round event.  Consumed by
  ``show-status``, the webapp, and the parity test.
- ``campaign_output.log`` — append-only query-by-query audit trail.

It also seeds ``campaign_control.json`` (the HITL control surface) with
defaults on init, but never touches it afterwards — see ``control.py``.

Auto-created by ``run_optimization()`` so every entry point (notebook, CLI,
web app) produces identical persistent artifacts.  Display is a separate
layer — see CLAUDE.md § Three-layer I/O architecture.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, TypedDict, cast

from promptpotter.infrastructure.persistence.control import CONTROL_FILENAME
from promptpotter.infrastructure.persistence.state import CampaignPhase
from promptpotter.infrastructure.store.base import write_json

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import LoopConfig
    from promptpotter.infrastructure.persistence.state import PhaseEvent, RoundResult

logger = logging.getLogger(__name__)

__all__ = ["CAMPAIGN_SESSION_ARTIFACTS", "CampaignPersistenceEmitter", "CampaignStateSchema"]


CAMPAIGN_SESSION_ARTIFACTS = {
    "campaign_state.json",
    "campaign_control.json",
    "campaign_output.log",
    "campaign_log.md",
    "session.json",
}


class CampaignStateSchema(TypedDict):
    """On-disk shape of ``campaign_state.json`` — scalar-only, emitter-owned."""

    # Execution
    workflow: str
    phase: str
    round: int
    max_rounds: int
    candidate: str
    query: str
    patience: str
    layer: str
    baseline: float
    best: float
    current_acc: float
    cycle_id: str | None
    stop_reason: str | None
    pause_point: str | None
    # Timing
    elapsed_s: float
    round_elapsed_s: float
    avg_query_time_s: float
    eta_s: float
    # Pipeline
    active_nodes: list[str]
    excluded_nodes: list[str]
    terminated_at: str | None
    cache_hit_rate: float
    # Quality
    hit_rate: float
    degraded_count: int
    error_count: int
    best_round: int
    improvement_streak: int
    # Historical
    rounds_completed: int
    total_queries_scored: int
    total_backend_calls: int
    # Config
    model: str
    n_variants: int
    sp_budget_ttest: int


class CampaignPersistenceEmitter:
    """Writes ``campaign_state.json`` + ``campaign_output.log`` during optimization.

    Instantiated by the optimization loop — entry points never create this
    directly.  All persistent campaign dashboard artifacts flow through here.

    **Not an optimizer checkpoint.**  Optimizer resume uses trial files in
    the campaign store (``campaigns/{cycle_id}/trial_NNNN.json``) via
    ``_restore_from_checkpoint()`` in ``cycle_init.py``.  The counters here
    (``rounds_completed``, ``best``, etc.) are for display continuity only —
    see ``load_resume_state()``.

    See ``docs/architecture/overview.md § Persistence Architecture`` for the
    full two-tier layout and resume flow.
    """

    @classmethod
    def for_session(
        cls,
        config: LoopConfig,
        baseline_accuracy: float,
        cycle_id: str | None,
    ) -> CampaignPersistenceEmitter | None:
        """Build the emitter for an optimization-loop session, or ``None``.

        Returns ``None`` when ``project_root``/``backend_id``/``session_id``
        are missing — those three fields are the ingredients for a session
        directory.

        UI counter continuity on resume is cosmetic: we read the prior
        ``campaign_state.json`` off disk (if it exists) and hand the dict
        to ``__init__``'s ``resume_from`` parameter after overriding the
        baseline to the current value.  Optimizer resume is a separate
        concern that flows through ``LoopState.restore_from_trial()``.
        """
        if not (config.project_root and config.backend_id and config.session_id):
            return None

        session_dir = Path(config.project_root) / config.backend_id / "sessions" / config.session_id

        resume_from: dict[str, Any] | None = None
        prior_state = session_dir / "campaign_state.json"
        if prior_state.exists():
            try:
                resume_from = json.loads(prior_state.read_text(encoding="utf-8"))
                resume_from["baseline"] = baseline_accuracy
            except (json.JSONDecodeError, OSError):
                resume_from = None

        return cls(session_dir, config, resume_from=resume_from, cycle_id=cycle_id)

    def __init__(
        self,
        session_dir: Path,
        config: LoopConfig,
        *,
        resume_from: dict[str, Any] | None = None,
        cycle_id: str | None = None,
    ) -> None:
        self.state_path = session_dir / "campaign_state.json"
        self.control_path = session_dir / CONTROL_FILENAME
        self.log_path = session_dir / "campaign_output.log"
        self.log_md_path = session_dir / "campaign_log.md"

        self._patience_max: int = config.l1_patience

        r = resume_from or {}

        self._state: CampaignStateSchema = {
            # Execution
            "workflow": "optimize",
            "phase": "init",
            "round": 0,
            "max_rounds": config.max_rounds or 999,
            "candidate": "",
            "query": "",
            "patience": f"0/{self._patience_max}",
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
            "active_nodes": list(config.pipeline_schema.active_steps)
            if config.pipeline_schema
            else [],
            "excluded_nodes": [],
            "terminated_at": None,
            "cache_hit_rate": 0.0,
            # Quality
            "hit_rate": 0.0,
            "degraded_count": 0,
            "error_count": 0,
            "best_round": r.get("best_round", 0),
            "improvement_streak": 0,
            # Historical (carried over across cycles)
            "rounds_completed": r.get("rounds_completed", 0),
            "total_queries_scored": r.get("total_queries_scored", 0),
            "total_backend_calls": r.get("total_backend_calls", 0),
            # Config
            "model": config.model or "",
            "n_variants": config.n_variants,
            "sp_budget_ttest": config.sp_budget_ttest,
        }

        self._workflow_start = time.monotonic()
        self._round_start = time.monotonic()
        self._query_times: list[float] = []
        self._round_cache_hits = 0
        self._round_queries = 0
        self._round_degraded = 0
        self._candidate_hits = 0

        self._flush()
        # Seed the control surface.  Only write if missing so a user edit
        # made between runs (or a lingering pause from a previous session)
        # survives the next ``init``.
        if not self.control_path.exists():
            write_json(
                self.control_path,
                {
                    "requested_state": "running",
                    "pause_before_l2_scoring": config.pause_before_scoring,
                },
            )

        # Append-only log — hold one handle for the emitter's lifetime so
        # 100+ writes/round don't each open() the file.  Closed in
        # ``finalize()``.  ``buffering=1`` = line-buffered so tails are fresh.
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh: IO[str] | None = open(  # noqa: SIM115
            self.log_path, "a", encoding="utf-8", buffering=1
        )
        if resume_from:
            self._log(f"\n{'=' * 70}")
            self._log(
                f"  RESUMED — prior: {r.get('rounds_completed', 0)} rounds, best={r.get('best', 0):.1%}"
            )
            self._log(f"{'=' * 70}\n")

    # -- Callbacks -------------------------------------------------------------

    def on_phase(self, event: PhaseEvent) -> None:
        s = self._state
        s["phase"] = event.phase
        if event.round is not None:
            s["round"] = event.round

        phase, data = event.phase, event.data
        if phase == CampaignPhase.INIT and event.event == "exit":
            loop_state = data["state"]
            config = data["config"]
            s["cycle_id"] = loop_state.cycle_id
            s["baseline"] = loop_state.current_accuracy
            self._patience_max = config.l1_patience
            s["patience"] = f"0/{self._patience_max}"
        elif phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            s["round"] = data.get("round", s["round"])
            self._round_start = time.monotonic()
            self._round_cache_hits = 0
            self._round_queries = 0
            self._round_degraded = 0
            self._candidate_hits = 0
            s["degraded_count"] = 0
            s["round_elapsed_s"] = 0.0
        elif phase == CampaignPhase.REFINE_STRATEGY:
            s["layer"] = "L2"
        elif phase == CampaignPhase.MODIFY_PLAN:
            s["layer"] = "L3"

        self._log(f"--- {event.phase} {event.event} (round {event.round}) ---")
        self._flush()

    def on_sample_scored(
        self,
        ci: int,
        ct: int,
        qi: int,
        qt: int,
        result: dict,
    ) -> None:
        s = self._state
        candidate_label = f"C{ci + 1}/{ct}"
        if s["candidate"] != candidate_label:
            self._candidate_hits = 0
            s["candidate"] = candidate_label
        s["query"] = f"{qi + 1}/{qt}"

        pd = result.get("pipeline_data") or {}
        query_time = cast(float, pd.get("total_time", 0.0) or 0.0)
        hit = bool(result.get("hit"))
        is_cached = result.get("cached", False)
        terminated = pd.get("terminated_at") or ""

        # Timing
        self._query_times.append(query_time)
        avg = sum(self._query_times) / len(self._query_times)
        s["avg_query_time_s"] = round(avg, 2)
        s["elapsed_s"] = self._elapsed()
        s["round_elapsed_s"] = round(time.monotonic() - self._round_start, 1)
        remaining = (qt - qi - 1) + (ct - ci - 1) * qt
        s["eta_s"] = round(remaining * avg, 1)

        # Pipeline
        if terminated:
            s["terminated_at"] = terminated
        self._round_queries += 1
        if is_cached:
            self._round_cache_hits += 1
        s["cache_hit_rate"] = round(self._round_cache_hits / self._round_queries, 3)

        # Quality
        if hit:
            self._candidate_hits += 1
        s["hit_rate"] = round(self._candidate_hits / (qi + 1), 3)
        if result.get("error") or pd.get("error"):
            s["error_count"] += 1
        if pd.get("diagnostics"):
            self._round_degraded += 1
            s["degraded_count"] = self._round_degraded

        # Historical
        s["total_queries_scored"] += 1
        s["total_backend_calls"] += 0 if is_cached else 1

        # Audit log line — the only live record of per-query detail.
        q_text = (result.get("query") or "")[:45]
        pred = (result.get("prediction") or "")[:35]
        mark = "HIT" if hit else "MISS"
        cache_mark = " CACHED" if is_cached else ""
        self._log(
            f"  [{s['total_queries_scored']:>3d}] {query_time:5.1f}s "
            f"{mark}{cache_mark} {q_text} -> {pred}"
        )
        self._flush()

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        s = self._state
        acc = scores.get("accuracy", 0.0)
        hits = scores.get("hits", 0)
        n = scores.get("total", 0)
        comp = scores.get("composite")

        s["current_acc"] = round(acc, 4)
        s["hit_rate"] = 0.0
        self._candidate_hits = 0

        comp_str = f"  composite={comp:.3f}" if comp is not None else ""
        self._log(f"  === C{idx + 1}/{total}: {acc:.1%} ({hits}/{n}){comp_str} ===")
        self._flush()

    def on_round_complete(self, round_result: RoundResult, stall_count: int) -> None:
        s = self._state
        acc = round_result.accuracy
        improved = round_result.improved

        if acc > s["best"]:
            s["best"] = round(acc, 4)
            s["best_round"] = round_result.round

        s["improvement_streak"] = s["improvement_streak"] + 1 if improved else 0
        s["patience"] = f"{stall_count}/{self._patience_max}"
        s["layer"] = "L1"
        s["rounds_completed"] = round_result.round
        s["elapsed_s"] = self._elapsed()

        mark = "IMPROVED" if improved else "no improvement"
        self._log(
            f"\n  >>> Round {round_result.round}: "
            f"{round_result.label} {acc:.1%} — {mark} "
            f"(patience {stall_count}) <<<\n",
        )
        self._flush()

        self._append_log_md(
            f"## Round {round_result.round} — Evaluated\n"
            f"- {round_result.label}: {acc:.1%} — {mark}\n"
            f"- Hits: {round_result.hits}/{round_result.total}, stall: {stall_count}"
        )

    # -- Lifecycle -------------------------------------------------------------

    def set_stop_reason(self, reason: str | None, pause_point: str | None = None) -> None:
        self._state["stop_reason"] = reason
        self._state["pause_point"] = pause_point
        self._state["workflow"] = "idle"
        self._flush()

    def finalize(
        self,
        n_rounds: int,
        best_accuracy: float,
        best_round: int,
        stop_reason: str,
        cycle_id: str | None = None,
    ) -> None:
        """Write final summary to campaign_log.md, set stop reason, close log handle."""
        self.set_stop_reason(stop_reason)
        self._append_log_md(
            f"## Optimization Complete\n"
            f"- Rounds: {n_rounds}, best: {best_accuracy:.1%} (round {best_round})\n"
            f"- Stop reason: {stop_reason}\n"
            f"- Cycle ID: {cycle_id or 'N/A'}"
        )
        self._close_log()

    # -- Internal --------------------------------------------------------------

    def _elapsed(self) -> float:
        return round(time.monotonic() - self._workflow_start, 1)

    def _flush(self) -> None:
        """Atomically write state to disk.

        The control surface lives in its own file (``campaign_control.json``),
        so the emitter writes ``_state`` straight through without any
        read-merge-write dance.
        """
        write_json(self.state_path, self._state, default=str)

    def _log(self, line: str) -> None:
        if self._log_fh is None:
            # Already finalized — fall back to append so stray late writes
            # from interrupt handlers don't silently drop.
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            return
        self._log_fh.write(line + "\n")

    def _close_log(self) -> None:
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None

    def _append_log_md(self, section: str) -> None:
        self.log_md_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_md_path.open("a", encoding="utf-8") as f:
            f.write(section + "\n\n")
