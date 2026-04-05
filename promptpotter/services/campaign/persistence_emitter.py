"""Campaign persistence emitter — writes campaign_state.json + campaign_output.log.

Auto-created by ``run_optimization()`` so every entry point (notebook, CLI,
web app) produces identical persistent artifacts.  Display and control are
separate layers — see CLAUDE.md § Three-layer I/O architecture.

Produces two files in the session directory:

1. ``campaign_state.json`` — single JSON, overwritten on every update.
   Contains execution state + accumulator arrays (queries within current
   candidate, candidates within current round).  Resets accumulators on
   candidate/round transitions.  Supports ``resume_from`` to carry over
   historical counters across cycles.

   The ``control`` section is written with defaults.  A separate
   ``FileControlSurface`` reads it back for bidirectional control.

2. ``campaign_output.log`` — append-only plain-text log.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.models.phase_event import PhaseEvent
    from promptpotter.services.campaign.results import RoundResult
    from promptpotter.services.stores.session_store import SessionStore

logger = logging.getLogger(__name__)

__all__ = ["CampaignPersistenceEmitter"]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class CampaignPersistenceEmitter:
    """Writes campaign_state.json + campaign_output.log during optimization.

    Instantiated by the optimization loop — entry points never create this
    directly.  All persistent campaign artifacts flow through here.
    """

    def __init__(
        self,
        session_dir: Path,
        *,
        session_store: SessionStore | None = None,
        backend_id: str = "",
        session_id: str = "",
        max_rounds: int = 10,
        l1_patience: int = 3,
        active_nodes: list[str] | None = None,
        excluded_nodes: list[str] | None = None,
        config: dict[str, Any] | None = None,
        resume_from: dict[str, Any] | None = None,
    ) -> None:
        self.state_path = session_dir / "campaign_state.json"
        self.log_path = session_dir / "campaign_output.log"

        # Session store for campaign_log.md writes
        self._session_store = session_store
        self._backend_id = backend_id
        self._session_id = session_id

        cfg = config or {}
        opt = cfg.get("optimization", {})
        r = resume_from or {}

        self._state: dict[str, Any] = {
            # Execution
            "workflow": "optimize",
            "phase": "init",
            "round": 0,
            "max_rounds": max_rounds,
            "candidate": "",
            "query": "",
            "patience": f"0/{l1_patience}",
            "layer": "L1",
            "baseline": r.get("baseline", 0.0),
            "best": r.get("best", 0.0),
            "current_acc": 0.0,
            "cycle_id": None,
            "stop_reason": None,
            "pause_point": None,
            "log_tail": "",
            # Timing
            "elapsed_s": 0.0,
            "round_elapsed_s": 0.0,
            "avg_query_time_s": 0.0,
            "eta_s": 0.0,
            # Pipeline
            "active_nodes": active_nodes or [],
            "excluded_nodes": excluded_nodes or [],
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
            "total_queries_evaluated": r.get("total_queries_evaluated", 0),
            "total_backend_calls": r.get("total_backend_calls", 0),
            # Config
            "model": cfg.get("optimizer_llm", {}).get("model", ""),
            "n_variants": opt.get("n_variants", 5),
            "sample_size": cfg.get("sample_size", 0),
            # Accumulators (reset on transitions)
            "current_queries": [],
            "round_candidates": [],
            "last_round": None,
            # Bidirectional control surface (defaults — FileControlSurface reads back)
            "control": {
                "requested_state": "running",
                "pause_before_l2_eval": opt.get("pause_before_l2_eval", False),
            },
        }

        self._workflow_start = time.monotonic()
        self._round_start = time.monotonic()
        self._query_times: list[float] = []
        self._round_cache_hits = 0
        self._round_queries = 0
        self._round_degraded = 0
        self._improvement_streak = 0
        self._candidates_meta: list[dict] = []

        self._flush()
        # Append-only log — don't truncate on resume
        if not self.log_path.exists():
            self.log_path.write_text("", encoding="utf-8")
        if resume_from:
            self._log(f"\n{'=' * 70}")
            self._log(f"  RESUMED — prior: {r.get('rounds_completed', 0)} rounds, best={r.get('best', 0):.1%}")
            self._log(f"{'=' * 70}\n")

    # -- Resume ----------------------------------------------------------------

    @classmethod
    def load_resume_state(
        cls, session_dir: Path, baseline: float = 0.0,
    ) -> dict[str, Any] | None:
        """Load prior campaign_state.json to build resume_from dict."""
        state_path = session_dir / "campaign_state.json"
        if not state_path.exists():
            return None
        try:
            prior = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return {
            "best": prior.get("best", 0.0),
            "best_round": prior.get("best_round", 0),
            "baseline": baseline,
            "rounds_completed": prior.get("rounds_completed", 0),
            "total_queries_evaluated": prior.get("total_queries_evaluated", 0),
            "total_backend_calls": prior.get("total_backend_calls", 0),
        }

    # -- Callbacks -------------------------------------------------------------

    def on_phase(self, event: PhaseEvent) -> None:
        s = self._state
        s["phase"] = event.phase
        if event.round is not None:
            s["round"] = event.round

        data = event.data

        if event.phase == "init" and event.event == "exit":
            s["cycle_id"] = data.get("cycle_id")
            s["baseline"] = data.get("baseline_accuracy", 0.0)
            patience_max = data.get("patience", s["max_rounds"])
            s["patience"] = f"0/{patience_max}"

        elif event.phase == "l1_generate" and event.event == "enter":
            s["round"] = data.get("round", s["round"])
            self._round_start = time.monotonic()
            self._round_cache_hits = 0
            self._round_queries = 0
            self._round_degraded = 0
            s["degraded_count"] = 0
            s["round_elapsed_s"] = 0.0
            s["current_queries"] = []
            s["round_candidates"] = []

        elif event.phase == "l1_generate" and event.event == "exit":
            self._candidates_meta = data.get("candidates", [])

        elif event.phase == "l1_evaluate" and event.event == "enter":
            s["phase"] = "evaluating"

        elif event.phase in ("refine_context", "modify_plan"):
            s["layer"] = "L2" if event.phase == "refine_context" else "L3"

        self._log(f"--- {event.phase} {event.event} (round {event.round}) ---")
        self._flush()

    def on_query_eval(
        self, ci: int, ct: int, qi: int, qt: int, result: dict,
    ) -> None:
        s = self._state

        # Candidate switch — compact queries, reset
        if s["candidate"] and s["candidate"] != f"C{ci + 1}/{ct}":
            s["current_queries"] = []

        s["candidate"] = f"C{ci + 1}/{ct}"
        s["query"] = f"{qi + 1}/{qt}"

        pd = result.get("pipeline_data") or {}
        query_time = pd.get("total_time", 0.0) or 0.0
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
        s["cache_hit_rate"] = round(
            self._round_cache_hits / self._round_queries, 3,
        ) if self._round_queries else 0.0

        # Quality
        has_error = bool(result.get("error") or _is_error(result))
        if hit:
            current_hits = sum(1 for q in s["current_queries"] if q.get("hit")) + 1
        else:
            current_hits = sum(1 for q in s["current_queries"] if q.get("hit"))
        s["hit_rate"] = round(current_hits / (qi + 1), 3)
        if has_error:
            s["error_count"] += 1
        if pd.get("diagnostics"):
            self._round_degraded += 1
            s["degraded_count"] = self._round_degraded

        # Historical
        s["total_queries_evaluated"] += 1
        s["total_backend_calls"] += 0 if is_cached else 1

        # Accumulate query
        q_entry: dict[str, Any] = {
            "i": qi + 1, "q": (result.get("query") or "")[:50],
            "hit": hit, "time": round(query_time, 1),
            "step": terminated, "cached": is_cached,
        }
        if not hit:
            rank = result.get("ground_truth_rank")
            total = result.get("n_candidates")
            if rank is not None:
                q_entry["rank"] = f"{rank}/{total}" if total else str(rank)
        if has_error:
            q_entry["error"] = True
        s["current_queries"].append(q_entry)

        # Structured log line (no display dependency — audit trail only)
        counter = s["total_queries_evaluated"]
        q_text = (result.get("query") or "")[:45]
        pred = (result.get("prediction") or "")[:35]
        mark = "HIT" if hit else "MISS"
        cache_mark = " CACHED" if is_cached else ""
        line = f"  [{counter:>3d}] {query_time:5.1f}s {mark}{cache_mark} {q_text} -> {pred}"
        s["log_tail"] = line.strip()[:120]
        self._log(line.rstrip())
        self._flush()

    def on_candidate_eval(self, idx: int, total: int, scores: dict) -> None:
        s = self._state
        acc = scores.get("accuracy", 0.0)
        hits = scores.get("hits", 0)
        n = scores.get("total", 0)
        comp = scores.get("composite")

        s["current_acc"] = round(acc, 4)

        # Compact current_queries -> candidate summary
        summary: dict[str, Any] = {
            "id": f"C{idx + 1}", "acc": round(acc, 4),
            "hits": hits, "total": n,
        }
        if comp is not None:
            summary["composite"] = round(comp, 4)
        mutations = self._get_mutations(idx)
        if mutations:
            summary["mutations"] = mutations
        s["round_candidates"].append(summary)

        # Reset for next candidate
        s["current_queries"] = []
        s["hit_rate"] = 0.0

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

        if improved:
            self._improvement_streak += 1
        else:
            self._improvement_streak = 0
        s["improvement_streak"] = self._improvement_streak

        patience_parts = s["patience"].split("/")
        patience_max = patience_parts[1] if len(patience_parts) == 2 else "?"
        s["patience"] = f"{stall_count}/{patience_max}"
        s["layer"] = "L1"
        s["rounds_completed"] = round_result.round
        s["elapsed_s"] = self._elapsed()

        # Compact round_candidates -> last_round
        s["last_round"] = {
            "r": round_result.round, "best": round_result.label,
            "acc": round(acc, 4), "improved": improved,
            "candidates": s["round_candidates"],
        }
        s["round_candidates"] = []
        s["current_queries"] = []

        mark = "IMPROVED" if improved else "no improvement"
        self._log(
            f"\n  >>> Round {round_result.round}: "
            f"{round_result.label} {acc:.1%} — {mark} "
            f"(patience {stall_count}) <<<\n",
        )
        self._flush()

        # Persist round to campaign_log.md via SessionStore
        if self._session_store and self._backend_id and self._session_id:
            _rr_hits = round_result.hits
            _rr_total = round_result.total
            self._session_store.append_log(
                self._backend_id, self._session_id,
                f"## Round {round_result.round} — Evaluated\n"
                f"- {round_result.label}: {acc:.1%} — {mark}\n"
                f"- Hits: {_rr_hits}/{_rr_total}, stall: {stall_count}",
            )

    # -- Lifecycle -------------------------------------------------------------

    def set_stop_reason(self, reason: str | None, pause_point: str | None = None) -> None:
        self._state["stop_reason"] = reason
        self._state["pause_point"] = pause_point
        self._state["workflow"] = "idle"
        self._flush()

    def finalize(self, n_rounds: int, best_accuracy: float, best_round: int,
                 stop_reason: str, cycle_id: str | None = None) -> None:
        """Write final summary to campaign_log.md and set stop reason."""
        self.set_stop_reason(stop_reason)

        if self._session_store and self._backend_id and self._session_id:
            self._session_store.append_log(
                self._backend_id, self._session_id,
                f"## Optimization Complete\n"
                f"- Rounds: {n_rounds}, best: {best_accuracy:.1%} (round {best_round})\n"
                f"- Stop reason: {stop_reason}\n"
                f"- Cycle ID: {cycle_id or 'N/A'}",
            )

    # -- Internal --------------------------------------------------------------

    def _elapsed(self) -> float:
        return round(time.monotonic() - self._workflow_start, 1)

    def _get_mutations(self, idx: int) -> dict:
        if idx < len(self._candidates_meta):
            return self._candidates_meta[idx].get("pipeline_params_override") or {}
        return {}

    def _flush(self) -> None:
        """Write state to disk, preserving user-written control fields."""
        # Read-merge-write: preserve control edits the user may have written
        if self.state_path.exists():
            try:
                on_disk = json.loads(self.state_path.read_text(encoding="utf-8"))
                disk_control = on_disk.get("control", {})
                # User may have changed requested_state or pause_before_l2_eval
                # Merge: disk wins for control fields (user intent), we win for everything else
                merged_control = {**self._state["control"], **disk_control}
                self._state["control"] = merged_control
            except (json.JSONDecodeError, OSError):
                pass
        self.state_path.write_text(
            json.dumps(self._state, indent=2, default=str),
            encoding="utf-8",
        )

    def _log(self, line: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _is_error(result: dict) -> bool:
    pd = result.get("pipeline_data") or {}
    return pd.get("error") is not None or result.get("error") is not None
