"""LiveDashboardProjection — operator-facing ``dashboard.json`` writer.

Family-root-bound: one ``dashboard.json`` per cycle family, shared across
all forks (the active fork is identified by ``dashboard.json::cycle_id``).
The constructor takes :data:`RootCycleDir` so a per-cycle audit block
cannot accidentally land here. ``for_session`` is the standard factory —
it derives the root from ``cycle_id`` via ``stores.root_dir_for`` and
wraps in the newtype before delegating to ``__init__``. A runtime
assertion in ``__init__`` rejects any path that contains a ``forks/``
segment.

Single ingress: the projection consumes only via ``on_record`` from the
per-cycle ``CycleLedger``. The runner emits typed ``PhaseRecord`` /
``SnapshotRecord`` / ``DecisionRecord`` records; ``LiveDashboardProjection``
is a thin router that fans each record kind to a ``_ScalarBlock``
(top-level scalars + counters) and a ``_RoundBlock`` (per-round nodes /
candidates / p_best leaderboard), then merges both into one
``dashboard.json`` write through ``_persist``.

(Historical: this writer also produced ``output.log`` — a parallel
narrative stream. Dropped because the per-line format was strictly
weaker than ``LiveDisplay`` stderr (truncated query, no pred / gt / io
tokens / sample id), and the structured fact stream lives on
``ledger.jsonl``.)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import RootCycleDir
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.run_records import PhaseRecord, SnapshotRecord
from promptpotter.infrastructure.projections.base import ProjectionBase
from promptpotter.infrastructure.store import root_dir_for, session_dir_for
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
        # Active per-round composite_fitness formula — set on INIT:exit and on
        # ``scoring_steer:applied``. Visible at the top of dashboard.json
        # so an operator tailing the file always sees what's scoring the
        # current round. ``composite_fitness_formula_short`` is the short form
        # WITH the latest candidate's resolved values inlined as
        # ``code|value`` (e.g. ``0.65*acc|0.667 + 0.15*H|0.972 + ...``)
        # so the formula is self-describing — no separate legend lookup.
        # See ``docs/operations/improvement-tracking.md`` for the legend.
        "composite_fitness_formula": r.get("composite_fitness_formula"),
        "composite_fitness_formula_short": r.get("composite_fitness_formula_short"),
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


class _ScalarBlock:
    """Top-level dashboard.json scalars: phase markers, sample markers,
    cumulative counters, current accuracy, composite-fitness formula. The
    short-form formula template is held here too — it's set on INIT/exit
    and consumed by both per-candidate live updates and the per-round
    l1_score block render."""

    def __init__(
        self,
        resume_from: dict[str, Any] | None,
        cycle_id: str | None,
        *,
        l1_patience: int,
        n_variants: int,
        sp_budget_ttest: int,
    ) -> None:
        self.patience_max = l1_patience
        self.state: dict[str, Any] = _make_initial_state(
            resume_from,
            cycle_id,
            patience_max=l1_patience,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
        )
        # Bare short-form formula template (set on INIT:exit). On every
        # candidate score we re-inline the candidate's resolved
        # evaluator values into this template and write the result onto
        # ``state["composite_fitness_formula_short"]``.
        self.short_formula_template: str | None = None

    def apply_phase(self, event: PhaseEvent, view: dict | None) -> None:
        s = self.state
        s["phase"] = event.phase
        if event.round is not None:
            s["round"] = event.round

        phase, data = event.phase, event.data
        if phase == CampaignPhase.INIT and event.event == "exit":
            cycle = data["state"]
            loop_env = data["env"]
            config = data["config"]
            s["cycle_id"] = loop_env.state.cycle_id
            s["baseline"] = cycle.tracking.current_accuracy
            self.patience_max = config.optimization.l1_patience
            s["patience"] = f"0/{self.patience_max}"
            if view is not None:
                s["composite_fitness_formula"] = view.get("composite_fitness_formula")
                self.short_formula_template = view.get("composite_fitness_formula_short")
                s["composite_fitness_formula_short"] = self.short_formula_template
        elif phase == "scoring_steer" and event.event == "applied":
            # Operator-driven hot-swap: mirror the new formula onto the
            # top-level scalar so the next dashboard tail shows it.
            new_formula = data.get("formula")
            if new_formula:
                s["composite_fitness_formula"] = new_formula
                # Custom formulas render verbatim — no short form, no
                # value inlining (operator authored it, they read it).
                self.short_formula_template = None
                s["composite_fitness_formula_short"] = None
        elif phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            s["round"] = data.get("round", s["round"])
            s["degraded_count"] = 0
        elif phase in _PHASE_TO_LAYER:
            s["layer"] = _PHASE_TO_LAYER[phase]

    def update_sample_markers(self, ci: int, ct: int, qi: int, qt: int) -> None:
        s = self.state
        s["candidate"] = f"C{ci + 1}/{ct}"
        s["query"] = f"{qi + 1}/{qt}"

    def mark_sample_started(self, query_text: str) -> None:
        s = self.state
        s["query_in_flight"] = True
        s["query_started_at"] = datetime.now(UTC).isoformat()
        s["current_query_payload"] = (query_text or "")[:120]

    def absorb_sample_scored(self, result: dict) -> None:
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

        s["query_in_flight"] = False
        s["query_started_at"] = None
        s["current_query_payload"] = None
        s["last_query_elapsed_s"] = round(query_time, 2)

    def update_current_acc(self, scores: dict) -> None:
        s = self.state
        s["current_acc"] = round(scores.get("accuracy", 0.0), 4)
        # Inline this candidate's resolved evaluator values into the
        # short-form formula so the top of dashboard.json reads
        # ``0.65*acc|0.667 + 0.15*H|0.972 + ...`` instead of needing a
        # legend lookup. Skipped when the operator authored a custom
        # formula (no template) — it renders verbatim in
        # ``composite_fitness_formula``.
        if self.short_formula_template:
            s["composite_fitness_formula_short"] = inline_short_formula_values(
                self.short_formula_template, scores.get("evaluators")
            )

    def absorb_round_complete(self, accuracy: float, l1_stall_count: int) -> None:
        s = self.state
        if accuracy > s["best"]:
            s["best"] = round(accuracy, 4)
        s["patience"] = f"{l1_stall_count}/{self.patience_max}"
        s["layer"] = "L1"

    def update_cycle_id(self, new_cycle_id: str) -> None:
        self.state["cycle_id"] = new_cycle_id


class _RoundBlock:
    """Per-round structure under ``dashboard.json::current_round.nodes
    .l1_score`` — candidates, samples, and the round-wide P(best)
    leaderboard. Reset at L1_GENERATE/enter and at round-complete."""

    def __init__(self) -> None:
        self.current: dict[str, Any] = {"round": 0, "candidates": {}}

    def begin_round(self, round_idx: int) -> None:
        self.current = {"round": round_idx, "candidates": {}}

    def seed_candidate(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pp_override: dict | None,
    ) -> None:
        # Seed the entry so CURRENT shows labelled pending slots; sample/score
        # callbacks lazy-init the same key for paths that skip this callback.
        entry = self.current.setdefault("candidates", {}).setdefault(idx, {})
        entry["idx"] = idx
        entry["total"] = total
        entry["label"] = changes_description or ""
        entry["pp_override"] = pp_override
        entry.setdefault("samples", [])
        entry.setdefault("scores", None)

    def append_sample(self, ci: int, ct: int, qi: int, qt: int, result: dict) -> None:
        pd = result.get("pipeline_data") or {}
        query_time = float(pd.get("total_time", 0.0) or 0.0)
        hit = bool(result.get("hit"))
        is_cached = bool(result.get("cached", False))
        terminated = pd.get("terminated_at") or ""
        # Lazy-init candidate entry — older paths may skip on_candidate_started.
        cand = self.current.setdefault("candidates", {}).setdefault(
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

    def set_candidate_scores(self, idx: int, total: int, scores: dict) -> None:
        # Store the report verbatim — single source of truth shared with
        # ``round_result.candidate_scores`` (same dict instance).
        # ``build_l1_score_block`` projects to the dashboard/round_NNNN
        # shape without a second copy of the keys.
        cand = self.current.setdefault("candidates", {}).setdefault(
            idx, {"idx": idx, "total": total, "label": "", "samples": [], "scores": None}
        )
        cand["scores"] = scores

    def update_p_best(
        self,
        idx: int,
        total: int,
        current_id: str,
        n_queries: int,
        p_best: dict[str, float],
    ) -> None:
        """Merge per-query P(best) into the candidate slot + top-5 leaderboard.

        Stores each candidate's ``p_best``, signed delta vs prior query, and
        a capped trajectory list. Also publishes the round-wide top-5 sorted
        view at ``current.p_best_top``.
        """
        cand = self.current.setdefault("candidates", {}).setdefault(
            idx, {"idx": idx, "total": total}
        )
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
        cand["p_best_n_queries"] = n_queries

        # Round-wide leaderboard (top-5 by P(best)).
        top = sorted(p_best.items(), key=lambda kv: -kv[1])[:5]
        self.current["p_best_top"] = [{"id": cid, "p_best": p} for cid, p in top]

    def build_l1_score_block(
        self,
        *,
        short_formula_template: str | None,
        active_formula: str | None,
        live: bool,
    ) -> dict[str, Any]:
        """Project current candidates to dashboard's l1_score shape.

        ``live=True`` renders samples as compact one-liners (keeps
        dashboard.json from carrying 2 kB query strings per sample);
        ``live=False`` emits the full sample dicts (round-complete flush).
        ``active_formula`` pairs each candidate's composite_fitness number
        with the formula that produced it; ``short_formula_template`` is
        the bare template re-inlined per-candidate with that candidate's
        resolved evaluator values.
        """
        candidates = self.current.get("candidates") or {}
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
                "composite_fitness": scores.get("composite_fitness"),
                "composite_fitness_formula": active_formula,
                # Per-candidate value-inlined short formula — the
                # top-level scalar carries the latest candidate's
                # version; this field carries the per-candidate
                # snapshot so a finished round records every
                # candidate's resolved formula.
                "composite_fitness_formula_short": inline_short_formula_values(
                    short_formula_template, cand_evaluators
                ),
                # Resolved evaluator values that fed the formula —
                # ``accuracy``, ``latency_norm``, ``error_rate``, etc.
                # Use these to read what each short code (``acc``,
                # ``H``, ``lat``, ``R``, ``pc``) resolved to. Legend
                # lives in ``docs/operations/improvement-tracking.md``.
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
                    "samples": [fmt_sample_line(s) for s in samples] if live else list(samples),
                }
            )
        return {
            "input": {"candidates": input_candidates},
            "output": {"candidates": output_candidates},
        }


class LiveDashboardProjection(ProjectionBase):
    """Per-cycle dashboard writer; routes ledger records to the scalar +
    round blocks and persists the merged view to ``dashboard.json``. Not
    an optimizer checkpoint — resume reads ``rounds/trial_NNNN.json``,
    counters here are display continuity only."""

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
        # Forks share one continuous dashboard.json; per-fork audit
        # (index.json, log.md, rounds/, .runtime/) stays in each cycle's
        # own dir, written through dynamic ``session.state.cycle_id`` paths.
        root_path = Path(root_dir)
        sibling_kinds = {"forks", "diag", "sweeps"}
        if sibling_kinds & set(root_path.parts):
            raise ValueError(
                f"LiveDashboardProjection root_dir must be a family root, not a sibling dir; "
                f"got {root_path}"
            )
        self.root_dir = root_path
        self.state_path = root_path / "dashboard.json"
        self.session_dir = session_dir
        self._recorder = recorder

        self.scalar = _ScalarBlock(
            resume_from,
            cycle_id,
            l1_patience=l1_patience,
            n_variants=n_variants,
            sp_budget_ttest=sp_budget_ttest,
        )
        self.round = _RoundBlock()

        self._persist()

        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "journal.md").touch()
        (session_dir / "notes.md").touch()

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

    def log_fork(self, *, old_cycle_id: str, new_cycle_id: str, from_round: int) -> None:
        """Mark a fork-on-divergence cutover on the live dashboard.

        Updates ``dashboard.json::cycle_id`` so the active fork is
        identified to live readers. The structured fork-cut decision is
        appended to ``ledger.jsonl`` by the runner; this method only
        keeps the dashboard's identity field current.
        """
        del old_cycle_id, from_round
        self.scalar.update_cycle_id(new_cycle_id)
        self._persist()

    # -- Ledger subscription (sole ingress) -----------------------------------
    #
    # DecisionRecord records are persisted to ``ledger.jsonl`` by the
    # runner; this projection only mirrors the live scalar / round state
    # to ``dashboard.json``. Phases drive scalar updates; snapshots drive
    # per-round structures. Both fan-outs are explicit here — no second
    # dispatch path elsewhere.

    def _handle_phase(self, record: PhaseRecord) -> None:
        if record.phase == "round" and record.event == "display":
            payload = record.payload or {}
            round_result = payload.get("round_result")
            l1_stall = int(payload.get("l1_stall_count") or 0)
            if round_result is not None:
                self.scalar.absorb_round_complete(round_result.accuracy, l1_stall)
                # Deposit l1_score block onto the active recorder before
                # runner._finalize_run flush — produces one consolidated
                # .runtime/cache/rounds/round_NNNN.json.
                if self._recorder is not None:
                    self._recorder.set_l1_score(self._build_l1_score_block(round_result))
                self.round.begin_round(round_result.round + 1)
                self._persist()
            return

        payload = record.payload or {}
        view = payload.get("view")
        data = payload.get("data") or {}
        event = PhaseEvent(
            phase=record.phase,
            event=record.event,
            round=record.round,
            data=data,
        )
        self.scalar.apply_phase(event, view)
        # L1_GENERATE/enter resets the in-flight round block in lockstep
        # with the scalar's degraded_count clear.
        if event.phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            self.round.begin_round(self.scalar.state["round"])
        self._persist()

    def _handle_snapshot(self, record: SnapshotRecord) -> None:
        ev = record.event
        payload = record.payload or {}
        ci = int(record.candidate_idx or 0)
        ct = int(record.candidate_total or 0)
        qi = int(record.sample_idx or 0)
        qt = int(record.sample_total or 0)
        if ev == "sample_started":
            self.scalar.update_sample_markers(ci, ct, qi, qt)
            self.scalar.mark_sample_started(payload.get("query_text") or "")
            # Flush so an operator tailing dashboard.json sees
            # query_in_flight + payload during the in-flight call, not
            # only after sample_scored persists.
            self._persist()
        elif ev == "sample_scored":
            result = payload.get("result") or {}
            self.scalar.update_sample_markers(ci, ct, qi, qt)
            self.scalar.absorb_sample_scored(result)
            self.round.append_sample(ci, ct, qi, qt, result)
            self._persist()
        elif ev == "candidate_started":
            self.round.seed_candidate(
                ci,
                ct,
                payload.get("changes_description") or "",
                payload.get("pp_override"),
            )
            # No _persist() here — placeholder seed; the next sample_scored
            # write picks it up live, and on_round_complete is the final flush.
        elif ev == "candidate_scored":
            scores = payload.get("scores") or {}
            self.scalar.update_current_acc(scores)
            self.round.set_candidate_scores(ci, ct, scores)
            # No _persist() here — flushed by next sample_scored
            # (next candidate) or by round_complete.
        elif ev == "p_best_update":
            self.round.update_p_best(
                ci,
                ct,
                payload.get("current_id") or "",
                int(payload.get("n_queries") or 0),
                {str(k): float(v) for k, v in (payload.get("p_best") or {}).items()},
            )
            self._persist()

    def _build_l1_score_block(
        self,
        round_result: RoundResult | None = None,
    ) -> dict[str, Any]:
        return self.round.build_l1_score_block(
            short_formula_template=self.scalar.short_formula_template,
            active_formula=self.scalar.state.get("composite_fitness_formula"),
            live=round_result is None,
        )

    # -- Internal --------------------------------------------------------------

    def _persist(self) -> None:
        # Direct write — dashboard.json is display-only; readers tolerate
        # partial reads and the file is rewritten on the next callback.

        # Mirror per-round node I/O live, same shape as round_NNNN.json::nodes.
        round_idx = self.round.current.get("round", 0)
        nodes: dict[str, Any] = {}
        if self._recorder is not None:
            nodes.update(self._recorder.snapshot_nodes())
        if self.round.current.get("candidates"):
            nodes["l1_score"] = self._build_l1_score_block()
        ordered: dict[str, Any] = {}
        for preferred in ("l1_generate", "l1_critique"):
            if preferred in nodes:
                ordered[preferred] = nodes.pop(preferred)
        if "l1_score" in nodes:
            ordered["l1_score"] = nodes.pop("l1_score")
        ordered.update(nodes)
        s = self.scalar.state
        s["current_round"] = {"round": round_idx, "nodes": ordered}

        s["wallclock_serialized_at"] = datetime.now(UTC).isoformat()
        self.state_path.write_text(
            json.dumps(s, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
