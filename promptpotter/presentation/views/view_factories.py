"""View-model factories — ``from_phase_event`` (live) and ``from_disk`` (post-hoc).

Both paths produce the same dataclass instances from ``view_models.py``.
The named correctness contract (round-trip invariant on
``RoundCompleteView``) lives in ``tests/test_view_factories``.

This module wraps ``phase_views.build_phase_view`` for the live path —
the existing dict-builder is re-used so its bookkeeping (``ctx`` carry-over
between rounds, p-value computation, baseline accuracy tracking) keeps
running. The factory then projects the wire dict to a typed dataclass.

``view_from_record`` is the second live entry point: subscribers downstream
of the ledger receive the wire dict (no ctx) and need to recover the View
shape for rendering. Internally both factories share ``_project``.
"""

from __future__ import annotations

from typing import Any

from promptpotter.domain.phases import PhaseEvent
from promptpotter.presentation.views.phase_views import build_phase_view
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
    ScoringStartView,
    SpDiffView,
    WarningEntry,
)
from promptpotter.shared.statistics import wilson_ci

__all__ = [
    "from_disk_log",
    "from_disk_round",
    "from_phase_event",
    "view_from_record",
]


def _sp_diff_from_dict(d: dict | None) -> SpDiffView:
    if not d:
        return SpDiffView(
            columns=(),
            node_param_keys=None,
            round_num=None,
            clone_labels=(),
            l1_yield=1.0,
            l1_n_no_op=0,
            l1_n_duplicate=0,
        )
    cols = tuple((c["label"], dict(c["flat"])) for c in d.get("columns") or [])
    return SpDiffView(
        columns=cols,
        node_param_keys=d.get("node_param_keys"),
        round_num=d.get("round_num"),
        clone_labels=tuple(d.get("clone_labels") or []),
        l1_yield=float(d.get("l1_yield", 1.0)),
        l1_n_no_op=int(d.get("l1_n_no_op", 0)),
        l1_n_duplicate=int(d.get("l1_n_duplicate", 0)),
    )


def _score_entries_from_view(scores: list[dict]) -> tuple[ScoreEntry, ...]:
    """Compute Wilson CI bounds and pack into ScoreEntry rows."""
    out: list[ScoreEntry] = []
    for s in scores:
        hits = int(s.get("hits", 0))
        total = int(s.get("total", 0))
        ci_lo, ci_hi = wilson_ci(hits, total)
        out.append(
            ScoreEntry(
                label=str(s.get("label", "")),
                accuracy=float(s.get("accuracy", 0.0)),
                composite=s.get("composite"),
                hits=hits,
                total=total,
                ci_lo=ci_lo,
                ci_hi=ci_hi,
                escalation_aborted=bool(s.get("escalation_aborted", False)),
            )
        )
    return tuple(out)


# --- shared dict→dataclass projection ------------------------------------


_PHASE_TO_VIEW: dict[str, str] = {
    "init:enter": "InitEnter",
    "init:exit": "InitExit",
    "l1_generate:enter": "RoundStart",
    "l1_generate:exit": "CandidatesGenerated",
    "l1_score:enter": "ScoringStart",
    "l1_score:exit": "RoundComplete",
    "escalation:enter": "EscalationEnter",
    "escalation:exit": "EscalationExit",
    "refine_strategy:enter": "L2RefineEnter",
    "refine_strategy:exit": "L2RefineExit",
    "probe_round:enter": "ProbeEnter",
    "probe_round:exit": "ProbeExit",
    "modify_plan:enter": "PlanEnter",
    "modify_plan:exit": "PlanExit",
}


