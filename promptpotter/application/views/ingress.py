"""Live ``PhaseEvent → typed View`` ingress. The view rides ``PhaseRecord.payload['view']`` and Pydantic serialises it on
persist + SSE, so nothing downstream hand-rebuilds it."""

from __future__ import annotations

from typing import Any

from promptpotter.application.optimization.dispatch.llm_call.prompts import optimizer_model
from promptpotter.application.scoring.evaluators import resolve_round_formula
from promptpotter.application.views.view_models import (
    AnyView,
    CandidatesGeneratedView,
    EscalationEnterView,
    EscalationExitView,
    InitEnterView,
    InitExitView,
    L2RefineEnterView,
    L2RefineExitView,
    PlanEnterView,
    PlanExitView,
    RoundCompleteView,
    RoundStartView,
    ScoreEntry,
    SpDiffView,
    ViewContext,
    WarningEntry,
)
from promptpotter.domain.candidate_diff import build_candidate_flat, flatten_sp_summary
from promptpotter.domain.phases import PhaseEvent
from promptpotter.domain.rendering import format_l1_critique_for_prompt
from promptpotter.domain.results import ScoredCandidate
from promptpotter.shared import truncate

__all__ = [
    "from_phase_event",
]


# --- per-phase typed builders (live; with ctx side effects) ---------------


def _init_enter(d: dict[str, Any], ctx: ViewContext) -> InitEnterView:
    config = d["config"]
    dataset = d["dataset"]
    session = d["env"]
    schema = session.pipeline_schema
    opt = config.optimization
    sample = config.sp_budget_ttest

    ctx.max_rounds = opt.max_rounds or 0
    ctx.patience = opt.l1_patience
    origin_pp = session.pipeline_params or schema.to_pipeline_params()
    ctx.original_sp_flat = flatten_sp_summary(origin_pp)
    ctx.node_param_keys = {s: sorted(k) for s, k in schema.node_param_keys().items()}
    ctx.round_num = 0
    ctx.l1_stall_count = 0
    ctx.hearts = opt.lives.start if opt.lives is not None else None
    ctx.hearts_cap = opt.lives.cap if opt.lives is not None else None
    ctx.parent_accuracy = 0.0

    # Resolve the per-round composite formula at INIT.enter so the live
    # dashboard can stamp it before origin scoring fires (matches _init_exit
    # priority: explicit campaign override > schema default > None).
    full, short = resolve_round_formula(session.scoring.scorer_round_formula, schema)
    ctx.composite_fitness_formula = full
    ctx.composite_fitness_formula_short = short

    return InitEnterView(
        warnings=tuple(
            WarningEntry(title=w.title, detail=w.detail) for w in (d.get("warnings") or [])
        ),
        max_rounds=ctx.max_rounds,
        patience=ctx.patience,
        n_variants=opt.n_variants,
        sp_budget_ttest=sample,
        dataset_size=len(dataset),
        model=optimizer_model(),
        composite_fitness_formula=full,
        composite_fitness_formula_short=short,
    )


def _init_exit(d: dict[str, Any], ctx: ViewContext) -> InitExitView:
    cycle = d["state"]
    session = d["env"]
    ctx.parent_accuracy = cycle.tracking.current_accuracy
    ctx.parent_composite_fitness = cycle.tracking.current_composite_fitness
    schema = session.pipeline_schema
    full, short = resolve_round_formula(session.scoring.scorer_round_formula, schema)
    ctx.composite_fitness_formula = full
    ctx.composite_fitness_formula_short = short

    for field_name, value in cycle.opt_sp.prompt_field_dict().items():
        if value:
            ctx.original_sp_flat[field_name] = str(value)

    return InitExitView(
        origin_acc=cycle.origin_round.accuracy,
        cycle_id_short=(session.state.cycle_id or "?")[:12],
        samples=len(session.scoring.scoring_set),
        origin_samples=len(cycle.origin_round.results),
        obs_on=session.state.obs is not None,
        resumed_from_round=session.state.resumed_from_round,
        # Rounds replayed off disk — round 0 is built fresh by `Cycle.start`, so it is
        # only "cached" when a resume's priors superseded it.
        cached_rounds_count=sum(1 for rr in cycle.rounds if rr.round > 0),
        task_context_keys=len(cycle.opt_sp.memory.task_context),
        l2_round=cycle.escalation.l2_round,
        composite_fitness_formula=full,
        composite_fitness_formula_short=short,
        origin_composite_fitness=ctx.parent_composite_fitness or 0.0,
    )


