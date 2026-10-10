"""A view that is only its fields is constructed where it is emitted, not here."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.scoring.evaluators import resolve_cell_formula
from promptpotter.application.views.view_models import ViewContext
from promptpotter.config.settings import settings
from promptpotter.domain.candidate_diff import build_candidate_flat, flatten_sp_summary
from promptpotter.domain.phase_views import (
    CandidatesGeneratedView,
    InitEnterView,
    InitExitView,
    RoundCompleteView,
    RoundStartView,
    RunSpendView,
    SpDiffView,
    WarningEntry,
)
from promptpotter.domain.results import RoundResult, RunStanding
from promptpotter.domain.spend import MeteredSpend
from promptpotter.domain.wounds import collapse_reason
from promptpotter.shared import truncate

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizers.nodes import RoundOpening
    from promptpotter.application.preflight import PreflightWarning
    from promptpotter.domain.sample import Sample

__all__ = [
    "init_enter",
    "init_exit",
    "propose_enter",
    "propose_exit",
    "run_spend_view",
    "select_exit",
]


def init_enter(
    ctx: ViewContext,
    *,
    config: CampaignConfig,
    dataset: Sequence[Sample],
    session: Session,
    warnings: Sequence[PreflightWarning],
) -> InitEnterView:
    schema = session.pipeline_schema
    opt = config.optimization
    selected = select_optimizer(opt)

    ctx.max_rounds = opt.max_rounds or 0
    ctx.patience = opt.convergence_patience
    origin_pp = session.pipeline_params or schema.to_pipeline_params()
    ctx.original_sp_flat = flatten_sp_summary(origin_pp)
    ctx.node_param_keys = {s: sorted(k) for s, k in schema.node_param_keys().items()}
    ctx.run_standing = RunStanding.opening(None if opt.lives is None else opt.lives.bank)

    # Resolved at INIT.enter so the live dashboard stamps the formula before origin scoring fires.
    full, short = resolve_cell_formula(session.scoring.require_scorer().per_cell, schema)
    ctx.composite_fitness_formula = full
    ctx.composite_fitness_formula_short = short

    return InitEnterView(
        warnings=tuple(WarningEntry(title=w.title, detail=w.detail) for w in warnings),
        max_rounds=ctx.max_rounds,
        patience=ctx.patience,
        sp_budget_round=selected.round_cells(len(dataset)),
        dataset_size=len(dataset),
        model=selected.model(),
        composite_fitness_formula=full,
        composite_fitness_formula_short=short,
    )


def init_exit(ctx: ViewContext, *, cycle: Cycle, session: Session) -> InitExitView:
    full, short = resolve_cell_formula(
        session.scoring.require_scorer().per_cell, session.pipeline_schema
    )
    ctx.composite_fitness_formula = full
    ctx.composite_fitness_formula_short = short
    ctx.display_metric = session.scoring.display_metric

    for field_name, value in cycle.opt_sp.prompt_field_dict().items():
        if value:
            ctx.original_sp_flat[field_name] = str(value)

    partition = session.scoring.require_partition()
    return InitExitView(
        origin_acc=cycle.origin_round.accuracy,
        cycle_id_short=(session.state.cycle_id or "?")[:12],
        samples=len(partition.search),
        bench_samples=len(partition.bench),
        origin_samples=len(cycle.origin_round.results),
        obs_on=settings.OBS_ENABLED,
        resumed_from_round=session.state.resumed_from_round,
        # Round 0 is built fresh by `Cycle.start`, so only later rounds count as cached.
        cached_rounds_count=sum(1 for rr in cycle.rounds if rr.round > 0),
        task_context_keys=len(cycle.framing),
        composite_fitness_formula=full,
        composite_fitness_formula_short=short,
    )


def propose_enter(
    ctx: ViewContext,
    *,
    round: int,
    node: str,
    opening: RoundOpening,
    max_rounds: int | None,
    parent_accuracy: float | None,
    prompt_preview: str,
    model: str | None,
    pipeline_params: dict[str, Any] | None,
    parent_prompt_fields: Mapping[str, Any],
) -> RoundStartView:
    # The header renders before candidates exist: it describes the parent.
    preview = prompt_preview.replace("\n", " ").strip()
    preview = "(empty)" if not preview else truncate(preview, 50, "...")

    new_flat = flatten_sp_summary(pipeline_params)
    for field_name, value in parent_prompt_fields.items():
        if value:
            new_flat[field_name] = str(value)
    ctx.current_sp_flat = new_flat
    # Per round, not only at INIT: `change-run-limits` moves the cap between rounds.
    ctx.max_rounds = max_rounds or 0

    return RoundStartView(
        node=node,
        round=round,
        max_rounds=ctx.max_rounds,
        standing=opening.standing,
        parent_accuracy=parent_accuracy,
        prompt_preview=preview,
        arms=opening.arms,
        note=opening.note,
        model=model or "(default)",
        run_standing=ctx.run_standing,
    )


def propose_exit(
    ctx: ViewContext,
    *,
    round: int,
    opening: RoundOpening,
    candidates: Sequence[dict[str, Any]],
    collapses: Mapping[str, int],
    n_scoring_samples: int,
) -> CandidatesGeneratedView:
    parent = ctx.current_sp_flat
    columns: list[tuple[str, dict[str, str]]] = [
        ("Start", dict(ctx.original_sp_flat)),
        ("Parent", dict(parent)),
    ]
    clone_labels: list[str] = []
    for c in candidates:
        label = c["label"]
        flat = build_candidate_flat(parent, c)
        if flat == parent:
            clone_labels.append(label)
        columns.append((label, flat))

    return CandidatesGeneratedView(
        n_candidates=len(candidates),
        source="disk" if opening.replayed else "llm",
        n_scoring_samples=n_scoring_samples,
        clone_labels=tuple(clone_labels),
        sp_diff=SpDiffView(
            columns=tuple(columns),
            node_param_keys=ctx.node_param_keys,
            round_num=round,
            clone_labels=tuple(clone_labels),
            collapses=dict(collapses),
            proposer=opening.proposer,
        ),
    )


def select_exit(ctx: ViewContext, round_result: RoundResult) -> RoundCompleteView:
    """Never re-chosen by a point estimate here, which could name another arm than the one kept."""
    return RoundCompleteView(
        round=round_result.round,
        arms=tuple(
            reading
            for sc, reading in zip(
                round_result.candidate_scores, round_result.arm_readings(), strict=True
            )
            if collapse_reason(sc.validation_failures) is None
        ),
        ended_on=round_result.label,
        accuracy=round_result.accuracy,
        composite_fitness=round_result.composite_fitness,
        evaluators=dict(round_result.evaluators),
        total=round_result.total,
        improved=round_result.improved,
        verdict_reason=round_result.verdict_reason,
        composite_fitness_formula=ctx.composite_fitness_formula,
        composite_fitness_formula_short=ctx.composite_fitness_formula_short,
        display_metric=ctx.display_metric,
    )


def run_spend_view(
    spent: MeteredSpend, *, usd_cap: float | None, token_cap: int | None
) -> RunSpendView:
    return RunSpendView(
        billed_usd=spent.billed_usd,
        rate_priced_usd=spent.rate_priced_usd,
        incurred_usd=spent.incurred_usd,
        meter=spent.meter,
        metered_usd=spent.metered_usd,
        metered_tokens=spent.metered_tokens,
        usd_cap=usd_cap,
        token_cap=token_cap,
    )
