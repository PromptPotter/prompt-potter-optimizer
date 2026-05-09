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
``SnapshotRecord`` / ``DecisionRecord`` records; this class is one flat
router that mutates the ``state`` scalars + the ``_round`` candidate dict
in place, then merges both into one ``dashboard.json`` write through
``_persist``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import RootCycleDir
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.run_records import PhaseRecord, SnapshotRecord, TokenUsageRecord
from promptpotter.infrastructure.projections.base import ProjectionBase
from promptpotter.infrastructure.projections.live_state import (
    LiveStateCore,
    apply_p_best_update,
    apply_phase,
    top_n_p_best,
)
from promptpotter.infrastructure.store import root_dir_for, session_dir_for
from promptpotter.shared.composite import inline_short_formula_values
from promptpotter.shared.errors import is_degraded
from promptpotter.shared.spend import compute_usd

if TYPE_CHECKING:
    from promptpotter.domain.phases import PhaseEvent
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.projections.audit_trail import AuditTrailProjection

logger = logging.getLogger(__name__)

__all__ = ["LiveDashboardProjection"]


# Phase enter → ``state`` value. The L1_SCORE phase has no entry in this
# table because ``sample_started`` / ``sample_scored`` drive its
# transitions (``scoring`` / ``between_samples`` / ``between_candidates``).
_PHASE_TO_STATE: dict[str, str] = {
    CampaignPhase.INIT: "init",
    CampaignPhase.BASELINE: "baseline",
    CampaignPhase.L1_GENERATE: "l1_generate",
    CampaignPhase.REFINE_STRATEGY: "l2_refining",
    CampaignPhase.MODIFY_PLAN: "l3_replanning",
    CampaignPhase.ESCALATION: "escalation",
}


def _empty_bucket() -> dict[str, Any]:
    """One ``state["spend"]`` sub-bucket — backend or loop."""
    return {
        "used_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "rate_known": False,
        "model": None,
    }


def _empty_spend() -> dict[str, Any]:
    return {
        "backend": _empty_bucket(),
        "loop": _empty_bucket(),
        "total_used_usd": 0.0,
        "budget_usd": None,
    }


# Per-sample terminator badge for the compact ``fmt_sample_line`` rendering;
# unmapped nodes render as the first two characters of the node name.
# Cap on ``state["recent_rules"]`` — rolling buffer the StuckDiagnosis
# webapp widget reads. 8 covers the last few rounds of post-round FSM
# firings without bloating dashboard.json.
_RECENT_RULES_CAP = 8


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


