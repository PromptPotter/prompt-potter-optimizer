"""Live ``PhaseEvent → typed View`` ingress. The view rides ``PhaseRecord.payload['view']`` and Pydantic serialises it on
persist + SSE, so nothing downstream hand-rebuilds it."""

from __future__ import annotations

from typing import Any

from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.optimizers.nodes import RoundOpening
from promptpotter.application.scoring.evaluators import resolve_cell_formula
from promptpotter.application.views.view_models import (
    AnyView,
    BenchGradedView,
    BenchScoredView,
    CandidatesGeneratedView,
    InitEnterView,
    InitExitView,
    MeasureEnterView,
    OptimizerStepEnterView,
    OptimizerStepExitView,
    RoundCompleteView,
    RoundStartView,
    ScoreEntry,
    SpDiffView,
    ViewContext,
    WarningEntry,
)
from promptpotter.domain.candidate_diff import build_candidate_flat, flatten_sp_summary
from promptpotter.domain.dashboard_rows import RunStanding
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.results import ArmOutcome, ScoredCandidate
from promptpotter.domain.ruler import is_flat_ruler_id
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
    selected = select_optimizer(opt)
    pacing = selected.pacing
    sample = selected.round_cells(len(dataset))

    ctx.max_rounds = opt.max_rounds or 0
    ctx.patience = pacing.patience
    origin_pp = session.pipeline_params or schema.to_pipeline_params()
    ctx.original_sp_flat = flatten_sp_summary(origin_pp)
    ctx.node_param_keys = {s: sorted(k) for s, k in schema.node_param_keys().items()}
    ctx.round_num = 0
    opening, cap = pacing.stalls_left or (None, None)
    ctx.run_standing = RunStanding(
        rounds_without_advance=0, stalls_left=opening, stalls_left_cap=cap
    )
    ctx.parent_accuracy = 0.0

    # Resolve the per-round composite formula at INIT.enter so the live
    # dashboard can stamp it before origin scoring fires (matches _init_exit
    # priority: explicit campaign override > schema default > None).
    full, short = resolve_cell_formula(session.scoring.scorer_cell_formula, schema)
    ctx.composite_fitness_formula = full
    ctx.composite_fitness_formula_short = short

    return InitEnterView(
        warnings=tuple(
            WarningEntry(title=w.title, detail=w.detail) for w in (d.get("warnings") or [])
        ),
        max_rounds=ctx.max_rounds,
        patience=ctx.patience,
        sp_budget_round=sample,
        dataset_size=len(dataset),
        model=selected.model(),
        composite_fitness_formula=full,
        composite_fitness_formula_short=short,
    )


def _init_exit(d: dict[str, Any], ctx: ViewContext) -> InitExitView:
    cycle = d["state"]
    session = d["env"]
    ctx.parent_accuracy = cycle.tracking.current_accuracy
    ctx.parent_composite_fitness = cycle.tracking.current_composite_fitness
    schema = session.pipeline_schema
    full, short = resolve_cell_formula(session.scoring.scorer_cell_formula, schema)
    ctx.composite_fitness_formula = full
    ctx.composite_fitness_formula_short = short
    ctx.headline_metric = session.scoring.headline_metric

    for field_name, value in cycle.opt_sp.prompt_field_dict().items():
        if value:
            ctx.original_sp_flat[field_name] = str(value)

    return InitExitView(
        origin_acc=cycle.origin_round.accuracy,
        cycle_id_short=(session.state.cycle_id or "?")[:12],
        samples=len(session.scoring.require_partition().search),
        bench_samples=len(session.scoring.require_partition().bench),
        origin_samples=len(cycle.origin_round.results),
        obs_on=session.state.obs is not None,
        resumed_from_round=session.state.resumed_from_round,
        # Rounds replayed off disk — round 0 is built fresh by `Cycle.start`, so it is
        # only "cached" when a resume's priors superseded it.
        cached_rounds_count=sum(1 for rr in cycle.rounds if rr.round > 0),
        task_context_keys=len(cycle.framing),
        composite_fitness_formula=full,
        composite_fitness_formula_short=short,
    )


