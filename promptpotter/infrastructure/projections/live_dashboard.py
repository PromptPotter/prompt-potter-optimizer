"""LiveDashboardProjection — operator-facing ``dashboard.json`` + ``output.log`` writer.

Family-root-bound: one stream per cycle family, shared across all forks
(the active fork is identified by ``dashboard.json::cycle_id``). The
constructor takes :data:`RootCycleDir` so a per-cycle audit block cannot
accidentally land here. ``for_session`` is the standard factory — it
derives the root from ``cycle_id`` via ``stores.root_dir_for`` and wraps
in the newtype before delegating to ``__init__``. A runtime assertion in
``__init__`` rejects any path that contains a ``forks/`` segment.

Phase 2 of the persistence cleanup: the class still exposes the legacy
per-event callback API (``on_phase`` / ``on_sample_started`` / …) the
runner calls directly. Phase 3 will replace those with subscription to
the ``RunLedger`` via the ``Projection`` ``on_record`` hook, deriving
the same on-disk artifacts from the typed record stream.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import RootCycleDir
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.run_records import Decision, Phase, RunRecord
from promptpotter.infrastructure.store.stores import root_dir_for, session_dir_for
from promptpotter.shared.composite import inline_short_formula_values
from promptpotter.shared.errors import is_degraded

if TYPE_CHECKING:
    from promptpotter.domain.phases import PhaseEvent
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.projections.audit_trail import AuditTrailProjection

logger = logging.getLogger(__name__)

__all__ = ["LiveDashboardProjection"]


# Keep in sync with ``LayerTransition.phase`` / ``.layer``.
_PHASE_TO_LAYER: dict[str, str] = {
    CampaignPhase.REFINE_STRATEGY: "L2",
    CampaignPhase.MODIFY_PLAN: "L3",
}


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
        # Active per-round composite formula — set on INIT:exit and on
        # ``scoring_steer:applied``. Visible at the top of dashboard.json
        # so an operator tailing the file always sees what's scoring the
        # current round. ``composite_formula_short`` is the short form
        # WITH the latest candidate's resolved values inlined as
        # ``code|value`` (e.g. ``0.65*acc|0.667 + 0.15*H|0.972 + ...``)
        # so the formula is self-describing — no separate legend lookup.
        # See ``docs/operations/improvement-tracking.md`` for the legend.
        "composite_formula": r.get("composite_formula"),
        "composite_formula_short": r.get("composite_formula_short"),
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


class LiveDashboardProjection:
    """Per-cycle dashboard + audit log writer; ensures per-session narrative
    + control files. Not an optimizer checkpoint — resume reads
    ``trials/trial_NNNN.json``, counters here are display continuity only."""

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
        recorder: AuditTrailProjection | None = None,
    ) -> None:
        # Telemetry binds to the family root (the cycle with no parent_cycle_id).
        # Forks share one continuous dashboard / output.log stream; per-fork
        # audit (index.json, log.md, .cache/candidates/, trials/, .cache/rounds/)
        # stays in each cycle's own dir, written through dynamic
        # ``session.state.cycle_id`` paths.
        root_path = Path(root_dir)
        if "forks" in root_path.parts:
            raise ValueError(
                f"LiveDashboardProjection root_dir must be a family root, not a fork dir; "
                f"got {root_path}"
            )
        self.root_dir = root_path
        self.state_path = root_path / "dashboard.json"
        self.log_path = root_path / "output.log"
        self.session_dir = session_dir
        self._recorder = recorder

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

        # Bare short-form formula template (set on INIT:exit). On every
        # candidate score we re-inline the candidate's resolved
        # evaluator values into this template and write the result onto
        # ``self._state["composite_formula_short"]``.
        self._short_formula_template: str | None = None

        self._current_round: dict[str, Any] = {"round": 0, "candidates": {}}

        self._persist()

        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "journal.md").touch()
        (session_dir / "notes.md").touch()

        # One log handle for the projection's lifetime; closed in ``finalize``.
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
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
        resumed_from_round: int | None = None,
        recorder: AuditTrailProjection | None = None,
    ) -> LiveDashboardProjection | None:
        """Build projection, or ``None`` if ids missing. Carries prior UI counters
        across resumes; optimizer resume is separate (``Cycle.restore_from_trial``).
        On ``--from N`` rewind, the ``best`` counter past the surviving trials
        is clamped to avoid a phantom value.

        Telemetry binds to the family root (derived from ``cycle_id`` —
        the prefix before any ``_fork_`` segment). Forks of the same family
        share that root's dashboard.json / output.log."""
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
                resume_from["baseline"] = baseline_accuracy
            except (json.JSONDecodeError, OSError):
                resume_from = None

        if resume_from is not None and resumed_from_round is not None:
            completed = max(resumed_from_round - 1, 0)
            if completed == 0:
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
            s["cycle_id"] = loop_env.state.cycle_id
            s["baseline"] = cycle.current_accuracy
            self._patience_max = config.optimization.l1_patience
            s["patience"] = f"0/{self._patience_max}"
            if view is not None:
                s["composite_formula"] = view.get("composite_formula")
                # Stash the bare short-form template so subsequent
                # candidate scores can re-inline fresh values without
                # losing the structure.
                self._short_formula_template = view.get("composite_formula_short")
                s["composite_formula_short"] = self._short_formula_template
        elif phase == "scoring_steer" and event.event == "applied":
            # Operator-driven hot-swap: mirror the new formula onto the
            # top-level scalar so the next dashboard tail shows it.
            new_formula = data.get("formula")
            if new_formula:
                s["composite_formula"] = new_formula
                # Custom formulas render verbatim — no short form, no
                # value inlining (operator authored it, they read it).
                self._short_formula_template = None
                s["composite_formula_short"] = None
        elif phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            s["round"] = data.get("round", s["round"])
            self._round_start = time.monotonic()
            s["degraded_count"] = 0
            # Fresh round — clear in-flight accumulator (history already populated).
            self._current_round = {"round": s["round"], "candidates": {}}
        elif phase in _PHASE_TO_LAYER:
            s["layer"] = _PHASE_TO_LAYER[phase]

        self._log_fh.write(f"--- {event.phase} {event.event} (round {event.round}) ---\n")
        self._persist()

    def log_fork(self, *, old_cycle_id: str, new_cycle_id: str, from_round: int) -> None:
        """Banner in output.log marking a fork-on-divergence cutover.

        Mirrors the resume banner in ``__init__``. Subsequent HIT/MISS lines
        and phase events belong to the new fork; consumers tailing the
        family-root output.log see the cutover inline."""
        self._log_fh.write(
            f"\n{'=' * 70}\n"
            f"  FORK {new_cycle_id} from round {from_round} (parent: {old_cycle_id})\n"
            f"{'=' * 70}\n\n"
        )
        self._state["cycle_id"] = new_cycle_id
        self._persist()

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
        # No _persist() here — placeholder seed; the next on_sample_scored
        # write picks it up live, and on_round_complete is the final flush.

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
        # Inline this candidate's resolved evaluator values into the
        # short-form formula so the top of dashboard.json reads
        # ``0.65*acc|0.667 + 0.15*H|0.972 + ...`` instead of needing a
        # legend lookup. Skipped when the operator authored a custom
        # formula (no template) — it renders verbatim in
        # ``composite_formula``.
        if self._short_formula_template:
            s["composite_formula_short"] = inline_short_formula_values(
                self._short_formula_template, scores.get("evaluators")
            )

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
        # No _persist() here — on_round_complete (or the next candidate's
        # on_sample_scored) flushes the scored candidate to dashboard.json.

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
        # runner.py flush() — produces one consolidated .cache/rounds/round_NNNN.json.
        self._deposit_round_recorder_state(round_result)

        self._current_round = {"round": round_result.round + 1, "candidates": {}}
        self._persist()

    def _deposit_round_recorder_state(self, round_result: RoundResult) -> None:
        """Hand l1_score block to the active recorder."""
        if self._recorder is None:
            return
        self._recorder.set_l1_score(self._build_l1_score_block(round_result))

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
            cand_evaluators = dict(scores.get("evaluators") or {})
            stats: dict[str, Any] = {
                "accuracy": scores.get("accuracy"),
                "composite": scores.get("composite"),
                # Active formula at the moment this candidate was scored
                # — pairs the composite number with what produced it so
                # an operator reading the searchpoint's score never has
                # to scroll up to the top of dashboard.json to find it.
                "composite_formula": self._state.get("composite_formula"),
                # Per-candidate value-inlined short formula —
                # ``0.65*acc|0.667 + 0.15*H|0.972 + ...`` for THIS
                # candidate's evaluator values. The top-level scalar
                # carries the latest candidate's version; this field
                # carries the per-candidate snapshot, so a finished
                # round records every candidate's resolved formula.
                "composite_formula_short": inline_short_formula_values(
                    self._short_formula_template, cand_evaluators
                ),
                # Resolved evaluator values that fed the formula —
                # ``accuracy``, ``latency_norm``, ``error_rate``,
                # ``prompt_compactness``, etc. Use these to read what
                # each short code (``acc``, ``H``, ``lat``, ``R``,
                # ``pc``) resolved to for this candidate. Legend lives
                # in ``docs/operations/improvement-tracking.md``.
                "evaluators": cand_evaluators,
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

    # -- Ledger subscription (Phase 3) ----------------------------------------

    def on_record(self, record: RunRecord, offset: int) -> None:
        """Subscribe-side hook: receive a typed record from the ledger.

        Phase 3 of the persistence cleanup: the runner appends every fact
        to the per-cycle ``RunLedger`` and projections that ``bind`` to
        the ledger receive each record here. Decisions are mirrored into
        ``output.log`` so a tail follows the gating events (round-winner,
        elimination cuts) inline with the per-query stream. Phase events
        update the layer/phase scalars (the operator's top-of-dashboard
        view). Per-query/per-candidate snapshots are still driven by the
        legacy callbacks — Phase 3c will widen the dispatch.
        """
        if isinstance(record, Decision):
            self._log_fh.write(
                f"  [decision:{record.kind.value} round={record.round}] outcome={record.outcome!r}\n"
            )
        elif isinstance(record, Phase):
            self._state["phase"] = record.phase
            if record.round is not None:
                self._state["round"] = record.round
            self._persist()
        # Snapshot records are still produced by the per-callback API.
        _ = (record, offset)

    # -- Lifecycle -------------------------------------------------------------

    def finalize(self) -> None:
        self._log_fh.close()

    # -- Internal --------------------------------------------------------------

    def _persist(self) -> None:
        # Direct write — dashboard.json is display-only; readers tolerate
        # partial reads and the file is rewritten on the next callback.

        # Mirror per-round node I/O live, same shape as round_NNNN.json::nodes.
        round_idx = self._current_round.get("round", self._state.get("round", 0))
        nodes: dict[str, Any] = {}
        if self._recorder is not None:
            nodes.update(self._recorder.snapshot_nodes())
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
