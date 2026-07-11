"""Live ``PhaseEvent → typed View`` ingress.

``from_phase_event`` (called by ``RunCallbacks.on_phase``) builds a typed view
and applies ctx side effects (round-num tracking, origin rolling, prompt-flat
memo) in one pass. The typed view rides ``PhaseRecord.payload['view']`` on the
in-memory ledger fan-out — subscribers consume it directly (``to_text`` /
attribute reads); Pydantic serializes it to its wire dict on persist + SSE, so
no hand-rolled reconstruction is needed.

The ``score_entry_from_dict`` helper is also consumed by
``application/output/writers.py`` for disk-derived ``log.md`` rendering.
"""

from __future__ import annotations

from typing import Any

from promptpotter.application.optimization.dispatch.llm_call import optimizer_model
from promptpotter.application.scoring.evaluators import (
    default_per_round_formula,
    default_per_round_formula_short,
)
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
from promptpotter.domain.opt_search_point import (
    build_candidate_flat,
    flatten_sp_summary,
)
from promptpotter.domain.phases import PhaseEvent
from promptpotter.domain.rendering import format_l1_critique_for_prompt
from promptpotter.domain.results import ScoredCandidate
from promptpotter.shared import truncate

__all__ = [
    "from_phase_event",
    "score_entry_from_dict",
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
    origin_pp = session.pipeline_params
    if not origin_pp and schema is not None:
        origin_pp = schema.to_pipeline_params()
    ctx.original_sp_flat = flatten_sp_summary(origin_pp)
    ctx.node_param_keys = (
        {s: sorted(k) for s, k in schema.node_param_keys().items()} if schema else None
    )
    ctx.round_num = 0
    ctx.l1_stall_count = 0
    ctx.hearts = opt.lives.start if opt.lives is not None else None
    ctx.hearts_cap = opt.lives.cap if opt.lives is not None else None
    ctx.origin_accuracy = 0.0

    # Resolve the per-round composite formula at INIT.enter so the live
    # dashboard can stamp it before origin scoring fires (matches _init_exit
    # priority: explicit campaign override > schema default > None).
    explicit = session.scoring.scorer_round_formula
    if explicit:
        full, short = explicit, None
    elif schema is None:
        full, short = None, None
    else:
        full = default_per_round_formula(schema)
        short = default_per_round_formula_short(schema)
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
    ctx.origin_accuracy = cycle.tracking.current_accuracy
    ctx.origin_composite_fitness = cycle.tracking.current_composite_fitness
    schema = session.pipeline_schema
    explicit = session.scoring.scorer_round_formula
    if explicit:
        full, short = explicit, None
    elif schema is None:
        full, short = None, None
    else:
        full = default_per_round_formula(schema)
        short = default_per_round_formula_short(schema)
    ctx.composite_fitness_formula = full
    ctx.composite_fitness_formula_short = short

    for field_name, value in cycle.opt_sp.prompt_field_dict().items():
        if value:
            ctx.original_sp_flat[field_name] = str(value)

    return InitExitView(
        origin_acc=cycle.tracking.origin_accuracy,
        cycle_id_short=(session.state.cycle_id or "?")[:12],
        samples=len(session.scoring.scoring_set),
        origin_samples=len(cycle.tracking.origin_per_sample_results),
        obs_on=session.state.obs is not None,
        resumed_from_round=session.state.resumed_from_round,
        cached_rounds_count=len(cycle.rounds),
        task_context_keys=len(cycle.opt_sp.memory.task_context),
        l2_round=cycle.escalation.l2_round,
        composite_fitness_formula=full,
        composite_fitness_formula_short=short,
        origin_composite_fitness=ctx.origin_composite_fitness or 0.0,
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
    sp_diff = SpDiffView(
        columns=tuple(columns),
        node_param_keys=ctx.node_param_keys,
        round_num=ctx.round_num,
        clone_labels=tuple(clone_labels),
        l1_yield=l1_yield,
        l1_n_no_op=n_no_op,
        l1_n_duplicate=n_dup,
    )
    return CandidatesGeneratedView(
        n_candidates=d["n_candidates"],
        source="disk" if d["loaded_from_disk"] else "llm",
        n_scoring_samples=d["n_scoring_samples"],
        l1_yield=l1_yield,
        l1_n_no_op=n_no_op,
        l1_n_duplicate=n_dup,
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
    winner_hits = int(d.get("winner_hits", 0))
    winner_total = int(d.get("winner_total", 0))

    w_acc = float(d["winner_accuracy"])
    improved = bool(d["improved"])
    origin_acc = ctx.origin_accuracy
    # Matched-pair origin (winner-measured samples); fallback ``origin_acc``
    # for round 0 / pre-gate events. Δ uses this so operator-visible Δ
    # matches the ``improved`` gate, not the full-set comparison that
    # punishes PoBB-locked winners.
    matched_origin_acc = float(d.get("winner_matched_origin_accuracy", origin_acc))
    matched_origin_hits = int(d.get("winner_matched_origin_hits", 0))
    matched_origin_composite = d.get("winner_matched_origin_composite")
    delta = w_acc - matched_origin_acc
    p_value: float | None = d.get("p_value")  # computed by l1_score; not recomputed here.
    if improved:
        ctx.origin_accuracy = w_acc

    return RoundCompleteView(
        round=ctx.round_num,
        origin_acc=origin_acc,
        scores=tuple(score_entries),
        winner_label=winner_label,
        winner_accuracy=w_acc,
        winner_composite_fitness=d.get("winner_composite_fitness"),
        winner_evaluators=dict(d["winner_evaluators"]),
        winner_hits=winner_hits,
        winner_total=winner_total,
        improved=improved,
        delta=delta,
        p_value=p_value,
        improved_reason=d.get("improved_reason"),
        next_action=str(d.get("next_action", "?") or "?"),
        l1_critique_text=format_l1_critique_for_prompt(d.get("critique")),
        composite_fitness_formula=ctx.composite_fitness_formula,
        composite_fitness_formula_short=ctx.composite_fitness_formula_short,
        origin_composite_fitness=ctx.origin_composite_fitness,
        matched_origin_accuracy=matched_origin_acc,
        matched_origin_hits=matched_origin_hits,
        matched_origin_composite=matched_origin_composite,
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
    return L2RefineExitView(
        param_changes_count=d.get("param_changes_count", 0),
        task_context_changed=bool(d.get("task_context_changed", False)),
        action=d.get("action", "continue"),
        changes_description=d.get("changes_description", ""),
        warned_samples=d.get("warned_samples", 0),
        l2_prompt=d["l2_prompt"],
        l2_response_json=d.get("l2_response"),
    )


def _plan_enter(d: dict[str, Any], ctx: ViewContext) -> PlanEnterView:
    return PlanEnterView(
        l3_round=d.get("l3_round", "?"),
        l2_stall_count=d.get("l2_stall_count", "?"),
        current_plan_preview=truncate(d.get("current_plan_preview", "") or "", 55, "..."),
    )


def _plan_exit(d: dict[str, Any], ctx: ViewContext) -> PlanExitView:
    return PlanExitView(
        new_plan_preview=truncate(d.get("new_plan_preview", "") or "", 55, "..."),
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
}


# --- live entry points ----------------------------------------------------


def from_phase_event(event: PhaseEvent, ctx: ViewContext) -> AnyView | None:
    """Build a typed view from a ``PhaseEvent``; ``None`` for unregistered phases."""
    if event.round is not None:
        ctx.round_num = event.round
    builder = _BUILDERS.get(f"{event.phase}:{event.event}")
    return builder(event.data, ctx) if builder is not None else None


# --- score-entry helpers (shared with application/output/writers disk render) ---


def score_entry_from_dict(s: dict[str, Any]) -> ScoreEntry:
    """``ScoredCandidate`` dict (``model_dump``) → narrow ``ScoreEntry`` renderer row.

    ``ci_lo``/``ci_hi`` ride the validated candidate (sole Wilson site); the
    scoreboard's ``invalid_reason`` is derived from the first validation failure.
    """
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
        hits=sc.hits,
        total=sc.total,
        ci_lo=sc.ci_lo,
        ci_hi=sc.ci_hi,
        escalation_aborted=sc.escalation_aborted,
        invalid_reason=invalid_reason,
        matched_origin_accuracy=sc.matched_origin_accuracy,
        matched_origin_composite=sc.matched_origin_composite,
    )