def _project(
    view: dict[str, Any],
    phase: str,
    event_name: str,
    *,
    round_hint: int | None,
) -> AnyView | None:
    """Project a wire-format view dict into the matching typed View."""
    kind = _PHASE_TO_VIEW.get(f"{phase}:{event_name}")
    if kind is None:
        return None
    if kind == "InitEnter":
        return InitEnterView(
            warnings=tuple(
                WarningEntry(title=w.get("title", ""), detail=w.get("detail", ""))
                for w in (view.get("warnings") or [])
            ),
            max_rounds=int(view.get("max_rounds", 0) or 0),
            patience=int(view.get("patience", 0) or 0),
            n_variants=int(view.get("n_variants", 0) or 0),
            sp_budget_ttest=int(view.get("sp_budget_ttest", 0) or 0),
            dataset_size=int(view.get("dataset_size", 0) or 0),
            mde=float(view.get("mde", 0.0) or 0.0),
            model=str(view.get("model", "") or ""),
            l2_enabled=bool(view.get("l2_enabled", False)),
            l3_enabled=bool(view.get("l3_enabled", False)),
        )
    if kind == "InitExit":
        return InitExitView(
            baseline_acc=float(view.get("baseline_acc", 0.0) or 0.0),
            cycle_id_short=str(view.get("cycle_id_short", "")),
            samples=int(view.get("samples", 0) or 0),
            obs_on=bool(view.get("obs_on", False)),
            bootstrap_critique=str(view.get("bootstrap_critique", "") or ""),
            resumed_from_round=int(view.get("resumed_from_round", 0) or 0),
            l1_critique_chars=int(view.get("l1_critique_chars", 0) or 0),
            task_context_keys=int(view.get("task_context_keys", 0) or 0),
            l2_round=int(view.get("l2_round", 0) or 0),
            prompt_field_overlays=dict(view.get("prompt_field_overlays") or {}),
            composite_formula=view.get("composite_formula"),
            composite_formula_short=view.get("composite_formula_short"),
            baseline_composite=float(view.get("baseline_composite", 0.0) or 0.0),
        )
    if kind == "RoundStart":
        return RoundStartView(
            round=int(view.get("round", 0) or 0),
            max_rounds=int(view.get("max_rounds", 0) or 0),
            l1_stall_count=int(view.get("l1_stall_count", 0) or 0),
            patience=int(view.get("patience", 0) or 0),
            current_acc=float(view.get("current_acc", 0.0) or 0.0),
            prompt_preview=str(view.get("prompt_preview", "") or ""),
            n_variants=int(view.get("n_variants", 0) or 0),
            model=str(view.get("model", "") or ""),
            creativity=float(view.get("creativity", 0.0) or 0.0),
            has_l1_critique=bool(view.get("has_l1_critique", False)),
        )
    if kind == "CandidatesGenerated":
        return CandidatesGeneratedView(
            n_candidates=int(view.get("n_candidates", 0) or 0),
            source=str(view.get("source", "")),
            n_scoring_queries=int(view.get("n_scoring_queries", 0) or 0),
            l1_yield=float(view.get("l1_yield", 1.0) or 1.0),
            l1_n_no_op=int(view.get("l1_n_no_op", 0) or 0),
            l1_n_duplicate=int(view.get("l1_n_duplicate", 0) or 0),
            clone_labels=tuple(view.get("clone_labels") or []),
            sp_diff=_sp_diff_from_dict(view.get("sp_diff")),
        )
    if kind == "ScoringStart":
        return ScoringStartView()
    if kind == "RoundComplete":
        scores = list(view.get("scores") or [])
        score_entries = _score_entries_from_view(scores)
        return RoundCompleteView(
            round=int(round_hint or 0),
            baseline_acc=float(view.get("baseline_acc", 0.0) or 0.0),
            scores=score_entries,
            winner_label=str(view.get("winner_label", "")),
            winner_accuracy=float(view.get("winner_accuracy", 0.0) or 0.0),
            winner_composite=view.get("winner_composite"),
            winner_evaluators=dict(view.get("winner_evaluators") or {}),
            winner_hits=int(view.get("winner_hits", 0) or 0),
            winner_total=int(view.get("winner_total", 0) or 0),
            improved=bool(view.get("improved", False)),
            delta=float(view.get("delta", 0.0) or 0.0),
            p_value=view.get("p_value"),
            next_action=str(view.get("next_action", "") or ""),
            l1_critique_text=str(view.get("l1_critique_text", "") or ""),
            composite_formula=view.get("composite_formula"),
            composite_formula_short=view.get("composite_formula_short"),
            baseline_composite=view.get("baseline_composite"),
        )
    if kind == "EscalationEnter":
        return EscalationEnterView(
            check_name=str(view.get("check_name", "")),
            target=str(view.get("target", "")),
            degraded_rate=float(view.get("degraded_rate", 0.0) or 0.0),
            warning_types=dict(view.get("warning_types") or {}),
        )
    if kind == "EscalationExit":
        return EscalationExitView(
            classifications=tuple(
                (c.get("warning_type", ""), c.get("status", ""))
                for c in (view.get("classifications") or [])
            ),
        )
    if kind == "L2RefineEnter":
        return L2RefineEnterView(
            l2_round=view.get("l2_round", "?"),
            l1_stall_count=view.get("l1_stall_count", "?"),
            current_acc=float(view.get("current_acc", 0.0) or 0.0),
            best_acc=float(view.get("best_acc", 0.0) or 0.0),
            current_params=dict(view.get("current_params") or {}),
            n_params=int(view.get("n_params", 0) or 0),
        )
    if kind == "L2RefineExit":
        return L2RefineExitView(
            param_changes_count=int(view.get("param_changes_count", 0) or 0),
            task_context_changed=bool(view.get("task_context_changed", False)),
            action=str(view.get("action", "continue") or "continue"),
            changes_description=str(view.get("changes_description", "") or ""),
            warned_queries=int(view.get("warned_queries", 0) or 0),
            top_warning=str(view.get("top_warning", "") or ""),
            l2_prompt=str(view.get("l2_prompt", "") or ""),
            l2_response_json=view.get("l2_response_json"),
        )
    if kind == "ProbeEnter":
        return ProbeEnterView(
            n_probe_queries=int(view.get("n_probe_queries", 0) or 0),
            probe_queries=tuple(view.get("probe_queries") or []),
        )
    if kind == "ProbeExit":
        return ProbeExitView(
            n_probed=int(view.get("n_probed", 0) or 0),
            probe_hits=int(view.get("probe_hits", 0) or 0),
        )
    if kind == "PlanEnter":
        return PlanEnterView(
            l3_round=view.get("l3_round", "?"),
            l2_stall_count=view.get("l2_stall_count", "?"),
            current_plan_preview=str(view.get("current_plan_preview", "") or ""),
        )
    if kind == "PlanExit":
        return PlanExitView(
            new_plan_preview=str(view.get("new_plan_preview", "") or ""),
            changes_description=str(view.get("changes_description", "") or ""),
        )
    return None


