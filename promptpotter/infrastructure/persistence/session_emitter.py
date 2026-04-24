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
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from promptpotter.application.optimization.phases import CampaignPhase
from promptpotter.application.scoring.metrics import is_degraded
from promptpotter.infrastructure.persistence.control import ensure_control_file
from promptpotter.infrastructure.persistence.dashboard_md import (
    fmt_sample_line,
    render_dashboard_md,
    round_summary_from_trial,
)
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


# L2/L3 transition phase → dashboard layer label. Keep in sync with
# ``LayerTransition.phase`` / ``.layer`` in
# ``application/optimization/nodes/layer_transitions.py``.
_PHASE_TO_LAYER: dict[str, str] = {
    CampaignPhase.REFINE_STRATEGY: "L2",
    CampaignPhase.MODIFY_PLAN: "L3",
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
    patience_max: int,
    n_variants: int,
    sp_budget_ttest: int,
) -> dict[str, Any]:
    """Build the scalar-only dashboard dict.

    Only fields that earn their place live here. Setup info (max_rounds,
    model, active_nodes, dataset_count, backend_id) lives as emitter
    attributes; derived displays (elapsed timing, rolling rates, round
    history stats) are computed on read by the ``log.md`` renderer.
    Per-round working state lives on ``_current_round`` + ``_round_history``.
    """
    r = resume_from or {}
    # On resume, mirror ``requested_state`` / ``pause_before_l2_scoring``
    # from prior HITL (or fall back to flat top-level keys from the
    # pre-nested schema), but always reset ``stop_reason`` and
    # ``pause_point`` — a fresh run hasn't stopped and hasn't paused.
    prior_hitl = r.get("hitl") or {}
    return {
        # Execution markers
        "phase": "init",
        "round": 0,
        "candidate": "",
        "query": "",
        "patience": f"0/{patience_max}",
        "layer": "L1",
        "baseline": r.get("baseline", 0.0),
        "best": r.get("best", 0.0),
        "current_acc": 0.0,
        "cycle_id": cycle_id,
        # Cumulative counters
        "degraded_count": 0,
        "error_count": 0,
        "total_queries_scored": r.get("total_queries_scored", 0),
        "total_backend_calls": r.get("total_backend_calls", 0),
        # Liveness markers — set on sample_started, cleared on sample_scored
        "query_in_flight": False,
        "query_started_at": None,
        "current_query_payload": None,
        "last_query_elapsed_s": 0.0,
        "wallclock_serialized_at": None,
        # Config
        "n_variants": n_variants,
        "sp_budget_ttest": sp_budget_ttest,
        # HITL — gathered (see ``_snapshot_hitl``). ``requested_state`` and
        # ``pause_before_l2_scoring`` mirror ``control.json``; ``pause_point``
        # and ``stop_reason`` are set by the loop when it halts.
        "hitl": {
            "requested_state": prior_hitl.get("requested_state", "running"),
            "pause_before_l2_scoring": bool(prior_hitl.get("pause_before_l2_scoring", False)),
            "pause_point": None,
            "stop_reason": None,
        },
    }