def _propose_enter(d: dict[str, Any], ctx: ViewContext) -> RoundStartView:
    # Header renders before candidates exist — describes parent SP, not the
    # round's mutation mode (sp_diff table emits that next render).
    opening: RoundOpening = d["opening"]
    preview = (d.get("prompt_preview") or "").replace("\n", " ").strip()
    preview = "(empty)" if not preview else truncate(preview, 50, "...")

    new_flat = flatten_sp_summary(d.get("pipeline_params"))
    for field_name, value in (d.get("parent_prompt_fields") or {}).items():
        if value:
            new_flat[field_name] = str(value)
    ctx.current_sp_flat = new_flat
    # Per round, not only at INIT: `change-run-limits` moves the cap between rounds.
    ctx.max_rounds = d["max_rounds"] or 0

    return RoundStartView(
        node=str(d["node"]),
        round=ctx.round_num,
        max_rounds=ctx.max_rounds,
        standing=opening.standing,
        current_acc=d.get("current_accuracy", 0.0),
        prompt_preview=preview,
        arms=opening.arms,
        note=opening.note,
        model=d.get("model") or "(default)",
        run_standing=ctx.run_standing,
    )


def _propose_exit(d: dict[str, Any], ctx: ViewContext) -> CandidatesGeneratedView:
    opening: RoundOpening = d["opening"]
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

    sp_diff = SpDiffView(
        columns=tuple(columns),
        node_param_keys=ctx.node_param_keys,
        round_num=ctx.round_num,
        clone_labels=tuple(clone_labels),
        collapses=dict(d["collapses"]),
        proposer=opening.proposer,
    )
    return CandidatesGeneratedView(
        n_candidates=len(candidates_meta),
        source="disk" if opening.replayed else "llm",
        n_scoring_samples=d["n_scoring_samples"],
        clone_labels=tuple(clone_labels),
        sp_diff=sp_diff,
    )


def _measure_enter(d: dict[str, Any], ctx: ViewContext) -> MeasureEnterView:
    return MeasureEnterView(
        node=str(d["node"]), n_candidates=int(d["n_candidates"]), n_samples=int(d["n_samples"])
    )


def _bench_scored(d: dict[str, Any], ctx: ViewContext) -> BenchScoredView:
    return BenchScoredView(bench=d["bench"].model_dump(mode="json"))


def _bench_graded(d: dict[str, Any], ctx: ViewContext) -> BenchGradedView:
    reading = d["reading"]
    return BenchGradedView(
        reading=None if reading is None else reading.model_dump(mode="json"), missing=d["missing"]
    )


