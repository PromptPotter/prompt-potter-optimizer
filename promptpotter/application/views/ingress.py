"""Live ``PhaseEvent → typed View`` ingress. The view rides ``PhaseRecord.payload['view']`` and Pydantic serialises it on
persist + SSE, so nothing downstream hand-rebuilds it."""

from __future__ import annotations

from typing import Any

from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.optimizers.nodes import RoundOpening
from promptpotter.application.scoring.evaluators import resolve_cell_formula
from promptpotter.application.views.view_models import (
    AnyView,
    BenchEnterView,
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
    RunSpendView,
    ScoreEntry,
    SpDiffView,
    VerifyEnterView,
    VerifyGradedView,
    ViewContext,
    WarningEntry,
)
from promptpotter.domain.candidate_diff import build_candidate_flat, flatten_sp_summary
from promptpotter.domain.dashboard_rows import RunStanding
from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.results import ScoredCandidate
from promptpotter.domain.spend import MeteredSpend
from promptpotter.domain.wounds import collapse_reason
from promptpotter.shared import truncate

__all__ = [
    "from_phase_event",
    "run_spend_view",
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
    schema = session.pipeline_schema
    full, short = resolve_cell_formula(session.scoring.scorer_cell_formula, schema)
    ctx.composite_fitness_formula = full
    ctx.composite_fitness_formula_short = short
    ctx.display_metric = session.scoring.display_metric

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
        current_acc=d["current_accuracy"],
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


def _bench_enter(d: dict[str, Any], ctx: ViewContext) -> BenchEnterView:
    return BenchEnterView(
        subject=d["subject"],
        label=str(d["label"]),
        sp_hash=str(d["sp_hash"]),
        round=int(d["graded_round"]),
        rows=int(d["rows"]),
    )


def _bench_scored(d: dict[str, Any], ctx: ViewContext) -> BenchScoredView:
    return BenchScoredView(bench=d["bench"].model_dump(mode="json"))


def _bench_graded(d: dict[str, Any], ctx: ViewContext) -> BenchGradedView:
    reading = d["reading"]
    return BenchGradedView(
        reading=None if reading is None else reading.model_dump(mode="json"),
        missing=d["missing"],
        label=str(d["label"]),
    )


def _verify_enter(d: dict[str, Any], ctx: ViewContext) -> VerifyEnterView:
    return VerifyEnterView(
        label=str(d["label"]),
        round=int(d["candidate_round"]),
        rows=int(d["rows"]),
        strategy=d["strategy"],
    )


def _verify_graded(d: dict[str, Any], ctx: ViewContext) -> VerifyGradedView:
    return VerifyGradedView(
        verify_pass=d["verify_pass"].model_dump(mode="json"),
        reading=d["reading"].model_dump(mode="json"),
    )


def run_spend_view(
    spent: MeteredSpend, *, usd_cap: float | None, token_cap: int | None
) -> RunSpendView:
    """The run-end view. A control record is declared outside ``from_phase_event``, so its
    declarer builds this and hands it to ``declare_run_phase``."""
    return RunSpendView(
        billed_usd=spent.billed_usd,
        incurred_usd=spent.incurred_usd,
        meter=spent.meter,
        metered_usd=spent.metered_usd,
        metered_tokens=spent.metered_tokens,
        usd_cap=usd_cap,
        token_cap=token_cap,
    )


def _select_exit(d: dict[str, Any], ctx: ViewContext) -> RoundCompleteView:
    score_entries = [score_entry(sc) for sc in d["candidate_scores"]]

    # The selected arm's label straight off the round result, never re-chosen by a point estimate
    # here — that could name a different candidate than the one the selector kept, so the verdict
    # line / SCOREBOARD `*` would disagree with the dashboard. ``""`` on a held round.
    winner_label = str(d["winner_label"])
    w_acc: float | None = d["winner_accuracy"]
    # Matched-pair reference (winner-measured samples), so the operator-visible Δ matches the
    # ``improved`` gate. Absent when the winner did not cover its reference's panel, never filled.
    reference_acc: float | None = d["winner_reference_accuracy"]
    # No Δ without BOTH ends of it.
    delta = None if reference_acc is None or w_acc is None else w_acc - reference_acc

    return RoundCompleteView(
        round=ctx.round_num,
        scores=tuple(score_entries),
        winner_label=winner_label,
        stamps_theta=bool(d["stamps_theta"]),
        winner_accuracy=w_acc,
        winner_composite_fitness=d["winner_composite_fitness"],
        winner_evaluators=dict(d["winner_evaluators"]),
        winner_total=int(d["winner_total"]),
        improved=bool(d["improved"]),
        delta=delta,
        p_value=d["p_value"],
        verdict_reason=d["verdict_reason"],
        composite_fitness_formula=ctx.composite_fitness_formula,
        composite_fitness_formula_short=ctx.composite_fitness_formula_short,
        reference_accuracy=reference_acc,
        reference_composite=d["winner_reference_composite"],
        display_metric=ctx.display_metric,
    )


_BUILDERS: dict[str, Any] = {
    f"{CampaignPhase.INIT}:enter": _init_enter,
    f"{CampaignPhase.INIT}:exit": _init_exit,
    f"{CampaignPhase.PROPOSE}:enter": _propose_enter,
    f"{CampaignPhase.PROPOSE}:exit": _propose_exit,
    f"{CampaignPhase.MEASURE}:enter": _measure_enter,
    f"{CampaignPhase.SELECT}:exit": _select_exit,
    f"{CampaignPhase.BENCH}:enter": _bench_enter,
    f"{CampaignPhase.BENCH}:scored": _bench_scored,
    f"{CampaignPhase.BENCH}:graded": _bench_graded,
    f"{CampaignPhase.VERIFY}:enter": _verify_enter,
    f"{CampaignPhase.VERIFY}:graded": _verify_graded,
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


def score_entry(sc: ScoredCandidate) -> ScoreEntry:
    """The narrow renderer row of one arm. The interval is the COMPOSITE CI, which brackets the
    number the row reports."""
    return ScoreEntry(
        label=sc.label,
        accuracy=sc.accuracy,
        composite_fitness=sc.composite_fitness,
        total=sc.total,
        scored=sc.scored_samples,
        expected=sc.expected_samples,
        mean_fitness_ci_lo=sc.mean_fitness_ci_lo,
        mean_fitness_ci_hi=sc.mean_fitness_ci_hi,
        outcome=sc.outcome,
        collapsed_by=collapse_reason(sc.validation_failures),
        reference_accuracy=sc.reference_accuracy,
        reference_composite=sc.reference_composite,
        theta=sc.theta,
        theta_se=sc.theta_se,
    )