def _round_summary_from_round_result(
    rr: RoundResult, current_round: dict[str, Any]
) -> dict[str, Any]:
    """Build the dashboard round_summary shape from an in-memory RoundResult
    + the live per-candidate accumulator. Shared schema with
    ``dashboard_md.round_summary_from_trial`` so sections render the same
    whether the data is in-memory or reloaded from disk."""
    candidates = current_round.get("candidates") or {}
    leaderboard: list[dict[str, Any]] = []
    # Prefer authoritative per-candidate scores from RoundResult when present;
    # fall back to the live accumulator for runs where the loop doesn't emit
    # candidate_scores yet.
    scored = list(rr.candidate_scores or [])
    if scored:
        for idx, cs in enumerate(scored):
            cand = candidates.get(idx, {})
            leaderboard.append(
                {
                    "idx": idx,
                    "accuracy": float(cs.get("accuracy", 0.0)),
                    "hits": int(cs.get("hits", 0)),
                    "total": int(cs.get("total", 0)),
                    "label": cs.get("changes_description")
                    or cand.get("label")
                    or cs.get("label")
                    or "",
                    "is_winner": bool(cs.get("is_winner", False)),
                    "eliminated_at": cs.get("eliminated_at"),
                }
            )
    else:
        for idx in sorted(candidates.keys()):
            c = candidates[idx]
            s = c.get("scores") or {}
            leaderboard.append(
                {
                    "idx": idx,
                    "accuracy": float(s.get("accuracy", 0.0)),
                    "hits": int(s.get("hits", 0)),
                    "total": int(s.get("total", 0)),
                    "label": c.get("label") or "",
                    "is_winner": False,
                    "eliminated_at": None,
                }
            )

    return {
        "round": rr.round,
        "accuracy": float(rr.accuracy),
        "hits": int(rr.hits),
        "total": int(rr.total),
        "winner_label": rr.label or "",
        "improved": bool(rr.improved),
        "leaderboard": leaderboard,
    }


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
        dataset_count: int | None = None,
        backend_id: str | None = None,
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
            patience_max=self._patience_max,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
        )
        self._workflow_start = time.monotonic()
        self._round_start = time.monotonic()
        self._query_start: float | None = None

        # Setup info — doesn't change over the run, lives on the emitter so
        # log.md header can show it without bloating dashboard.json.
        self._max_rounds = max_rounds
        self._model = model
        self._active_nodes = list(active_nodes)
        self._dataset_count = dataset_count
        self._backend_id = backend_id

        # Sliding-window dashboard state. ``_current_round`` accumulates
        # per-candidate detail for the in-flight round; ``_round_history``
        # holds completed round summaries (oldest-first). On resume, history
        # is rebuilt from ``trials/`` so the EARLIER section is accurate.
        self._current_round: dict[str, Any] = {"round": 0, "candidates": {}}
        self._round_history: list[dict[str, Any]] = []
        self._rehydrate_history_from_disk()

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
        dataset_count: int | None = None,
        backend_id: str | None = None,
    ) -> CampaignPersistenceEmitter | None:
        """Construct the emitter for a run, or ``None`` if ids are unknown.

        Reads prior ``dashboard.json`` (if present) to carry UI counters
        across resumes — optimizer resume is a separate concern
        via ``Cycle.restore_from_trial``.

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

        # On a mid-cycle rewind, ``best`` might reference a round that got
        # invalidated. The _rehydrate_history_from_disk step on the emitter
        # repopulates _round_history from surviving trials/ — recomputing
        # best from that is the source of truth, so clamp it here.
        if resume_from is not None and resumed_from_round is not None:
            completed = max(resumed_from_round - 1, 0)
            if completed == 0:
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
            dataset_count=dataset_count,
            backend_id=backend_id,
        )

    # -- Callbacks -------------------------------------------------------------

    def on_phase(self, event: PhaseEvent) -> None:
        s = self._state
        s["phase"] = event.phase
        if event.round is not None:
            s["round"] = event.round

        phase, data = event.phase, event.data
        if phase == CampaignPhase.INIT and event.event == "exit":
            cycle = data["state"]
            loop_env = data["env"]
            config = data["config"]
            s["cycle_id"] = loop_env.cycle_id
            s["baseline"] = cycle.current_accuracy
            self._patience_max = config.optimization.l1_patience
            s["patience"] = f"0/{self._patience_max}"
        elif phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            s["round"] = data.get("round", s["round"])
            self._round_start = time.monotonic()
            s["degraded_count"] = 0
            # Fresh round — clear the in-flight per-candidate accumulator
            # so the CURRENT section starts empty. History was already
            # populated in on_round_complete for the previous round.
            self._current_round = {"round": s["round"], "candidates": {}}
        elif phase in _PHASE_TO_LAYER:
            s["layer"] = _PHASE_TO_LAYER[phase]

        self._log_fh.write(f"--- {event.phase} {event.event} (round {event.round}) ---\n")
        self._persist()

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pp_override: dict | None,
    ) -> None:
        # Seed the candidate entry so the CURRENT section shows labelled
        # pending slots before scoring begins. ``on_sample_scored`` /
        # ``on_candidate_scored`` lazy-init for the same key, so runs that
        # skip this callback still render correctly.
        entry = self._current_round.setdefault("candidates", {}).setdefault(idx, {})
        entry["idx"] = idx
        entry["total"] = total
        entry["label"] = changes_description or ""
        entry["pp_override"] = pp_override
        entry.setdefault("samples", [])
        entry.setdefault("scores", None)
        self._persist()

    def _update_sample_markers(self, ci: int, ct: int, qi: int, qt: int) -> None:
        """Refresh dashboard candidate/query labels."""
        s = self._state
        s["candidate"] = f"C{ci + 1}/{ct}"
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
        s["current_query_payload"] = (query_text or "")[:120]

    def on_sample_scored(
        self,
        ci: int,
        ct: int,
        qi: int,
        qt: int,
        result: dict,
    ) -> None:
        s = self._state
        self._update_sample_markers(ci, ct, qi, qt)

        pd = result.get("pipeline_data") or {}
        query_time = float(pd.get("total_time", 0.0) or 0.0)
        hit = bool(result.get("hit"))
        is_cached = bool(result.get("cached", False))
        terminated = pd.get("terminated_at") or ""

        if result.get("error") or pd.get("error"):
            s["error_count"] += 1
        if is_degraded(result):
            s["degraded_count"] += 1

        s["total_queries_scored"] += 1
        if not is_cached:
            s["total_backend_calls"] += 1

        # Clear in-flight markers — query landed.
        s["query_in_flight"] = False
        s["query_started_at"] = None
        s["current_query_payload"] = None
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

        # Accumulate for the dashboard PER-QUERY DETAIL section. Lazy-init
        # the candidate entry — tests/older paths may skip on_candidate_started.
        cand = self._current_round.setdefault("candidates", {}).setdefault(
            ci, {"idx": ci, "total": ct, "label": "", "samples": [], "scores": None}
        )
        sample_id = result.get("sample_id")
        # Token counts may be nested on the sample result or the pipeline
        # envelope depending on the backend; emit ``None`` when absent.
        input_tokens = result.get("input_tokens")
        if input_tokens is None:
            input_tokens = pd.get("input_tokens")
        output_tokens = result.get("output_tokens")
        if output_tokens is None:
            output_tokens = pd.get("output_tokens")
        cand["samples"].append(
            {
                "qi": qi,
                "qt": qt,
                "sample_id": sample_id,
                "hit": hit,
                "cached": is_cached,
                "query": result.get("query") or "",
                "prediction": result.get("prediction") or "",
                "ground_truth": result.get("ground_truth") or "",
                "time_s": round(query_time, 2),
                "terminated_at": terminated,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
        )
        self._persist()

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        s = self._state
        acc = scores.get("accuracy", 0.0)
        hits = scores.get("hits", 0)
        n = scores.get("total", 0)
        comp = scores.get("composite")

        s["current_acc"] = round(acc, 4)

        comp_str = f"  composite={comp:.3f}" if comp is not None else ""
        invalid_mark = "  INVALID" if scores.get("invalid") else ""
        self._log_fh.write(
            f"  === C{idx + 1}/{total}: {acc:.1%} ({hits}/{n}){comp_str}{invalid_mark} ===\n"
        )

        # Finalize the candidate's slot in the dashboard accumulator.
        cand = self._current_round.setdefault("candidates", {}).setdefault(
            idx, {"idx": idx, "total": total, "label": "", "samples": [], "scores": None}
        )
        cand["scores"] = {
            "accuracy": round(acc, 4),
            "composite": round(comp, 4) if comp is not None else None,
            "hits": int(hits),
            "total": int(n),
            "invalid": bool(scores.get("invalid", False)),
            "validation_failures": scores.get("validation_failures") or [],
        }
        self._persist()

    def on_round_complete(self, round_result: RoundResult, l1_stall_count: int) -> None:
        s = self._state
        acc = round_result.accuracy
        improved = round_result.improved

        if acc > s["best"]:
            s["best"] = round(acc, 4)

        s["patience"] = f"{l1_stall_count}/{self._patience_max}"
        s["layer"] = "L1"

        mark = "IMPROVED" if improved else "no improvement"
        self._log_fh.write(
            f"\n  >>> Round {round_result.round}: "
            f"{round_result.label} {acc:.1%} — {mark} "
            f"(patience {l1_stall_count}) <<<\n\n"
        )

        # Deposit the scoring-phase block + HITL snapshot onto the active
        # round recorder before runner.py calls ``flush()``. The recorder
        # already holds the L1/L2/L3 LLM actions captured during
        # ``execute_round`` via ``pipeline.llm_call()`` — so flushing now
        # writes one consolidated ``rounds/round_NNNN.json``.
        self._deposit_round_recorder_state(round_result)

        # Flush the in-flight round into history. Use the RoundResult's
        # candidate_scores as authoritative leaderboard data; fall back to
        # the accumulated per-candidate state if candidate_scores is empty.
        self._round_history.append(
            _round_summary_from_round_result(round_result, self._current_round)
        )
        self._current_round = {"round": round_result.round + 1, "candidates": {}}
        self._persist()

    def _deposit_round_recorder_state(self, round_result: RoundResult) -> None:
        """Build the ``l1_score`` node block and HITL snapshot, hand them
        to the active ``RoundRecorder`` for inclusion in ``round_NNNN.json``.
        """
        from promptpotter.application.optimization.pipeline import get_round_recorder

        recorder = get_round_recorder()
        if recorder is None:
            return
        recorder.set_l1_score(self._build_l1_score_block(round_result))
        recorder.set_hitl(self._snapshot_hitl())

    def _build_l1_score_block(self, round_result: RoundResult) -> dict[str, Any]:
        """Project in-memory per-candidate state + RoundResult.candidate_scores
        into the ``l1_score`` node block.

        Input side mirrors the candidates L1 generate produced (idx, label,
        ``changes_description``, ``pp_override``). Output side lists the
        samples that landed and the finalized stats — the same data that
        already drives the PER-QUERY DETAIL section of ``log.md``.
        """
        candidates = self._current_round.get("candidates") or {}
        authoritative = list(round_result.candidate_scores or [])

        input_candidates: list[dict[str, Any]] = []
        output_candidates: list[dict[str, Any]] = []
        for idx in sorted(candidates.keys()):
            cand = candidates[idx]
            cs = authoritative[idx] if idx < len(authoritative) else {}
            label = cand.get("label") or cs.get("changes_description") or cs.get("label") or ""
            input_candidates.append(
                {
                    "idx": idx,
                    "label": label,
                    "changes_description": cs.get("changes_description") or label,
                    "pp_override": cand.get("pp_override"),
                }
            )
            scores = cand.get("scores") or {}
            stats = {
                "accuracy": scores.get("accuracy"),
                "composite": scores.get("composite"),
                "hits": scores.get("hits"),
                "total": scores.get("total"),
                "invalid": scores.get("invalid", False),
                "validation_failures": scores.get("validation_failures") or [],
                "is_winner": bool(cs.get("is_winner", False)),
                "eliminated_at": cs.get("eliminated_at"),
            }
            output_candidates.append(
                {
                    "idx": idx,
                    "stats": stats,
                    "samples": list(cand.get("samples") or []),
                }
            )

        return {
            "input": {"candidates": input_candidates},
            "output": {"candidates": output_candidates},
        }

    # -- Lifecycle -------------------------------------------------------------

    def set_stop_reason(self, reason: str | None, pause_point: str | None = None) -> None:
        hitl = self._state.setdefault("hitl", {})
        hitl["stop_reason"] = reason
        hitl["pause_point"] = pause_point
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
        self._log_fh.close()

    def write_result(self, result: RunResult) -> None:
        """Persist the final ``RunResult`` as ``optimize_result.json``."""
        write_json(self.result_path, result.model_dump(), default=str)

    # -- Internal --------------------------------------------------------------

    def _snapshot_hitl(self) -> dict[str, Any]:
        """Build the consolidated HITL block — control signals + halt state.

        ``requested_state`` and ``pause_before_l2_scoring`` mirror
        ``sessions/{session_id}/control.json`` (session-level HITL intent).
        ``pause_point`` and ``stop_reason`` come from the in-memory state
        (set by the loop when it actually halts). Silently falls back to
        last-known values if ``control.json`` is missing or malformed —
        the control surface is bidirectional and may be briefly absent
        during a hand-edit.
        """
        existing = self._state.get("hitl") or {}
        snapshot = {
            "requested_state": existing.get("requested_state", "running"),
            "pause_before_l2_scoring": bool(existing.get("pause_before_l2_scoring", False)),
            "pause_point": existing.get("pause_point"),
            "stop_reason": existing.get("stop_reason"),
        }
        control_path = self.session_dir / "control.json"
        try:
            control = json.loads(control_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return snapshot
        snapshot["requested_state"] = control.get("requested_state", snapshot["requested_state"])
        snapshot["pause_before_l2_scoring"] = bool(
            control.get("pause_before_l2_scoring", snapshot["pause_before_l2_scoring"])
        )
        return snapshot

    def _persist(self) -> None:
        # Direct write (no tempfile+rename) — dashboard.json is a pure
        # display file; all readers already suppress JSONDecodeError on
        # partial reads, and the file is rewritten on the next callback.
        self._state["hitl"] = self._snapshot_hitl()
        self._state["current_round"] = self._snapshot_current_round()
        self._state["wallclock_serialized_at"] = datetime.now(UTC).isoformat()
        self.state_path.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        self._write_dashboard_md()

    def _snapshot_current_round(self) -> dict[str, Any]:
        """Mirror the per-round node I/O live into ``dashboard.json``.

        Same shape as ``rounds/round_NNNN.json::nodes`` — ``l1_generate``
        and ``l1_critique`` are pulled from the active ``RoundRecorder``
        as soon as each fires; ``l1_score`` is synthesized from the
        in-memory candidate/sample accumulator every time a sample lands
        so the file reflects scoring progress without waiting for the
        round to complete. L2/L3 blocks appear when the round escalates.
        """
        from promptpotter.application.optimization.pipeline import get_round_recorder

        round_idx = self._current_round.get("round", self._state.get("round", 0))
        nodes: dict[str, Any] = {}
        recorder = get_round_recorder()
        if recorder is not None:
            nodes.update(recorder.snapshot_nodes())
        if self._current_round.get("candidates"):
            nodes["l1_score"] = self._build_live_l1_score_block()

        ordered: dict[str, Any] = {}
        for preferred in ("l1_generate", "l1_critique"):
            if preferred in nodes:
                ordered[preferred] = nodes.pop(preferred)
        if "l1_score" in nodes:
            ordered["l1_score"] = nodes.pop("l1_score")
        ordered.update(nodes)
        return {"round": round_idx, "nodes": ordered}

    def _build_live_l1_score_block(self) -> dict[str, Any]:
        """Live variant of ``_build_l1_score_block`` — no authoritative
        ``candidate_scores`` yet (round not complete), so ``is_winner``
        and ``eliminated_at`` are omitted from in-flight stats.
        """
        candidates = self._current_round.get("candidates") or {}
        input_candidates: list[dict[str, Any]] = []
        output_candidates: list[dict[str, Any]] = []
        for idx in sorted(candidates.keys()):
            cand = candidates[idx]
            label = cand.get("label") or ""
            input_candidates.append(
                {
                    "idx": idx,
                    "label": label,
                    "changes_description": label,
                    "pp_override": cand.get("pp_override"),
                }
            )
            scores = cand.get("scores") or {}
            stats = {
                "accuracy": scores.get("accuracy"),
                "composite": scores.get("composite"),
                "hits": scores.get("hits"),
                "total": scores.get("total"),
                "invalid": scores.get("invalid", False),
                "validation_failures": scores.get("validation_failures") or [],
            }
            output_candidates.append(
                {
                    "idx": idx,
                    "stats": stats,
                    # ``dashboard.json`` uses the compact CLI one-liner per sample —
                    # full structured sample dicts (with ~2 kB BBEH query strings)
                    # only go to ``rounds/round_NNNN.json`` via ``_build_l1_score_block``.
                    "samples": [fmt_sample_line(s) for s in (cand.get("samples") or [])],
                }
            )
        return {
            "input": {"candidates": input_candidates},
            "output": {"candidates": output_candidates},
        }

    def _write_dashboard_md(self) -> None:
        """Overwrite ``log.md`` with the current sliding-window view."""
        content = render_dashboard_md(
            self.campaign_dir,
            self._state,
            self._current_round,
            self._round_history,
            max_rounds=self._max_rounds,
            model=self._model,
            active_nodes=self._active_nodes,
            dataset_count=self._dataset_count,
            backend_id=self._backend_id,
            elapsed_s=time.monotonic() - self._workflow_start,
        )
        self.log_md_path.write_text(content, encoding="utf-8")

    def _rehydrate_history_from_disk(self) -> None:
        """On resume, rebuild ``_round_history`` from ``trials/`` so the
        EARLIER + LAST COMPLETED sections survive a restart."""
        trials_dir = self.campaign_dir / "trials"
        if not trials_dir.exists():
            return
        for path in sorted(trials_dir.glob("trial_*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            self._round_history.append(round_summary_from_trial(data))