def _l1_generate_enter(d: dict[str, Any], ctx: ViewContext) -> RoundStartView:
    # Header renders before candidates exist — describes parent SP, not the
    # round's mutation mode (sp_diff table emits that next render).
    preview = (d.get("prompt_preview") or "").replace("\n", " ").strip()
    preview = "(empty)" if not preview else truncate(preview, 50, "...")

    new_flat = flatten_sp_summary(d.get("pipeline_params"))
    for field_name, value in (d.get("parent_prompt_fields") or {}).items():
        if value:
            new_flat[field_name] = str(value)
    for field_name, value in (d.get("parent_task_context") or {}).items():
        if value:
            new_flat[f"tc.{field_name}"] = str(value)
    ctx.current_sp_flat = new_flat

    return RoundStartView(
        round=ctx.round_num,
        max_rounds=ctx.max_rounds,
        l1_stall_count=ctx.l1_stall_count,
        patience=ctx.patience,
        current_acc=d.get("current_accuracy", 0.0),
        prompt_preview=preview,
        n_variants=d.get("n_variants", 0),
        model=d.get("model") or "(default)",
        has_l1_critique=bool(d.get("has_l1_critique")),
        hearts=ctx.hearts,
        hearts_cap=ctx.hearts_cap,
    )


def _l1_generate_exit(d: dict[str, Any], ctx: ViewContext) -> CandidatesGeneratedView:
    candidates_meta = d["candidates"]
    parent = ctx.current_sp_flat
    columns: list[tuple[str, dict[str, str]]] = [
        ("Start", dict(ctx.original_sp_flat)),
        ("Parent", dict(parent)),
    ]
    clone_labels: list[str] = []
    for c in candidates_meta:
        label = c["label"]
        flat = build_candidate_flat(parent, c)
        if flat == parent:
            clone_labels.append(label)
        columns.append((label, flat))

    l1_yield = float(d["l1_yield"])
    n_no_op = int(d["l1_n_no_op"])
    n_dup = int(d["l1_n_duplicate"])
    n_rep = int(d.get("l1_n_repeat", 0))
    sp_diff = SpDiffView(
        columns=tuple(columns),
        node_param_keys=ctx.node_param_keys,
        round_num=ctx.round_num,
        clone_labels=tuple(clone_labels),
        l1_yield=l1_yield,
        l1_n_no_op=n_no_op,
        l1_n_duplicate=n_dup,
        l1_n_repeat=n_rep,
    )
    return CandidatesGeneratedView(
        n_candidates=d["n_candidates"],
        source="disk" if d["loaded_from_disk"] else "llm",
        n_scoring_samples=d["n_scoring_samples"],
        l1_yield=l1_yield,
        l1_n_no_op=n_no_op,
        l1_n_duplicate=n_dup,
        l1_n_repeat=n_rep,
        clone_labels=tuple(clone_labels),
        sp_diff=sp_diff,
    )


def _l1_score_exit(d: dict[str, Any], ctx: ViewContext) -> RoundCompleteView:
    score_entries = [score_entry_from_dict(s) for s in d.get("candidate_scores") or []]

    # The promoted winner is elected by `elect_round_winner` (paired-delta LCB);
    # read its identity straight off the round result, never re-elect by a
    # point-estimate here — that could name a different candidate than the one
    # actually promoted (whose accuracy is `winner_accuracy`), so the verdict
    # line / SCOREBOARD `*` would disagree with the dashboard.
    winner_label = str(d.get("winner_label") or "?")
    winner_total = int(d.get("winner_total", 0))

    w_acc = float(d["winner_accuracy"])
    improved = bool(d["improved"])
    parent_acc = ctx.parent_accuracy
    # Matched-pair parent (winner-measured samples). Δ uses this so operator-visible Δ
    # matches the ``improved`` gate, not the full-set comparison that punishes PoBB-locked
    # winners. Absent when the winner did not cover the parent's panel, and it stays absent —
    # falling back to ``parent_acc`` publishes a prefix accuracy minus a full-panel rate.
    raw_matched = d.get("winner_matched_parent_accuracy")
    matched_parent_acc = None if raw_matched is None else float(raw_matched)
    matched_parent_composite = d.get("winner_matched_parent_composite")
    delta = None if matched_parent_acc is None else w_acc - matched_parent_acc
    p_value: float | None = d.get("p_value")  # computed by l1_score; not recomputed here.
    if improved:
        # BOTH move, or the candidate box renders an accuracy Δ against the new parent
        # above a composite Δ against C0, under one word and with nothing to tell them apart.
        ctx.parent_accuracy = w_acc
        w_comp = d.get("winner_composite_fitness")
        if isinstance(w_comp, int | float):
            ctx.parent_composite_fitness = float(w_comp)

    return RoundCompleteView(
        round=ctx.round_num,
        parent_acc=parent_acc,
        scores=tuple(score_entries),
        winner_label=winner_label,
        winner_accuracy=w_acc,
        winner_composite_fitness=d.get("winner_composite_fitness"),
        winner_evaluators=dict(d["winner_evaluators"]),
        winner_total=winner_total,
        improved=improved,
        delta=delta,
        p_value=p_value,
        verdict_reason=d.get("verdict_reason"),
        next_action=str(d.get("next_action", "?") or "?"),
        l1_critique_text=format_l1_critique_for_prompt(d.get("critique")),
        composite_fitness_formula=ctx.composite_fitness_formula,
        composite_fitness_formula_short=ctx.composite_fitness_formula_short,
        matched_parent_accuracy=matched_parent_acc,
        matched_parent_composite=matched_parent_composite,
    )


