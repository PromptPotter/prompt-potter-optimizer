"""View factories — single source: ``PhaseEvent + ctx → typed View``.

The previous ``phase_views.py`` dict-builder layer is gone. Each per-phase
builder here constructs the typed view directly and applies its ``ctx``
side effects (round-num tracking, baseline rolling, p-value, prompt-flat
memo) in one pass.

Two live entry points:

- ``from_phase_event(event, ctx)`` — used by ``RunListener``; the typed
  view is the single source of truth, and the runner serialises it via
  ``view_to_wire_dict`` before placing it on the ledger payload.
- ``view_from_record(record_dict)`` — used by ledger subscribers reading
  the wire-format dict back. Identity-style projection that mirrors the
  builder shapes.

Two disk entry points (``from_disk_round`` / ``from_disk_log``) rebuild
typed views from on-disk artifacts. The named round-trip invariant
(``tests/test_view_factories``) compares both paths on
``RoundCompleteView``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from promptpotter.domain.opt_search_point import (
    build_candidate_flat,
    flatten_sp_summary,
)
from promptpotter.domain.phases import PhaseEvent
from promptpotter.presentation.views.view_models import (
    AnyView,
    CandidatesGeneratedView,
    DigestStatusView,
    EscalationEnterView,
    EscalationExitView,
    FinalWinnerView,
    HardSamplesView,
    InitEnterView,
    InitExitView,
    L2RefineEnterView,
    L2RefineExitView,
    LogMdView,
    PlanEnterView,
    PlanExitView,
    ProbeEnterView,
    ProbeExitView,
    RoundCompleteView,
    RoundDigestView,
    RoundStartView,
    ScoreEntry,
    SpDiffView,
    WarningEntry,
)
from promptpotter.shared.statistics import min_detectable_effect, proportion_test, wilson_ci

__all__ = [
    "from_disk_log",
    "from_disk_round",
    "from_phase_event",
    "view_from_record",
    "view_to_wire_dict",
]


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


# --- per-phase typed builders (live; with ctx side effects) ---------------


def _init_enter(d: dict, ctx: dict) -> InitEnterView:
    config = d["config"]
    dataset = d["dataset"]
    session = d.get("env")
    schema = session.pipeline_schema if session is not None else None
    opt = config.optimization
    sample = config.sp_budget_ttest

    ctx["max_rounds"] = opt.max_rounds or 0
    ctx["patience"] = opt.l1_patience
    ctx["original_sp_flat"] = flatten_sp_summary(
        schema.to_pipeline_params() if schema else None,
    )
    ctx["node_param_keys"] = (
        {s: sorted(k) for s, k in schema.node_param_keys().items()} if schema else None
    )
    ctx["round_num"] = 0
    ctx["l1_stall_count"] = 0
    ctx["baseline_accuracy"] = 0.0

    return InitEnterView(
        warnings=tuple(
            WarningEntry(title=getattr(w, "title", ""), detail=getattr(w, "detail", ""))
            for w in (d.get("warnings") or [])
        ),
        max_rounds=ctx["max_rounds"],
        patience=ctx["patience"],
        n_variants=opt.n_variants,
        sp_budget_ttest=sample,
        dataset_size=len(dataset),
        mde=min_detectable_effect(sample),
        model=config.optimizer_llm.model or "(default)",
        l2_enabled=opt.enable_l2,
        l3_enabled=opt.enable_l3,
    )


def _init_exit(d: dict, ctx: dict) -> InitExitView:
    cycle = d["state"]
    session = d["env"]
    ctx["baseline_accuracy"] = cycle.current_accuracy
    ctx["baseline_composite"] = getattr(cycle, "current_composite", cycle.current_accuracy)
    schema = getattr(session, "pipeline_schema", None)
    explicit = getattr(session, "scorer_round_formula", None)
    if explicit:
        full, short = explicit, None
    elif schema is None:
        full, short = None, None
    else:
        from promptpotter.application.scoring.evaluators import (
            default_per_round_formula,
            default_per_round_formula_short,
        )

        full = default_per_round_formula(schema)
        short = default_per_round_formula_short(schema)
    ctx["composite_formula"] = full
    ctx["composite_formula_short"] = short

    overlays: dict[str, str] = {}
    for field_name, value in cycle.opt_sp.prompt_field_dict().items():
        if value:
            overlays[field_name] = str(value)
            ctx["original_sp_flat"][field_name] = str(value)

    crit = cycle.opt_sp.l1_critique_text or ""
    return InitExitView(
        baseline_acc=cycle.current_accuracy,
        cycle_id_short=(session.state.cycle_id or "?")[:12],
        samples=len(session.scoring.scoring_dataset),
        obs_on=session.state.obs is not None,
        bootstrap_critique=_truncate(crit.replace("\n", " ").strip(), 80),
        resumed_from_round=session.state.resumed_from_round,
        l1_critique_chars=len(cycle.opt_sp.l1_critique_text or ""),
        task_context_keys=len(cycle.opt_sp.task_context),
        l2_round=cycle.escalation.l2.round,
        prompt_field_overlays=overlays,
        composite_formula=full,
        composite_formula_short=short,
        baseline_composite=ctx["baseline_composite"],
    )


def _l1_generate_enter(d: dict, ctx: dict) -> RoundStartView:
    preview = (d.get("prompt_preview") or "").replace("\n", " ").strip()
    preview = (
        "(empty starting prompt - param-only optimization)"
        if not preview
        else _truncate(preview, 50)
    )

    new_flat = flatten_sp_summary(d.get("pipeline_params"))
    for field_name, value in (d.get("parent_prompt_fields") or {}).items():
        if value:
            new_flat[field_name] = str(value)
    ctx["previous_sp_flat"] = dict(
        (
            ctx.get("original_sp_flat")
            if ctx.get("round_num", 0) == 0
            else ctx.get("current_sp_flat")
        )
        or {}
    )
    ctx["current_sp_flat"] = new_flat

    return RoundStartView(
        round=ctx.get("round_num", 0) + 1,
        max_rounds=ctx.get("max_rounds", 0),
        l1_stall_count=ctx.get("l1_stall_count", 0),
        patience=ctx.get("patience", 0),
        current_acc=d.get("current_accuracy", 0.0),
        prompt_preview=preview,
        n_variants=d.get("n_variants", 0),
        model=d.get("model") or "(default)",
        creativity=d.get("creativity", 0.7),
        has_l1_critique=bool(d.get("has_l1_critique")),
    )


def _l1_generate_exit(d: dict, ctx: dict) -> CandidatesGeneratedView:
    candidates_meta = d.get("candidates", [])
    parent = ctx.get("current_sp_flat") or {}
    columns: list[tuple[str, dict[str, str]]] = [
        ("Start", dict(ctx.get("original_sp_flat") or {})),
        ("Parent", dict(parent)),
    ]
    clone_labels: list[str] = []
    for c in candidates_meta:
        label = f"C{c['idx'] + 1}"
        flat = build_candidate_flat(parent, c)
        if flat == parent:
            clone_labels.append(label)
        columns.append((label, flat))

    l1_yield = float(d.get("l1_yield", 1.0))
    n_no_op = int(d.get("l1_n_no_op", 0))
    n_dup = int(d.get("l1_n_duplicate", 0))
    sp_diff = SpDiffView(
        columns=tuple(columns),
        node_param_keys=ctx.get("node_param_keys"),
        round_num=ctx.get("round_num", 0) + 1,
        clone_labels=tuple(clone_labels),
        l1_yield=l1_yield,
        l1_n_no_op=n_no_op,
        l1_n_duplicate=n_dup,
    )
    return CandidatesGeneratedView(
        n_candidates=d.get("n_candidates", 0),
        source="disk" if d.get("loaded_from_disk") else "llm",
        n_scoring_queries=d.get("n_scoring_queries", 0),
        l1_yield=l1_yield,
        l1_n_no_op=n_no_op,
        l1_n_duplicate=n_dup,
        clone_labels=tuple(clone_labels),
        sp_diff=sp_diff,
    )


def _l1_score_exit(d: dict, ctx: dict) -> RoundCompleteView:
    raw_scores = list(d.get("candidate_scores", []))
    score_entries: list[ScoreEntry] = []
    for i, s in enumerate(raw_scores):
        hits = int(s.get("hits", 0))
        total = int(s.get("total", 0))
        ci_lo, ci_hi = wilson_ci(hits, total)
        score_entries.append(
            ScoreEntry(
                label=f"C{i + 1}",
                accuracy=float(s.get("accuracy", 0.0)),
                composite=s.get("composite"),
                hits=hits,
                total=total,
                ci_lo=ci_lo,
                ci_hi=ci_hi,
                escalation_aborted=bool(s.get("escalation_aborted", False)),
            )
        )

    non_aborted = [s for s in score_entries if not s.escalation_aborted]
    if non_aborted:
        best = max(
            non_aborted,
            key=lambda s: (s.composite if s.composite is not None else s.accuracy, s.accuracy),
        )
        winner_label, winner_hits, winner_total = best.label, best.hits, best.total
    else:
        winner_label, winner_hits, winner_total = "?", 0, 0

    w_acc = float(d.get("winner_accuracy", 0.0))
    improved = bool(d.get("improved", False))
    baseline_acc = ctx.get("baseline_accuracy", 0.0)
    delta = w_acc - baseline_acc if improved else 0.0
    p_value: float | None = None
    if improved and winner_total > 0:
        p_value = proportion_test(
            winner_hits, winner_total, round(baseline_acc * winner_total), winner_total
        )
        ctx["baseline_accuracy"] = w_acc

    return RoundCompleteView(
        round=int(ctx.get("round_num", 0)),
        baseline_acc=baseline_acc,
        scores=tuple(score_entries),
        winner_label=winner_label,
        winner_accuracy=w_acc,
        winner_composite=d.get("winner_composite"),
        winner_evaluators=dict(d.get("winner_evaluators") or {}),
        winner_hits=winner_hits,
        winner_total=winner_total,
        improved=improved,
        delta=delta,
        p_value=p_value,
        next_action=str(d.get("next_action", "?") or "?"),
        l1_critique_text=d.get("l1_critique_text", "") or "",
        composite_formula=ctx.get("composite_formula"),
        composite_formula_short=ctx.get("composite_formula_short"),
        baseline_composite=ctx.get("baseline_composite"),
    )


def _escalation_enter(d: dict, ctx: dict) -> EscalationEnterView:
    return EscalationEnterView(
        check_name=d.get("check_name", "?"),
        target=d.get("target", "?"),
        degraded_rate=d.get("degraded_rate", 0.0),
        warning_types=dict(d.get("warning_types") or {}),
    )


def _escalation_exit(d: dict, ctx: dict) -> EscalationExitView:
    return EscalationExitView(
        classifications=tuple(
            (c.get("warning_type", ""), c.get("status", ""))
            for c in (d.get("classifications") or [])
        )
    )


def _refine_enter(d: dict, ctx: dict) -> L2RefineEnterView:
    params = d.get("current_params") or {}
    return L2RefineEnterView(
        l2_round=d.get("l2_round", "?"),
        l1_stall_count=d.get("l1_stall_count", "?"),
        current_acc=d.get("current_accuracy", 0.0),
        best_acc=d.get("best_accuracy", 0.0),
        current_params={k: str(v) for k, v in params.items()},
        n_params=len(params),
    )


def _refine_exit(d: dict, ctx: dict) -> L2RefineExitView:
    return L2RefineExitView(
        param_changes_count=d.get("param_changes_count", 0),
        task_context_changed=bool(d.get("task_context_changed", False)),
        action=d.get("action", "continue"),
        changes_description=d.get("changes_description", ""),
        warned_queries=d.get("warned_queries", 0),
        top_warning=d.get("top_warning", ""),
        l2_prompt=d.get("l2_prompt", "") or "",
        l2_response_json=d.get("l2_response"),
    )


def _probe_enter(d: dict, ctx: dict) -> ProbeEnterView:
    queries = list(d.get("probe_queries") or [])
    return ProbeEnterView(
        n_probe_queries=d.get("n_probe_queries", len(queries)),
        probe_queries=tuple(queries),
    )


def _probe_exit(d: dict, ctx: dict) -> ProbeExitView:
    return ProbeExitView(
        n_probed=d.get("n_probed", 0),
        probe_hits=d.get("probe_hits", 0),
    )


def _plan_enter(d: dict, ctx: dict) -> PlanEnterView:
    return PlanEnterView(
        l3_round=d.get("l3_round", "?"),
        l2_stall_count=d.get("l2_stall_count", "?"),
        current_plan_preview=_truncate(d.get("current_plan_preview", "") or "", 55),
    )


def _plan_exit(d: dict, ctx: dict) -> PlanExitView:
    return PlanExitView(
        new_plan_preview=_truncate(d.get("new_plan_preview", "") or "", 55),
        changes_description=d.get("changes_description", ""),
    )


_BUILDERS: dict[str, Any] = {
    "init:enter": _init_enter,
    "init:exit": _init_exit,
    "l1_generate:enter": _l1_generate_enter,
    "l1_generate:exit": _l1_generate_exit,
    "l1_score:exit": _l1_score_exit,
    "refine_strategy:enter": _refine_enter,
    "refine_strategy:exit": _refine_exit,
    "modify_plan:enter": _plan_enter,
    "modify_plan:exit": _plan_exit,
    "escalation:enter": _escalation_enter,
    "escalation:exit": _escalation_exit,
    "probe_round:enter": _probe_enter,
    "probe_round:exit": _probe_exit,
}


# --- live entry points ----------------------------------------------------


def from_phase_event(event: PhaseEvent, ctx: dict) -> AnyView | None:
    """Build a typed view from a ``PhaseEvent``; ``None`` for unregistered phases."""
    if event.round is not None:
        ctx["round_num"] = event.round
    builder = _BUILDERS.get(f"{event.phase}:{event.event}")
    return builder(event.data, ctx) if builder is not None else None


def view_to_wire_dict(view: AnyView | None) -> dict[str, Any] | None:
    """Convert a typed view to its JSON-shaped wire dict (for the ledger)."""
    return asdict(view) if view is not None else None


# --- wire-dict → typed reconstruction (for ledger subscribers) ------------


def _sp_diff_from_dict(d: dict | None) -> SpDiffView:
    if not d:
        return SpDiffView((), None, None, (), 1.0, 0, 0)
    return SpDiffView(
        columns=tuple((c[0], dict(c[1])) for c in d.get("columns") or []),
        node_param_keys=d.get("node_param_keys"),
        round_num=d.get("round_num"),
        clone_labels=tuple(d.get("clone_labels") or []),
        l1_yield=float(d.get("l1_yield", 1.0)),
        l1_n_no_op=int(d.get("l1_n_no_op", 0)),
        l1_n_duplicate=int(d.get("l1_n_duplicate", 0)),
    )


def _score_entry_from_dict(s: dict) -> ScoreEntry:
    return ScoreEntry(
        label=s.get("label", ""),
        accuracy=float(s.get("accuracy", 0.0)),
        composite=s.get("composite"),
        hits=int(s.get("hits", 0)),
        total=int(s.get("total", 0)),
        ci_lo=float(s.get("ci_lo", 0.0)),
        ci_hi=float(s.get("ci_hi", 0.0)),
        escalation_aborted=bool(s.get("escalation_aborted", False)),
    )


def view_from_record(record: dict[str, Any]) -> AnyView | None:
    """Reconstruct a typed view from a wire-format ledger payload.

    The wire dict is ``view_to_wire_dict(typed_view)``; this is its inverse.
    """
    v = record.get("view")
    if v is None:
        return None
    key = f"{record.get('phase', '')}:{record.get('event', '')}"
    if key == "init:enter":
        return InitEnterView(
            warnings=tuple(
                WarningEntry(title=w.get("title", ""), detail=w.get("detail", ""))
                for w in v.get("warnings") or []
            ),
            max_rounds=v.get("max_rounds", 0),
            patience=v.get("patience", 0),
            n_variants=v.get("n_variants", 0),
            sp_budget_ttest=v.get("sp_budget_ttest", 0),
            dataset_size=v.get("dataset_size", 0),
            mde=v.get("mde", 0.0),
            model=v.get("model", ""),
            l2_enabled=v.get("l2_enabled", False),
            l3_enabled=v.get("l3_enabled", False),
        )
    if key == "init:exit":
        return InitExitView(**v)
    if key == "l1_generate:enter":
        return RoundStartView(**v)
    if key == "l1_generate:exit":
        return CandidatesGeneratedView(
            **{k: v[k] for k in v if k != "sp_diff" and k != "clone_labels"},
            clone_labels=tuple(v.get("clone_labels") or []),
            sp_diff=_sp_diff_from_dict(v.get("sp_diff")),
        )
    if key == "l1_score:exit":
        return RoundCompleteView(
            **{k: v[k] for k in v if k != "scores"},
            scores=tuple(_score_entry_from_dict(s) for s in v.get("scores") or []),
        )
    if key == "escalation:enter":
        return EscalationEnterView(**v)
    if key == "escalation:exit":
        return EscalationExitView(
            classifications=tuple((c[0], c[1]) for c in v.get("classifications") or []),
        )
    if key == "refine_strategy:enter":
        return L2RefineEnterView(**v)
    if key == "refine_strategy:exit":
        return L2RefineExitView(**v)
    if key == "probe_round:enter":
        return ProbeEnterView(
            n_probe_queries=v.get("n_probe_queries", 0),
            probe_queries=tuple(v.get("probe_queries") or []),
        )
    if key == "probe_round:exit":
        return ProbeExitView(**v)
    if key == "modify_plan:enter":
        return PlanEnterView(**v)
    if key == "modify_plan:exit":
        return PlanExitView(**v)
    return None


# --- from_disk -----------------------------------------------------------


def from_disk_round(
    trial: dict[str, Any],
    *,
    composite_formula: str | None = None,
    composite_formula_short: str | None = None,
    baseline_composite: float | None = None,
) -> RoundCompleteView:
    """Reconstruct a ``RoundCompleteView`` from a persisted ``trial_NNNN.json``."""
    score_entries: list[ScoreEntry] = []
    for i, s in enumerate(trial.get("candidate_scores") or []):
        hits = int(s.get("hits", 0))
        total = int(s.get("total", 0))
        if "ci_lo" in s and "ci_hi" in s:
            ci_lo, ci_hi = float(s["ci_lo"]), float(s["ci_hi"])
        else:
            ci_lo, ci_hi = wilson_ci(hits, total)
        score_entries.append(
            ScoreEntry(
                label=f"C{i + 1}",
                accuracy=float(s.get("accuracy", 0.0)),
                composite=s.get("composite"),
                hits=hits,
                total=total,
                ci_lo=ci_lo,
                ci_hi=ci_hi,
                escalation_aborted=bool(s.get("escalation_aborted", False)),
            )
        )

    winner_label = ""
    non_aborted = [s for s in score_entries if not s.escalation_aborted]
    if non_aborted:
        best = max(
            non_aborted,
            key=lambda s: (s.composite if s.composite is not None else s.accuracy, s.accuracy),
        )
        winner_label = best.label

    winner_acc = float(trial.get("accuracy", 0.0))
    baseline_acc = float(trial.get("baseline_accuracy", 0.0))
    improved = bool(trial.get("improved", False))
    return RoundCompleteView(
        round=int(trial.get("round", 0)),
        baseline_acc=baseline_acc,
        scores=tuple(score_entries),
        winner_label=winner_label,
        winner_accuracy=winner_acc,
        winner_composite=trial.get("composite"),
        winner_evaluators=dict(trial.get("evaluators") or {}),
        winner_hits=int(trial.get("hits", 0)),
        winner_total=int(trial.get("total", 0)),
        improved=improved,
        delta=(winner_acc - baseline_acc) if improved else 0.0,
        p_value=trial.get("p_value"),
        next_action=str(trial.get("next_action", "")),
        l1_critique_text=str((trial.get("opt_search_point") or {}).get("l1_critique_text") or ""),
        composite_formula=composite_formula,
        composite_formula_short=composite_formula_short,
        baseline_composite=baseline_composite,
    )


def _load_p_best_trajectory(
    streams_dir: Path | None, round_num: int
) -> tuple[dict[str, list[float]], dict[str, int]]:
    """Load per-query P(best) snapshots from the JSONL stream for a single round.

    Returns ``(trajectory, stopped_at)`` — trajectory is candidate_id →
    list of P(best) values across queries; stopped_at is candidate_id →
    last-query-idx-before-the-current-candidate-stopped, populated when a
    candidate's value drops to / below ``ε`` (best-effort: the stream
    doesn't carry ε, so we infer "stopped" as the query at which the
    current candidate's snapshot disappears).
    """
    if streams_dir is None or not streams_dir.is_dir():
        return {}, {}
    path = streams_dir / f"round_{round_num:04d}_p_best.jsonl"
    if not path.is_file():
        return {}, {}
    trajectory: dict[str, list[float]] = {}
    last_seen: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            qi = int(rec.get("query_idx", -1))
            for cid, prob in (rec.get("p_best") or {}).items():
                trajectory.setdefault(str(cid), []).append(float(prob))
                last_seen[str(cid)] = qi
    except OSError:
        return {}, {}
    return trajectory, last_seen


def from_disk_log(
    index: dict[str, Any],
    trials: list[dict[str, Any]],
    *,
    hard_samples_artifact: dict[str, Any] | None = None,
    sample_query_lookup: dict[int, str] | None = None,
    streams_dir: Path | None = None,
) -> LogMdView:
    """Build the ``log.md`` view from ``index.json`` + a list of trial dicts."""
    final = index.get("final") or {}
    status = DigestStatusView(
        campaign_id=str(index.get("campaign_id") or ""),
        parent_session_id=index.get("parent_session_id"),
        status=str(index.get("status", "active")),
        stop_reason=str(final.get("stop_reason") or index.get("stop_reason") or "(running)"),
        baseline_accuracy=float(index.get("baseline_accuracy", 0.0)),
        best_accuracy=float(index.get("best_accuracy", 0.0)),
        best_round=index.get("best_round"),
        rounds_completed=int(index.get("n_trials", 0)),
        started_at=final.get("started_at"),
        finished_at=final.get("finished_at"),
    )

    rounds: list[RoundDigestView] = []
    for t in trials:
        osp = t.get("opt_search_point") or {}
        lineage = osp.get("lineage") or {}
        rnd_raw = t.get("round")
        rnd = rnd_raw if isinstance(rnd_raw, int) else 0
        traj, stopped = _load_p_best_trajectory(streams_dir, rnd)
        rounds.append(
            RoundDigestView(
                round=rnd,
                label=str(t.get("label") or "").strip() or f"round_{rnd}",
                accuracy=float(t.get("accuracy", 0.0)),
                improved=bool(t.get("improved", False)),
                hits=int(t.get("hits", 0)),
                total=int(t.get("total", 0)),
                composite=float(t.get("composite", 0.0)),
                changes_description=(lineage.get("changes_description") or "").strip(),
                l2_directive=(osp.get("l2_directive") or "").strip(),
                l1_critique_text=(osp.get("l1_critique_text") or "").strip(),
                l1_yield=float(t.get("l1_yield", 1.0)),
                l1_n_no_op=int(t.get("l1_n_no_op", 0)),
                l1_n_duplicate=int(t.get("l1_n_duplicate", 0)),
                candidates_scored=int(t.get("candidates_scored", 0)),
                evaluators=dict(t.get("evaluators") or {}),
                p_best_trajectory=traj,
                p_best_stopped=stopped,
            )
        )

    final_view = (
        FinalWinnerView(
            winner_prompt_fields=dict(final.get("winner_prompt_fields") or {}),
            winner_pipeline_params=dict(final.get("winner_pipeline_params") or {}),
        )
        if final
        else None
    )
    hard = (
        HardSamplesView(
            artifact=dict(hard_samples_artifact),
            sample_query_lookup=dict(sample_query_lookup or {}),
        )
        if hard_samples_artifact
        else None
    )
    return LogMdView(
        status=status,
        rounds=tuple(rounds),
        formula=final.get("scorer_round_formula"),
        baseline_composite=final.get("baseline_composite"),
        hard_samples=hard,
        final=final_view,
    )