# --- entry points --------------------------------------------------------


def from_phase_event(event: PhaseEvent, ctx: dict) -> AnyView | None:
    """Build a typed view from a ``PhaseEvent`` payload.

    Internally calls ``build_phase_view`` so the existing ``ctx``
    bookkeeping (round number, baseline tracking, formula carryover) runs
    once. The returned dict is then projected onto the matching dataclass.
    Returns ``None`` for phase/event pairs without a registered builder.
    """
    view = build_phase_view(event, ctx)
    if view is None:
        return None
    return _project(view, event.phase, event.event, round_hint=event.round)


def view_from_record(record: dict[str, Any]) -> AnyView | None:
    """Project a wire-format ledger payload (``{phase, event, round, view}``)
    into a typed View. Used by ledger subscribers that receive the dict shape."""
    view = record.get("view")
    if view is None:
        return None
    return _project(
        view,
        str(record.get("phase", "")),
        str(record.get("event", "")),
        round_hint=record.get("round"),
    )


# --- from_disk -----------------------------------------------------------


def _label_for_score(s: dict, idx: int) -> str:
    """Trial JSON candidates have ``candidate_id`` (hash) + an implicit
    enumeration order. Live views label them ``C1`` / ``C2`` / …; mirror that
    so the round-trip invariant compares apples to apples."""
    return f"C{idx + 1}"


def from_disk_round(
    trial: dict[str, Any],
    *,
    composite_formula: str | None = None,
    composite_formula_short: str | None = None,
    baseline_composite: float | None = None,
) -> RoundCompleteView:
    """Reconstruct a ``RoundCompleteView`` from a persisted ``trial_NNNN.json``.

    Reads ``baseline_accuracy`` (D2 field) off the trial directly — no need
    to walk back to the prior trial. ``ci_lo`` / ``ci_hi`` are stored on
    each candidate-score row by ``CandidateScore.to_dict``; we read or
    recompute. ``composite_formula`` / ``baseline_composite`` come from
    ``index.json::final`` and are passed in by the caller (the log writer
    has both at hand).
    """
    candidate_scores = list(trial.get("candidate_scores") or [])
    score_entries: list[ScoreEntry] = []
    for i, s in enumerate(candidate_scores):
        hits = int(s.get("hits", 0))
        total = int(s.get("total", 0))
        if "ci_lo" in s and "ci_hi" in s:
            ci_lo = float(s["ci_lo"])
            ci_hi = float(s["ci_hi"])
        else:
            ci_lo, ci_hi = wilson_ci(hits, total)
        score_entries.append(
            ScoreEntry(
                label=_label_for_score(s, i),
                accuracy=float(s.get("accuracy", 0.0)),
                composite=s.get("composite"),
                hits=hits,
                total=total,
                ci_lo=ci_lo,
                ci_hi=ci_hi,
                escalation_aborted=bool(s.get("escalation_aborted", False)),
            )
        )

    # Winner = the trial's top-level metrics (it's the candidate that
    # was promoted at end-of-round). Match it to a ScoreEntry by accuracy
    # to recover the C-label.
    winner_acc = float(trial.get("accuracy", 0.0))
    winner_label = ""
    non_aborted = [s for s in score_entries if not s.escalation_aborted]
    if non_aborted:
        best = max(
            non_aborted,
            key=lambda s: (s.composite if s.composite is not None else s.accuracy, s.accuracy),
        )
        winner_label = best.label

    baseline_acc = float(trial.get("baseline_accuracy", 0.0))
    improved = bool(trial.get("improved", False))
    delta = (winner_acc - baseline_acc) if improved else 0.0

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
        delta=delta,
        p_value=trial.get("p_value"),
        next_action=str(trial.get("next_action", "") or ""),
        l1_critique_text=str(
            (trial.get("opt_search_point") or {}).get("l1_critique_text", "") or ""
        ),
        composite_formula=composite_formula,
        composite_formula_short=composite_formula_short,
        baseline_composite=baseline_composite,
    )


