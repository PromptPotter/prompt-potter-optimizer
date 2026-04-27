"""Campaign session emitter — live dashboard.json + output.log writer."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from promptpotter.domain.phases import CampaignPhase
from promptpotter.infrastructure.persistence.control import ensure_control_file
from promptpotter.infrastructure.store.campaign_store import campaign_dir_for
from promptpotter.infrastructure.store.session_store import session_dir_for
from promptpotter.shared.errors import is_degraded

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.domain.phases import PhaseEvent

# Injected callable returning the active RoundRecorder or None — keeps
# infrastructure from importing upward into application/optimization.
RecorderProvider = Callable[[], Any]

__all__ = [
    "CAMPAIGN_ARTIFACTS",
    "SESSION_ARTIFACTS",
    "CampaignPersistenceEmitter",
    "append_journal",
    "read_claude_notes",
]


# Per-cycle artifacts under ``campaigns/{cycle_id}/``.
CAMPAIGN_ARTIFACTS = {
    "index.json",
    "dashboard.json",
    "output.log",
    "log.md",
    "phase_events.jsonl",
}

# Per-session artifacts under ``sessions/{session_id}/``. ``session.json``
# is owned by SessionStore; the emitter ensures the rest exist from mint.
SESSION_ARTIFACTS = {
    "session.json",
    "journal.md",
    "notes.md",
    "control.json",
}


# Keep in sync with ``LayerTransition.phase`` / ``.layer``.
_PHASE_TO_LAYER: dict[str, str] = {
    CampaignPhase.REFINE_STRATEGY: "L2",
    CampaignPhase.MODIFY_PLAN: "L3",
}


def append_journal(session_dir: Path, action: str, body: str = "") -> None:
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    entry = f"## {ts} \u2014 {action}\n"
    if body:
        entry += f"\n{body}\n"
    with (session_dir / "journal.md").open("a", encoding="utf-8") as f:
        f.write(entry + "\n")


def read_claude_notes(session_dir: Path) -> str:
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
    """Build the scalar-only dashboard dict (no setup/derived fields)."""
    r = resume_from or {}
    # Mirror prior ``requested_state`` but reset stop/pause — fresh run.
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
        "n_variants": n_variants,
        "sp_budget_ttest": sp_budget_ttest,
        # ``requested_state`` mirrors control.json; pause_point/stop_reason
        # are written by the loop on halt. See ``_snapshot_hitl``.
        "hitl": {
            "requested_state": prior_hitl.get("requested_state", "running"),
            "pause_point": None,
            "stop_reason": None,
        },
    }


# Per-query terminator badge for the compact ``fmt_sample_line`` rendering;
# unmapped nodes render as the first two characters of the node name.
_NODE_BADGES: dict[str, str] = {
    "llm_only": "ai",
    "llm_ranking": "ai",
    "entity_profiling": "ai",
    "cache_lookup": "cache",
    "fuzzy_matching": "fz",
    "token_matching": "tk",
    "web_search": "ws",
}


def _trim(text: str, n: int) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def fmt_sample_line(s: dict[str, Any]) -> str:
    """One compact line per query for ``dashboard.json::current_round.nodes
    .l1_score.output.candidates[].samples`` — keeps the live dashboard
    scannable instead of bloating it with full ~2 kB query strings."""
    qi = int(s.get("qi", 0))
    hit = bool(s.get("hit"))
    cached = bool(s.get("cached"))
    time_s = float(s.get("time_s") or 0.0)
    badge = _NODE_BADGES.get(s.get("terminated_at") or "", (s.get("terminated_at") or "?")[:2])
    cache_icon = "📖" if cached else " "
    mark = "HIT " if hit else "MISS"
    query = _trim(s.get("query") or "", 42)
    pred = _trim(s.get("prediction") or "", 28)
    gt = _trim(s.get("ground_truth") or "", 20)
    in_tok = s.get("input_tokens")
    out_tok = s.get("output_tokens")
    tok_seg = ""
    if in_tok is not None or out_tok is not None:
        tok_seg = (
            f" io={in_tok if in_tok is not None else '-'}/{out_tok if out_tok is not None else '-'}"
        )
    return (
        f"  {time_s:4.1f}s #{qi:03d} {mark} [{badge}]{cache_icon}"
        f"{tok_seg} -> '{pred}' gt:'{gt}' q:'{query}'"
    )


class CampaignPersistenceEmitter:
    """Per-cycle dashboard + audit log writer; ensures per-session narrative
    + control files. Not an optimizer checkpoint — resume reads
    ``trials/trial_NNNN.json``, counters here are display continuity only."""

    def __init__(
        self,
        campaign_dir: Path,
        session_dir: Path,
        *,
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
        resume_from: dict[str, Any] | None = None,
        cycle_id: str | None = None,
        recorder_provider: RecorderProvider | None = None,
    ) -> None:
        self.campaign_dir = campaign_dir
        self.state_path = campaign_dir / "dashboard.json"
        self.log_path = campaign_dir / "output.log"
        self.phase_events_path = campaign_dir / "phase_events.jsonl"
        self.session_dir = session_dir
        self._recorder_provider: RecorderProvider = recorder_provider or (lambda: None)
        # Phase-view ctx accumulator. Initialised empty here; ``RunListener``
        # overwrites this attribute at construction with its own shared dict
        # so emitter + display read the same state machine.
        self._phase_ctx: dict[str, Any] = {}
        self._phase_event_seq: int = 0

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

        self._current_round: dict[str, Any] = {"round": 0, "candidates": {}}

        self._persist()

        session_dir.mkdir(parents=True, exist_ok=True)
        ensure_control_file(session_dir)
        (session_dir / "journal.md").touch()
        (session_dir / "notes.md").touch()

        # One log handle for the emitter's lifetime; closed in ``finalize``.
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh: IO[str] = open(  # noqa: SIM115
            self.log_path, "a", encoding="utf-8", buffering=1
        )
        # Touch for artifact parity — append-only, preserved on resume.
        self.phase_events_path.touch()
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
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
        resumed_from_round: int | None = None,
        recorder_provider: RecorderProvider | None = None,
    ) -> CampaignPersistenceEmitter | None:
        """Build emitter, or ``None`` if ids missing. Carries prior UI counters
        across resumes; optimizer resume is separate (``Cycle.restore_from_trial``).
        On ``--from N`` rewind, the ``best`` counter past the surviving trials
        is clamped to avoid a phantom value."""
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
            completed = max(resumed_from_round - 1, 0)
            if completed == 0:
                resume_from["best"] = 0.0

        return cls(
            campaign_dir,
            session_dir,
            l1_patience=l1_patience,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
            resume_from=resume_from,
            cycle_id=cycle_id,
            recorder_provider=recorder_provider,
        )

    # -- Callbacks -------------------------------------------------------------

    def on_phase(self, event: PhaseEvent, view: dict | None) -> None:
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
            # Fresh round — clear in-flight accumulator (history already populated).
            self._current_round = {"round": s["round"], "candidates": {}}
        elif phase in _PHASE_TO_LAYER:
            s["layer"] = _PHASE_TO_LAYER[phase]

        self._log_fh.write(f"--- {event.phase} {event.event} (round {event.round}) ---\n")
        self._persist_phase_event(event, view)
        self._persist()

    def _persist_phase_event(self, event: PhaseEvent, view: dict | None) -> None:
        """Append a phase_events.jsonl line; no-op when view is None."""
        if view is None:
            return
        record = {
            "seq": self._phase_event_seq,
            "phase": event.phase,
            "event": event.event,
            "round": event.round,
            "ts": event.timestamp,
            "view": view,
        }
        self._phase_event_seq += 1
        with self.phase_events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pp_override: dict | None,
    ) -> None:
        # Seed the entry so CURRENT shows labelled pending slots; sample/score
        # callbacks lazy-init the same key for paths that skip this callback.
        entry = self._current_round.setdefault("candidates", {}).setdefault(idx, {})
        entry["idx"] = idx
        entry["total"] = total
        entry["label"] = changes_description or ""
        entry["pp_override"] = pp_override
        entry.setdefault("samples", [])
        entry.setdefault("scores", None)
        self._persist()

    def _update_sample_markers(self, ci: int, ct: int, qi: int, qt: int) -> None:
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

        s["query_in_flight"] = False
        s["query_started_at"] = None
        s["current_query_payload"] = None
        s["last_query_elapsed_s"] = round(query_time, 2)
        self._query_start = None

        q_text = (result.get("query") or "")[:45]
        pred = (result.get("prediction") or "")[:35]
        mark = "HIT" if hit else "MISS"
        cache_mark = " CACHED" if is_cached else ""
        self._log_fh.write(
            f"  [{s['total_queries_scored']:>3d}] {query_time:5.1f}s "
            f"{mark}{cache_mark} {q_text} -> {pred}\n"
        )

        # Lazy-init candidate entry — older paths may skip on_candidate_started.
        cand = self._current_round.setdefault("candidates", {}).setdefault(
            ci, {"idx": ci, "total": ct, "label": "", "samples": [], "scores": None}
        )
        # Tokens may live on result or pd; prefer result, preserve 0 vs None.
        in_tok = result.get("input_tokens")
        out_tok = result.get("output_tokens")
        cand["samples"].append(
            {
                "qi": qi,
                "qt": qt,
                "sample_id": result.get("sample_id"),
                "hit": hit,
                "cached": is_cached,
                "query": result.get("query") or "",
                "prediction": result.get("prediction") or "",
                "ground_truth": result.get("ground_truth") or "",
                "time_s": round(query_time, 2),
                "terminated_at": terminated,
                "input_tokens": pd.get("input_tokens") if in_tok is None else in_tok,
                "output_tokens": pd.get("output_tokens") if out_tok is None else out_tok,
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

        # Store the report verbatim — single source of truth shared with
        # ``round_result.candidate_scores`` (same dict instance from
        # ``_fire``). ``_build_l1_score_block`` projects to the
        # dashboard/round_NNNN shape without a second copy of the keys.
        cand = self._current_round.setdefault("candidates", {}).setdefault(
            idx, {"idx": idx, "total": total, "label": "", "samples": [], "scores": None}
        )
        cand["scores"] = scores
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

        # Deposit l1_score block + HITL onto the active recorder before
        # runner.py flush() — produces one consolidated rounds/round_NNNN.json.
        self._deposit_round_recorder_state(round_result)

        self._current_round = {"round": round_result.round + 1, "candidates": {}}
        self._persist()

    def _deposit_round_recorder_state(self, round_result: RoundResult) -> None:
        """Hand l1_score block + HITL snapshot to the active recorder."""
        recorder = self._recorder_provider()
        if recorder is None:
            return
        recorder.set_l1_score(self._build_l1_score_block(round_result))
        recorder.set_hitl(self._snapshot_hitl())

    def _build_l1_score_block(
        self,
        round_result: RoundResult | None = None,
    ) -> dict[str, Any]:
        """l1_score block for dashboard/round_NNNN.json.

        Reads the full score report stored in ``cand['scores']`` by
        ``on_candidate_scored`` and projects to dashboard shape. ``round_result``
        is currently only used to switch sample rendering between live (compact
        one-liners to keep dashboard.json from carrying 2 kB query strings)
        and round-complete (full structured samples).
        """
        candidates = self._current_round.get("candidates") or {}
        is_live = round_result is None

        input_candidates: list[dict[str, Any]] = []
        output_candidates: list[dict[str, Any]] = []
        for idx in sorted(candidates.keys()):
            cand = candidates[idx]
            scores = cand.get("scores") or {}
            label = cand.get("label") or scores.get("changes_description") or ""
            input_candidates.append(
                {
                    "idx": idx,
                    "label": label,
                    "changes_description": scores.get("changes_description") or label,
                    "pp_override": cand.get("pp_override"),
                }
            )
            stats: dict[str, Any] = {
                "accuracy": scores.get("accuracy"),
                "composite": scores.get("composite"),
                "hits": scores.get("hits"),
                "total": scores.get("total"),
                "invalid": scores.get("invalid", False),
                "validation_failures": scores.get("validation_failures") or [],
            }
            samples = cand.get("samples") or []
            output_candidates.append(
                {
                    "idx": idx,
                    "stats": stats,
                    "samples": [fmt_sample_line(s) for s in samples] if is_live else list(samples),
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

    def finalize(self, stop_reason: str) -> None:
        self.set_stop_reason(stop_reason)
        self._log_fh.close()

    # -- Internal --------------------------------------------------------------

    def _snapshot_hitl(self) -> dict[str, Any]:
        """Consolidated HITL block. ``requested_state`` mirrors control.json
        (silent fallback to last-known on missing/malformed — control.json
        may be briefly absent during hand-edits)."""
        existing = self._state.get("hitl") or {}
        snapshot = {
            "requested_state": existing.get("requested_state", "running"),
            "pause_point": existing.get("pause_point"),
            "stop_reason": existing.get("stop_reason"),
        }
        control_path = self.session_dir / "control.json"
        try:
            control = json.loads(control_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return snapshot
        snapshot["requested_state"] = control.get("requested_state", snapshot["requested_state"])
        return snapshot

    def _persist(self) -> None:
        # Direct write — dashboard.json is display-only; readers tolerate
        # partial reads and the file is rewritten on the next callback.
        self._state["hitl"] = self._snapshot_hitl()

        # Mirror per-round node I/O live, same shape as round_NNNN.json::nodes.
        round_idx = self._current_round.get("round", self._state.get("round", 0))
        nodes: dict[str, Any] = {}
        recorder = self._recorder_provider()
        if recorder is not None:
            nodes.update(recorder.snapshot_nodes())
        if self._current_round.get("candidates"):
            nodes["l1_score"] = self._build_l1_score_block()
        ordered: dict[str, Any] = {}
        for preferred in ("l1_generate", "l1_critique"):
            if preferred in nodes:
                ordered[preferred] = nodes.pop(preferred)
        if "l1_score" in nodes:
            ordered["l1_score"] = nodes.pop("l1_score")
        ordered.update(nodes)
        self._state["current_round"] = {"round": round_idx, "nodes": ordered}

        self._state["wallclock_serialized_at"] = datetime.now(UTC).isoformat()
        self.state_path.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
