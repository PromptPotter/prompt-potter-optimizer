"""Campaign persistence — emitter, control surface, and round recorder.

Consolidates all file-based campaign I/O:
- CampaignPersistenceEmitter — writes campaign_state.json + campaign_output.log
- CampaignControlReader — reads control signals from campaign_state.json
- RoundRecorder — writes round_NNN.json action traces

Auto-created by ``run_optimization()`` so every entry point (notebook, CLI,
web app) produces identical persistent artifacts.  Display is a separate
layer — see CLAUDE.md § Three-layer I/O architecture.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from promptpotter.services.campaign.state import CampaignPhase

if TYPE_CHECKING:
    from promptpotter.services.campaign.config import LoopConfig
    from promptpotter.services.campaign.state import PhaseEvent, RoundResult
    from promptpotter.services.store.stores import SessionStore

logger = logging.getLogger(__name__)

__all__ = ["CampaignControlReader", "CampaignPersistenceEmitter", "RoundRecorder"]


class CampaignPersistenceEmitter:
    """Writes ``campaign_state.json`` + ``campaign_output.log`` during optimization.

    Instantiated by the optimization loop — entry points never create this
    directly.  All persistent campaign artifacts flow through here.

    ``campaign_state.json`` is the **live UI dashboard + bidirectional HITL
    control surface**.  It is exposed directly to the webapp and to
    ``show-status``.  Users may edit the ``control`` section at checkpoints
    (pause / resume / stop).

    **This file is NOT an optimizer checkpoint.**  Optimizer resume uses trial
    files in the campaign store (``campaigns/{cycle_id}/trial_NNNN.json``)
    via ``_restore_from_checkpoint()`` in ``cycle_init.py``.  The counters
    here (``rounds_completed``, ``best``, etc.) are for display continuity
    only — see ``load_resume_state()``.

    Schema sections::

        execution   — phase, round, candidate, query, patience, layer, cycle_id
        timing      — elapsed_s, round_elapsed_s, avg_query_time_s, eta_s
        pipeline    — active_nodes, excluded_nodes, terminated_at, cache_hit_rate
        quality     — hit_rate, degraded_count, error_count
        best        — best, best_round, improvement_streak
        historical  — rounds_completed, total_queries_evaluated, total_backend_calls
        config      — model, n_variants, sp_budget_ttest
        accumulators— current_queries (reset per candidate), round_candidates
                      (reset per round), last_round
        control     — bidirectional: requested_state, pause_before_l2_scoring

    See ``docs/architecture.md § Persistence Architecture`` for the full
    two-tier layout and resume flow.
    """

    def __init__(
        self,
        session_dir: Path,
        config: LoopConfig,
        *,
        session_store: SessionStore | None = None,
        resume_from: dict[str, Any] | None = None,
        cycle_id: str | None = None,
    ) -> None:
        self.state_path = session_dir / "campaign_state.json"
        self.log_path = session_dir / "campaign_output.log"

        # Session store for campaign_log.md writes
        self._session_store = session_store
        self._backend_id = config.backend_id
        self._session_id = config.session_id

        r = resume_from or {}

        self._state: dict[str, Any] = {
            # Execution
            "workflow": "optimize",
            "phase": "init",
            "round": 0,
            "max_rounds": config.max_rounds or 999,
            "candidate": "",
            "query": "",
            "patience": f"0/{config.l1_patience}",
            "layer": "L1",
            "baseline": r.get("baseline", 0.0),
            "best": r.get("best", 0.0),
            "current_acc": 0.0,
            "cycle_id": cycle_id,
            "stop_reason": None,
            "pause_point": None,
            "log_tail": "",
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
            "total_queries_evaluated": r.get("total_queries_evaluated", 0),
            "total_backend_calls": r.get("total_backend_calls", 0),
            # Config
            "model": config.model or "",
            "n_variants": config.n_variants,
            "sp_budget_ttest": config.sp_budget_ttest,
            # Accumulators (carried over on resume, reset on transitions)
            "current_queries": [],
            "round_candidates": r.get("round_candidates", []),
            "last_round": r.get("last_round"),
            # Bidirectional control surface (defaults — CampaignControlReader reads back)
            "control": {
                "requested_state": "running",
                "pause_before_l2_scoring": config.pause_before_scoring,
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
            self._log(
                f"  RESUMED — prior: {r.get('rounds_completed', 0)} rounds, best={r.get('best', 0):.1%}"
            )
            self._log(f"{'=' * 70}\n")

    # -- Resume ----------------------------------------------------------------

    @classmethod
    def load_resume_state(
        cls,
        session_dir: Path,
        baseline: float = 0.0,
    ) -> dict[str, Any] | None:
        """Load UI counter state from prior ``campaign_state.json`` for display continuity.

        This is NOT optimizer resume — optimizer state is restored from trial
        checkpoint files in ``_restore_from_checkpoint()`` (``cycle_init.py``).
        These fields are purely cosmetic counters so the dashboard doesn't
        reset to zero on resume.
        """
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
            "round_candidates": prior.get("round_candidates", []),
            "last_round": prior.get("last_round"),
        }

    # -- Callbacks -------------------------------------------------------------

    # Phase dispatch table — maps (phase, event) to handler methods.
    _PHASE_DISPATCH: ClassVar[dict[tuple[str, str], str]] = {
        (CampaignPhase.INIT, "exit"): "_on_init_exit",
        (CampaignPhase.L1_GENERATE, "enter"): "_on_generate_enter",
        (CampaignPhase.L1_GENERATE, "exit"): "_on_generate_exit",
        (CampaignPhase.L1_SCORE, "enter"): "_on_evaluate_enter",
    }

    def on_phase(self, event: PhaseEvent) -> None:
        s = self._state
        s["phase"] = event.phase
        if event.round is not None:
            s["round"] = event.round

        handler_name = self._PHASE_DISPATCH.get((event.phase, event.event))
        if handler_name:
            getattr(self, handler_name)(event.data)

        # L2/L3 layer label (both enter and exit)
        if event.phase in (CampaignPhase.REFINE_STRATEGY, CampaignPhase.MODIFY_PLAN):
            s["layer"] = "L2" if event.phase == CampaignPhase.REFINE_STRATEGY else "L3"

        self._log(f"--- {event.phase} {event.event} (round {event.round}) ---")
        self._flush()

    def _on_init_exit(self, data: dict) -> None:
        s = self._state
        s["cycle_id"] = data.get("cycle_id")
        s["baseline"] = data.get("baseline_accuracy", 0.0)
        patience_max = data.get("patience", s["max_rounds"])
        s["patience"] = f"0/{patience_max}"

    def _on_generate_enter(self, data: dict) -> None:
        s = self._state
        s["round"] = data.get("round", s["round"])
        self._round_start = time.monotonic()
        self._round_cache_hits = 0
        self._round_queries = 0
        self._round_degraded = 0
        s["degraded_count"] = 0
        s["round_elapsed_s"] = 0.0
        s["current_queries"] = []
        s["round_candidates"] = []

    def _on_generate_exit(self, data: dict) -> None:
        self._candidates_meta = data.get("candidates", [])

    def _on_evaluate_enter(self, data: dict) -> None:
        self._state["phase"] = "evaluating"

    def on_sample_scored(
        self,
        ci: int,
        ct: int,
        qi: int,
        qt: int,
        result: dict,
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
        s["cache_hit_rate"] = (
            round(
                self._round_cache_hits / self._round_queries,
                3,
            )
            if self._round_queries
            else 0.0
        )

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
            "i": qi + 1,
            "q": (result.get("query") or "")[:50],
            "hit": hit,
            "time": round(query_time, 1),
            "step": terminated,
            "cached": is_cached,
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

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        s = self._state
        acc = scores.get("accuracy", 0.0)
        hits = scores.get("hits", 0)
        n = scores.get("total", 0)
        comp = scores.get("composite")

        s["current_acc"] = round(acc, 4)

        # Compact current_queries -> candidate summary
        summary: dict[str, Any] = {
            "id": f"C{idx + 1}",
            "acc": round(acc, 4),
            "hits": hits,
            "total": n,
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
            "r": round_result.round,
            "best": round_result.label,
            "acc": round(acc, 4),
            "improved": improved,
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
                self._backend_id,
                self._session_id,
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

    def finalize(
        self,
        n_rounds: int,
        best_accuracy: float,
        best_round: int,
        stop_reason: str,
        cycle_id: str | None = None,
    ) -> None:
        """Write final summary to campaign_log.md and set stop reason."""
        self.set_stop_reason(stop_reason)

        if self._session_store and self._backend_id and self._session_id:
            self._session_store.append_log(
                self._backend_id,
                self._session_id,
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
                # User may have changed requested_state or pause_before_l2_scoring
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


class CampaignControlReader:
    """Reads control signals from ``campaign_state.json`` at checkpoints.

    The persistence emitter writes the file; this class only reads the
    ``control`` section back.  Returns ``"pause"`` or ``"stop"`` when the
    user requested it, else ``None``.
    """

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def check(self, checkpoint_name: str) -> str | None:
        """Read control section. Returns action or None.

        Called at natural checkpoints (after_round, before_l2, before_l3).
        """
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

        control = data.get("control", {})
        requested = control.get("requested_state", "running")

        if requested == "resume":
            # Acknowledge resume: overwrite control to running
            data["control"]["requested_state"] = "running"
            self.state_path.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
            logger.info("Control: resume acknowledged at %s", checkpoint_name)
            return None

        if requested == "pause":
            logger.info("Control: pause requested at %s", checkpoint_name)
            return "pause"

        if requested == "stop":
            logger.info("Control: stop requested at %s", checkpoint_name)
            return "stop"

        # Check L2-specific pause
        if checkpoint_name == "before_l2_scoring" and control.get("pause_before_l2_scoring"):
            logger.info("Control: pause_before_l2_scoring active at %s", checkpoint_name)
            return "pause"

        return None


class RoundRecorder:
    """Accumulates actions within a round, writes ``round_NNN.json`` on flush."""

    def __init__(self, rounds_dir: Path) -> None:
        self.rounds_dir = rounds_dir
        self._current_round: int = 0
        self._actions: list[dict[str, Any]] = []
        self._started_at: str = ""
        self._has_escalation = False

    def begin_round(self, round_num: int) -> None:
        """Start recording a new round. Flushes any pending actions."""
        if self._actions:
            logger.warning(
                "RoundRecorder: unflushed actions from round %d discarded",
                self._current_round,
            )
        self._current_round = round_num
        self._actions = []
        self._started_at = datetime.now(UTC).isoformat()
        self._has_escalation = False

    def add_action(self, action: dict[str, Any]) -> None:
        """Append an action to the current round's trace."""
        action.setdefault("timestamp", datetime.now(UTC).isoformat())
        if action.get("type") in ("l2_refine_strategy", "l3_modify_plan"):
            self._has_escalation = True
        self._actions.append(action)

    def flush(self, state_snapshot: dict[str, Any] | None = None) -> Path | None:
        """Write the round file and reset. Returns the written path."""
        if not self._actions:
            return None

        self.rounds_dir.mkdir(parents=True, exist_ok=True)

        suffix = ""
        if self._has_escalation:
            for a in self._actions:
                if a.get("type") == "l2_refine_strategy":
                    suffix = "_l2"
                    break
                if a.get("type") == "l3_modify_plan":
                    suffix = "_l3"
                    break

        filename = f"round_{self._current_round:03d}{suffix}.json"
        path = self.rounds_dir / filename

        record = {
            "round": self._current_round,
            "started_at": self._started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "actions": self._actions,
        }
        if state_snapshot:
            record["state_snapshot"] = state_snapshot

        path.write_text(
            json.dumps(record, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.debug(
            "Round %d recorded: %d actions → %s",
            self._current_round,
            len(self._actions),
            filename,
        )

        self._actions = []
        self._has_escalation = False
        return path

    def record_round_outcome(
        self,
        round_result: Any,
        state: Any,
    ) -> Path | None:
        """Record eval + decision actions for a normal round, then flush.

        Encapsulates the serialization format so the optimization loop
        doesn't need to know the recorder's schema.
        """
        self.add_action(
            {
                "type": "l1_score",
                "n_candidates": round_result.candidates_scored,
                "n_queries": round_result.total,
                "candidates": round_result.candidate_scores,
            }
        )
        self.add_action(
            {
                "type": "decision",
                "winner": round_result.label,
                "accuracy": round_result.accuracy,
                "composite": round_result.composite,
                "improved": round_result.improved,
                "stall_count": state.stall_count,
                "winner_prompt_fields": round_result.prompt_fields,
                "winner_pipeline_params": round_result.pipeline_params,
            }
        )
        return self.flush(
            state_snapshot={
                "opt_search_point_id": state.opt_sp.id,
                "l2_directive": state.opt_sp.l2_directive or "",
                "escalation_counters": {
                    "l2_stall": state.escalation.l2_stall_count,
                    "l3_stall": state.escalation.l3_stall_count,
                    "l2_round": state.escalation.l2_round,
                    "l3_round": state.escalation.l3_round,
                },
            }
        )