def _select_exit(d: dict[str, Any], ctx: ViewContext) -> RoundCompleteView:
    score_entries = [score_entry_from_dict(s) for s in d.get("candidate_scores") or []]

    # The selected arm's label straight off the round result, never re-chosen by a point estimate
    # here — that could name a different candidate than the one the selector kept, so the verdict
    # line / SCOREBOARD `*` would disagree with the dashboard. ``""`` on a held round.
    winner_label = str(d["winner_label"])
    winner_total = int(d.get("winner_total", 0))

    # Read exactly as ``winner_reference_accuracy`` is read four lines down — the file
    # already knew an accuracy can be absent and applied it to the parent's but not the winner's.
    raw_winner = d.get("winner_accuracy")
    w_acc = None if raw_winner is None else float(raw_winner)
    improved = bool(d.get("improved"))
    parent_acc = ctx.parent_accuracy
    # Matched-pair parent (winner-measured samples). Δ uses this so operator-visible Δ
    # matches the ``improved`` gate, not the full-set comparison that punishes PoBB-locked
    # winners. Absent when the winner did not cover the parent's panel, and it stays absent —
    # falling back to ``parent_acc`` publishes a prefix accuracy minus a full-panel rate.
    raw_matched = d.get("winner_reference_accuracy")
    reference_acc = None if raw_matched is None else float(raw_matched)
    reference_composite = d.get("winner_reference_composite")
    # No Δ without BOTH ends of it. The winner's own rate is the new half of that condition.
    delta = None if reference_acc is None or w_acc is None else w_acc - reference_acc
    p_value: float | None = d.get("p_value")  # computed by l1_score; not recomputed here.
    # The WHOLE reading is emitted, so a cold scale is legible here rather than arriving as a
    # bare float indistinguishable from a warm one — headline `ability` declines the cold case.
    raw_ability = d.get("ability")
    ability_theta = (
        float(t)
        if isinstance(raw_ability, dict)
        and not is_flat_ruler_id(str(raw_ability.get("ruler_id") or ""))
        and isinstance(t := raw_ability.get("theta"), int | float)
        else None
    )
    # An ungraded winner re-anchors NEITHER, which is the same "BOTH move" rule read through its
    # own condition: there is no accuracy to anchor to, so moving the composite alone would put
    # an accuracy Δ against the old parent above a composite Δ against the new one.
    if improved and w_acc is not None:
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
        stamps_theta=bool(d["stamps_theta"]),
        winner_accuracy=w_acc,
        winner_composite_fitness=d.get("winner_composite_fitness"),
        winner_evaluators=dict(d["winner_evaluators"]),
        winner_total=winner_total,
        improved=improved,
        delta=delta,
        p_value=p_value,
        verdict_reason=d.get("verdict_reason"),
        composite_fitness_formula=ctx.composite_fitness_formula,
        composite_fitness_formula_short=ctx.composite_fitness_formula_short,
        reference_accuracy=reference_acc,
        reference_composite=reference_composite,
        headline_metric=ctx.headline_metric,
        ability_theta=ability_theta,
    )


_BUILDERS: dict[str, Any] = {
    f"{CampaignPhase.INIT}:enter": _init_enter,
    f"{CampaignPhase.INIT}:exit": _init_exit,
    f"{CampaignPhase.PROPOSE}:enter": _propose_enter,
    f"{CampaignPhase.PROPOSE}:exit": _propose_exit,
    f"{CampaignPhase.MEASURE}:enter": _measure_enter,
    f"{CampaignPhase.SELECT}:exit": _select_exit,
    f"{CampaignPhase.BENCH}:scored": _bench_scored,
    f"{CampaignPhase.BENCH}:graded": _bench_graded,
}


# --- live entry points ----------------------------------------------------


def from_phase_event(event: PhaseEvent, ctx: ViewContext) -> AnyView | None:
    """A bench phase is built here; an optimizer's own phase arrives with its view as ``step``,
    composed by the member that ran it, since only it knows what its step did."""
    if event.round is not None:
        ctx.round_num = event.round
    builder = _BUILDERS.get(f"{event.phase}:{event.event}")
    if builder is not None:
        view: AnyView = builder(event.data, ctx)
        return view
    step = event.data.get("step")
    return step if isinstance(step, OptimizerStepEnterView | OptimizerStepExitView) else None


# --- score-entry helpers ---


def score_entry_from_dict(s: dict[str, Any]) -> ScoreEntry:
    """``ScoredCandidate`` dict → the narrow renderer row. The interval is the COMPOSITE CI, which brackets the number the
    row reports — the old Wilson pair bracketed a binary hit rate nothing displayed."""
    sc = ScoredCandidate.model_validate(s)
    invalid_reason: str | None = None
    if sc.outcome is ArmOutcome.INVALID and sc.validation_failures:
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
        outcome=sc.outcome,
        invalid_reason=invalid_reason,
        reference_accuracy=sc.reference_accuracy,
        reference_composite=sc.reference_composite,
        theta=sc.theta,
        theta_se=sc.theta_se,
    )