def _digest_status_from_index(index: dict[str, Any]) -> DigestStatusView:
    final = index.get("final") or {}
    return DigestStatusView(
        campaign_id=str(index.get("campaign_id") or ""),
        parent_session_id=index.get("parent_session_id"),
        status=str(index.get("status", "active")),
        stop_reason=str(final.get("stop_reason") or index.get("stop_reason") or "(running)"),
        baseline_accuracy=float(index.get("baseline_accuracy", 0.0) or 0.0),
        best_accuracy=float(index.get("best_accuracy", 0.0) or 0.0),
        best_round=index.get("best_round"),
        rounds_completed=int(index.get("n_trials", 0) or 0),
        started_at=final.get("started_at"),
        finished_at=final.get("finished_at"),
    )


def _round_digest_from_trial(trial: dict[str, Any]) -> RoundDigestView:
    osp = trial.get("opt_search_point") or {}
    lineage = osp.get("lineage") or {}
    rnd_raw = trial.get("round")
    rnd = int(rnd_raw) if isinstance(rnd_raw, int) else 0
    label = (str(trial.get("label") or "")).strip() or f"round_{rnd}"
    return RoundDigestView(
        round=rnd,
        label=label,
        accuracy=float(trial.get("accuracy", 0.0) or 0.0),
        improved=bool(trial.get("improved", False)),
        hits=int(trial.get("hits", 0) or 0),
        total=int(trial.get("total", 0) or 0),
        composite=float(trial.get("composite", 0.0) or 0.0),
        changes_description=(lineage.get("changes_description") or "").strip(),
        l2_directive=(osp.get("l2_directive") or "").strip(),
        l1_critique_text=(osp.get("l1_critique_text") or "").strip(),
        l1_yield=float(trial.get("l1_yield", 1.0) or 1.0),
        l1_n_no_op=int(trial.get("l1_n_no_op", 0) or 0),
        l1_n_duplicate=int(trial.get("l1_n_duplicate", 0) or 0),
        candidates_scored=int(trial.get("candidates_scored", 0) or 0),
        evaluators=dict(trial.get("evaluators") or {}),
    )


def from_disk_log(
    index: dict[str, Any],
    trials: list[dict[str, Any]],
    *,
    hard_samples_artifact: dict[str, Any] | None = None,
    sample_query_lookup: dict[int, str] | None = None,
) -> LogMdView:
    """Build the ``log.md`` view from ``index.json`` + a list of trial dicts."""
    final = index.get("final") or {}
    final_view: FinalWinnerView | None = None
    if final:
        final_view = FinalWinnerView(
            winner_prompt_fields=dict(final.get("winner_prompt_fields") or {}),
            winner_pipeline_params=dict(final.get("winner_pipeline_params") or {}),
        )
    hard: HardSamplesView | None = None
    if hard_samples_artifact:
        hard = HardSamplesView(
            artifact=dict(hard_samples_artifact),
            sample_query_lookup=dict(sample_query_lookup or {}),
        )
    return LogMdView(
        status=_digest_status_from_index(index),
        rounds=tuple(_round_digest_from_trial(t) for t in trials),
        formula=final.get("scorer_round_formula"),
        baseline_composite=final.get("baseline_composite"),
        hard_samples=hard,
        final=final_view,
    )