def _escalation_enter(d: dict[str, Any], ctx: ViewContext) -> EscalationEnterView:
    return EscalationEnterView(
        check_name=d.get("check_name", "?"),
        target=d.get("target", "?"),
        degraded_rate=d.get("degraded_rate", 0.0),
        warning_types=dict(d.get("warning_types") or {}),
    )


def _escalation_exit(d: dict[str, Any], ctx: ViewContext) -> EscalationExitView:
    return EscalationExitView(
        classifications=tuple(
            (c.get("warning_type", ""), c.get("status", ""))
            for c in (d.get("classifications") or [])
        )
    )


def _refine_enter(d: dict[str, Any], ctx: ViewContext) -> L2RefineEnterView:
    params = d.get("l1_overrides") or {}
    return L2RefineEnterView(
        l2_round=d.get("l2_round", "?"),
        l1_stall_count=d.get("l1_stall_count", "?"),
        current_acc=d.get("current_accuracy", 0.0),
        best_acc=d.get("best_accuracy", 0.0),
        l1_overrides={k: str(v) for k, v in params.items()},
    )


def _refine_exit(d: dict[str, Any], ctx: ViewContext) -> L2RefineExitView:
    l2_theta = d.get("l2_best_theta_at_entry")
    return L2RefineExitView(
        param_changes_count=d.get("param_changes_count", 0),
        l1_layout_changed=bool(d.get("l1_layout_changed", False)),
        axis_targeted=d.get("axis_targeted", ""),
        changes_description=d.get("changes_description", ""),
        l2_round=int(d["l2_round"]),
        l2_stall_count=int(d["l2_stall_count"]),
        l2_best_composite_fitness_at_entry=float(d["l2_best_composite_fitness_at_entry"]),
        l2_best_theta_at_entry=None if l2_theta is None else float(l2_theta),
    )


def _plan_enter(d: dict[str, Any], ctx: ViewContext) -> PlanEnterView:
    return PlanEnterView(
        l3_round=d.get("l3_round", "?"),
        l2_stall_count=d.get("l2_stall_count", "?"),
        current_plan_preview=truncate(d.get("current_plan_preview", "") or "", 55, "..."),
    )


def _plan_exit(d: dict[str, Any], ctx: ViewContext) -> PlanExitView:
    l3_theta = d.get("l3_best_theta_at_entry")
    return PlanExitView(
        new_plan_preview=truncate(d.get("new_plan_preview", "") or "", 55, "..."),
        changes_description=d.get("changes_description", ""),
        l3_round=int(d["l3_round"]),
        l3_stall_count=int(d["l3_stall_count"]),
        l3_best_composite_fitness_at_entry=float(d["l3_best_composite_fitness_at_entry"]),
        l3_best_theta_at_entry=None if l3_theta is None else float(l3_theta),
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
}


# --- live entry points ----------------------------------------------------


def from_phase_event(event: PhaseEvent, ctx: ViewContext) -> AnyView | None:
    if event.round is not None:
        ctx.round_num = event.round
    builder = _BUILDERS.get(f"{event.phase}:{event.event}")
    return builder(event.data, ctx) if builder is not None else None


# --- score-entry helpers (shared with application/output disk render) ---


def score_entry_from_dict(s: dict[str, Any]) -> ScoreEntry:
    """``ScoredCandidate`` dict → the narrow renderer row. The interval is the COMPOSITE CI, which brackets the number the
    row reports — the old Wilson pair bracketed a binary hit rate nothing displayed."""
    sc = ScoredCandidate.model_validate(s)
    invalid_reason: str | None = None
    if sc.invalid and sc.validation_failures:
        first = sc.validation_failures[0]
        reason = first.get("reason") if isinstance(first, dict) else None
        invalid_reason = str(reason) if reason else None
    return ScoreEntry(
        label=sc.label,
        accuracy=sc.accuracy,
        composite_fitness=sc.composite_fitness,
        total=sc.total,
        mean_fitness_ci_lo=sc.mean_fitness_ci_lo,
        mean_fitness_ci_hi=sc.mean_fitness_ci_hi,
        escalation_aborted=sc.escalation_aborted,
        partial_reason=sc.partial_reason,
        invalid_reason=invalid_reason,
        matched_parent_accuracy=sc.matched_parent_accuracy,
        matched_parent_composite=sc.matched_parent_composite,
        theta=sc.theta,
        theta_se=sc.theta_se,
    )