class LiveDashboardProjection(ProjectionBase):
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
        recorder: AuditTrailProjection | None = None,
        evaluators_meta: list[dict[str, Any]] | None = None,
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
        self.patience_max = l1_patience
        # ``state`` is the single liveness/phase indicator (replaces the
        # prior phase/state/layer trio); see ``_PHASE_TO_STATE``.
        r = resume_from or {}
        self.state: dict[str, Any] = {
            "state": r.get("state", "init"),
            "state_since": datetime.now(UTC).isoformat(),
            "stop_reason": r.get("stop_reason"),
            "round": 0,
            "candidate": "",
            "query": "",
            "patience": f"0/{l1_patience}",
            "baseline": r.get("baseline", 0.0),
            "best": r.get("best", 0.0),
            "current_acc": 0.0,
            # Active per-round composite_fitness formula — set on
            # INIT:exit and on ``scoring_steer:applied``. Per-candidate
            # value-inlined versions live under
            # ``current_round.nodes.l1_score.output``.
            "composite_fitness_formula": r.get("composite_fitness_formula"),
            "cycle_id": cycle_id,
            "degraded_count": 0,
            "error_count": 0,
            "total_queries_scored": r.get("total_queries_scored", 0),
            "total_backend_calls": r.get("total_backend_calls", 0),
            # Last-query liveness — orthogonal to ``state``.
            "current_query_payload": None,
            "last_query_elapsed_s": 0.0,
            "wallclock_serialized_at": None,
            "n_variants": n_variants,
            "sp_budget_ttest": sp_budget_ttest,
            # Read by the webapp What-If panel for direction-aware
            # (↑/↓ better) ablation squares.
            "evaluators_meta": evaluators_meta or r.get("evaluators_meta") or [],
            # Spend rollup — split into ``backend`` (per-sample wire LLM
            # calls) and ``loop`` (optimizer L1/L2/L3/critique meta-prompt
            # calls) so the operator reads "Backend $X • Loop $Y" rather
            # than a fused number. ``total_used_usd`` is recomputed on
            # every persist. Per-step USD is taken from the wire when the
            # provider returned one (today only OpenRouter does), else
            # rate-tabled via ``shared/spend.py``. budget_usd is set from
            # campaign.optimization.spend_budget_usd at INIT:exit.
            "spend": r.get("spend") or _empty_spend(),
            # Phase 4 — last N cadence-rule firings + latest signal_inputs
            # snapshot per layer. Webapp's StuckDiagnosis reads these to
            # name *which* signal is at floor/ceiling without opening
            # ``.runtime/signals.jsonl``.
            "recent_rules": list(r.get("recent_rules") or []),
            "current_signals": dict(r.get("current_signals") or {}),
        }
        # Bare short-form formula template (set on INIT:exit). Used per-
        # candidate in ``_build_l1_score_block`` to pair each candidate's
        # composite_fitness number with a value-inlined formula. NOT
        # mirrored as a top-level scalar — readers consume it from the
        # per-candidate slots.
        self.short_formula_template: str | None = None
        # Per-round candidate dict — reset at L1_GENERATE/enter and at
        # round-complete.
        self._round: dict[str, Any] = {"round": 0, "candidates": {}}
        # Shared per-cycle scalars — round number, baseline + best anchors,
        # P(best) round snapshot. The terminal renderer (LiveDisplay) holds
        # the same shape so both subscribers maintain one accumulator
        # surface, not two. The JSON dict above is the persisted shape;
        # the core is the in-memory truth for those scalars.
        self._core = LiveStateCore(
            round_num=int(self.state.get("round") or 0),
            baseline_acc=float(self.state.get("baseline") or 0.0),
            best_acc=float(self.state.get("best") or 0.0),
        )

        self._persist()

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
        evaluators_meta: list[dict[str, Any]] | None = None,
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
            evaluators_meta=evaluators_meta,
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
        """Mark a fork-on-divergence cutover on the live dashboard.

        Updates ``dashboard.json::cycle_id`` so the active fork is
        identified to live readers. The structured fork-cut decision is
        appended to ``ledger.jsonl`` by the runner; this method only
        keeps the dashboard's identity field current.
        """
        del old_cycle_id, from_round
        self.state["cycle_id"] = new_cycle_id
        self._persist()

    # -- Ledger subscription (sole ingress) -----------------------------------
    #
    # DecisionRecord records are persisted to ``ledger.jsonl`` by the
    # runner; this projection only mirrors the live state to
    # ``dashboard.json``. Phases drive scalar updates; snapshots drive
    # per-round candidate structures. Both fan-outs are explicit here —
    # no second dispatch path elsewhere.

    def _handle_phase(self, record: PhaseRecord) -> None:
        if record.phase == "cadence" and record.event == "rule_fired":
            self._absorb_rule_fired(record)
            self._persist()
            return

        if record.phase == "round" and record.event == "display":
            payload = record.payload or {}
            round_result = payload.get("round_result")
            l1_stall = int(payload.get("l1_stall_count") or 0)
            if round_result is not None:
                self._absorb_round_complete(round_result.accuracy, l1_stall)
                # Deposit l1_score block onto the active recorder before
                # runner._finalize_run flush — produces one consolidated
                # .runtime/cache/rounds/round_NNNN.json.
                if self._recorder is not None:
                    self._recorder.set_l1_score(self._build_l1_score_block(round_result))
                self._round = {"round": round_result.round + 1, "candidates": {}}
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
            self._set_state("scoring")
            # Flush so an operator tailing dashboard.json sees the new
            # ``state="scoring"`` + payload during the in-flight call,
            # not only after sample_scored persists.
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
            # No _persist() here — placeholder seed; the next sample_scored
            # write picks it up live, and on_round_complete is the final flush.
        elif ev == "candidate_scored":
            scores = payload.get("scores") or {}
            self._update_current_acc(scores)
            self._set_candidate_scores(ci, ct, scores)
            # No _persist() here — flushed by next sample_scored
            # (next candidate) or by round_complete.
        elif ev == "p_best_update":
            self._update_p_best(
                ci,
                ct,
                payload.get("current_id") or "",
                int(payload.get("n_samples") or 0),
                {str(k): float(v) for k, v in (payload.get("p_best") or {}).items()},
            )
            self._persist()

    # -- Scalar mutations -----------------------------------------------------

    def _apply_phase(self, event: PhaseEvent, view: dict | None) -> None:
        s = self.state
        if event.round is not None:
            s["round"] = event.round
        # Drive ``state`` from the phase taxonomy. L1_SCORE is the
        # exception — sample_started/scored own its transitions; on
        # L1_SCORE:enter we leave the prior state alone until the first
        # sample dispatches.
        if event.event == "enter" and s.get("state") != "stopped":
            mapped = _PHASE_TO_STATE.get(event.phase)
            if mapped is not None:
                self._set_state(mapped)

        phase, data = event.phase, event.data
        if phase == CampaignPhase.INIT and event.event == "enter":
            # Stamp the formula early so it's already on dashboard.json
            # during baseline scoring (which runs before INIT.exit). Without
            # this, the live preview's What-If panel has no formula reference
            # for the entire baseline phase.
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
            s["cycle_id"] = loop_env.state.cycle_id
            s["baseline"] = cycle.tracking.current_accuracy
            self.patience_max = config.optimization.l1_patience
            s["patience"] = f"0/{self.patience_max}"
            # Spend ceiling is set once at INIT:exit; resume picks it up
            # from the on-disk dashboard.json::spend block via _resume_from.
            budget = getattr(config.optimization, "spend_budget_usd", None)
            s["spend"]["budget_usd"] = float(budget) if budget is not None else None
            if view is not None:
                s["composite_fitness_formula"] = view.get("composite_fitness_formula")
                self.short_formula_template = view.get("composite_fitness_formula_short")
        elif phase == "scoring_steer" and event.event == "applied":
            # Operator-driven hot-swap: mirror the new formula onto the
            # top-level scalar so the next dashboard tail shows it.
            new_formula = data.get("formula")
            if new_formula:
                s["composite_fitness_formula"] = new_formula
                # Custom formulas render verbatim — no short form, no
                # value inlining (operator authored it, they read it).
                self.short_formula_template = None
        elif phase == CampaignPhase.L1_GENERATE and event.event == "enter":
            s["round"] = data.get("round", s["round"])
            s["degraded_count"] = 0

        # Mirror baseline/best/round into the shared core so both renderers
        # read the same accumulator. The JSON dict remains the persisted
        # shape; the core is the in-memory truth.
        apply_phase(self._core, event, view)
        if self._core.best_acc > float(s.get("best") or 0.0):
            s["best"] = self._core.best_acc

    def _update_sample_markers(self, ci: int, ct: int, qi: int, qt: int) -> None:
        s = self.state
        s["candidate"] = f"C{ci + 1}/{ct}"
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
            self._accumulate_spend(pd)

        s["current_query_payload"] = None
        s["last_query_elapsed_s"] = round(query_time, 2)
        self._set_state("between_candidates" if last_in_candidate else "between_samples")

    def _accumulate_spend(self, pipeline_data: dict) -> None:
        """Sum per-sample backend tokens into ``state["spend"]["backend"]``.

        Reads ``pipeline_data.step_tokens`` (per-LLM-node ``{input,
        output, cost_usd?}``) and ``pipeline_data.llm_provider`` (model
        string). When a step entry carries ``cost_usd`` (set by the
        backend when the wire LLM is OpenRouter), it short-circuits the
        rate-table lookup. Otherwise ``shared/spend.py`` resolves USD
        from the model string; unknown models still bump token totals so
        the chip falls back to a token-count display rather than a fake
        zero. Cached samples skip this call (no fresh wire cost).
        """
        step_tokens = pipeline_data.get("step_tokens") or {}
        if not isinstance(step_tokens, dict):
            return
        in_tok = 0
        out_tok = 0
        wire_cost: float | None = None
        for entry in step_tokens.values():
            if not isinstance(entry, dict):
                continue
            in_tok += int(entry.get("input", 0) or 0)
            out_tok += int(entry.get("output", 0) or 0)
            step_cost = entry.get("cost_usd")
            if step_cost is not None:
                wire_cost = (wire_cost or 0.0) + float(step_cost)
        if in_tok == 0 and out_tok == 0:
            return
        model = pipeline_data.get("llm_provider")
        self._add_to_bucket("backend", in_tok, out_tok, model, wire_cost)

    def _handle_token_usage(self, record: TokenUsageRecord) -> None:
        """Route an optimizer LLM call into ``state["spend"]["loop"]``.

        Backend-kind events arriving here (none today, but kept for
        symmetry) are folded into the backend bucket. Per-call
        ``cost_usd`` (set by ``llm_call.py`` when OpenRouter returned a
        figure) wins over rate-table lookup; otherwise the rate table
        is consulted via ``compute_usd``.
        """
        bucket = "loop" if record.kind == "optimizer" else "backend"
        self._add_to_bucket(
            bucket,
            int(record.input_tokens),
            int(record.output_tokens),
            record.model,
            record.cost_usd,
        )
        self._persist()

    def _add_to_bucket(
        self,
        bucket: str,
        in_tok: int,
        out_tok: int,
        model: str | None,
        wire_cost_usd: float | None,
    ) -> None:
        """Single mutator for both spend buckets. Recomputes the running total.

        ``wire_cost_usd`` is the provider-reported USD for this call, if
        any (today only OpenRouter ships it). When provided, it
        short-circuits the rate lookup; this is the path that matches
        the operator's invoice. Otherwise the rate table is consulted.
        """
        spend = self.state["spend"]
        b = spend.setdefault(bucket, _empty_bucket())
        b["input_tokens"] += in_tok
        b["output_tokens"] += out_tok
        if model and not b.get("model"):
            b["model"] = model
        usd = compute_usd(model, in_tok, out_tok, override_usd=wire_cost_usd)
        if usd is not None:
            b["used_usd"] = round(b["used_usd"] + usd, 6)
            b["rate_known"] = True
        spend["total_used_usd"] = round(
            spend.get("backend", {}).get("used_usd", 0.0)
            + spend.get("loop", {}).get("used_usd", 0.0),
            6,
        )

    def _update_current_acc(self, scores: dict) -> None:
        self.state["current_acc"] = round(scores.get("accuracy", 0.0), 4)

    def _absorb_rule_fired(self, record: PhaseRecord) -> None:
        """Fold a ``cadence/rule_fired`` event into ``dashboard.json``.

        Appends to ``state["recent_rules"]`` (rolling, capped) and overwrites
        ``state["current_signals"][layer]`` with the latest snapshot. The
        webapp's ``StuckDiagnosis`` widget reads both — recent_rules names
        the last N firings, current_signals exposes the predicate inputs.
        """
        payload = record.payload or {}
        entry = {
            "round": record.round,
            "timestamp": record.timestamp,
            "layer": payload.get("layer"),
            "rule_name": payload.get("rule_name"),
            "rule_priority": payload.get("rule_priority"),
            "next_action": payload.get("next_action"),
            "reason": payload.get("reason"),
            "signal_inputs": payload.get("signal_inputs") or {},
        }
        recent: list[dict[str, Any]] = list(self.state.get("recent_rules") or [])
        recent.append(entry)
        if len(recent) > _RECENT_RULES_CAP:
            recent = recent[-_RECENT_RULES_CAP:]
        self.state["recent_rules"] = recent

        layer = entry["layer"]
        if isinstance(layer, str):
            current: dict[str, Any] = dict(self.state.get("current_signals") or {})
            current[layer] = {
                "round": record.round,
                "rule_name": entry["rule_name"],
                "next_action": entry["next_action"],
                "signal_inputs": entry["signal_inputs"],
            }
            self.state["current_signals"] = current

    def _absorb_round_complete(self, accuracy: float, l1_stall_count: int) -> None:
        s = self.state
        if accuracy > s["best"]:
            s["best"] = round(accuracy, 4)
        s["patience"] = f"{l1_stall_count}/{self.patience_max}"

    # -- Per-round candidate mutations ---------------------------------------

    def _candidate(self, idx: int, total: int = 0) -> dict[str, Any]:
        """Lazy-init candidate slot — single setdefault site for all mutators.

        Sample/score/p_best callbacks may fire before ``candidate_started``
        seeded the slot, so each mutator funnels through here.
        """
        return self._round.setdefault("candidates", {}).setdefault(
            idx, {"idx": idx, "total": total, "label": "", "samples": [], "scores": None}
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
        entry["label"] = changes_description or ""
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

    def _build_l1_score_block(
        self,
        round_result: RoundResult | None = None,
    ) -> dict[str, Any]:
        """Project current candidates to dashboard's l1_score shape.

        ``live`` (round_result is None) renders samples as compact
        one-liners (keeps dashboard.json from carrying 2 kB query strings
        per sample); ``not live`` emits the full sample dicts (round-
        complete flush). Each candidate's composite_fitness number is
        paired with the active formula and the value-inlined short
        formula derived from ``self.short_formula_template``.
        """
        live = round_result is None
        active_formula = self.state.get("composite_fitness_formula")
        candidates = self._round.get("candidates") or {}
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
                # Per-candidate value-inlined short formula. The legend
                # for short codes (``acc``, ``H``, ``lat``, ``R``,
                # ``pc``) lives in ``docs/operations/improvement-tracking.md``.
                "composite_fitness_formula_short": inline_short_formula_values(
                    self.short_formula_template, cand_evaluators
                ),
                # Resolved evaluator values that fed the formula.
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

    # -- Internal --------------------------------------------------------------

    def _persist(self) -> None:
        # Direct write — dashboard.json is display-only; readers tolerate
        # partial reads and the file is rewritten on the next callback.

        # Mirror per-round node I/O live, same shape as round_NNNN.json::nodes.
        nodes: dict[str, Any] = {}
        if self._recorder is not None:
            nodes.update(self._recorder.snapshot_nodes())
        if self._round.get("candidates"):
            nodes["l1_score"] = self._build_l1_score_block()
        ordered = {
            k: nodes.pop(k) for k in ("l1_generate", "l1_critique", "l1_score") if k in nodes
        }
        ordered.update(nodes)
        s = self.state
        s["current_round"] = {"round": self._round.get("round", 0), "nodes": ordered}

        s["wallclock_serialized_at"] = datetime.now(UTC).isoformat()
        self.state_path.write_text(
            json.dumps(s, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
