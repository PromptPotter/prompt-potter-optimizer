"""A view that is only its fields is constructed where it is emitted, not here."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.scoring.evaluators import resolve_cell_formula
from promptpotter.application.views.view_models import ViewContext
from promptpotter.config.settings import settings
from promptpotter.domain.candidate_diff import (
    build_candidate_flat,
    flatten_prompts,
    flatten_sp_summary,
)
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
from promptpotter.domain.results import (
    Proposal,
    RoundResult,
    RunStanding,
    candidate_label,
)
from promptpotter.domain.spend import Budget, MeteredSpend
from promptpotter.domain.wounds import collapse_reason
from promptpotter.shared.answer_text import truncate

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizers.nodes import RoundOpening
    from promptpotter.application.preflight import PreflightWarning
    from promptpotter.domain.opt_search_point import NodePrompt
    from promptpotter.domain.sample import Sample


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

    ctx.patience = opt.convergence_patience
    origin_pp = session.pipeline_params or schema.to_pipeline_params()
    ctx.original_sp_flat = flatten_sp_summary(origin_pp)
    ctx.node_param_keys = {s: sorted(k) for s, k in schema.node_param_keys().items()}

    # Resolved at INIT.enter so the live dashboard stamps the formula before origin scoring fires.
    full, short = resolve_cell_formula(session.state.scoring.require_scorer().per_cell, schema)
    ctx.composite_fitness_formula = full
    ctx.composite_fitness_formula_short = short

    return InitEnterView(
        warnings=tuple(WarningEntry(title=w.title, detail=w.detail) for w in warnings),
        patience=ctx.patience,
        sp_budget_round=selected.round_cells(len(dataset)),
        model=selected.model(),
        composite_fitness_formula=full,
        composite_fitness_formula_short=short,
    )


def init_exit(ctx: ViewContext, *, cycle: Cycle, session: Session) -> InitExitView:
    full, short = resolve_cell_formula(
        session.state.scoring.require_scorer().per_cell, session.pipeline_schema
    )
    ctx.composite_fitness_formula = full
    ctx.composite_fitness_formula_short = short
    ctx.display_metric = session.state.scoring.display_metric

    ctx.original_sp_flat.update(flatten_prompts(cycle.parent.prompts))

    partition = session.state.scoring.require_partition()
    return InitExitView(
        origin_acc=None if (fold := cycle.origin_round.fold) is None else fold.accuracy,
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
    budget_rounds: int | None,
    parent_accuracy: float | None,
    prompt_preview: str,
    model: str | None,
    pipeline_params: dict[str, Any] | None,
    parent_prompts: Mapping[str, NodePrompt],
    run_standing: RunStanding,
) -> RoundStartView:
    # The header renders before candidates exist: it describes the parent.
    preview = prompt_preview.replace("\n", " ").strip()
    preview = "(empty)" if not preview else truncate(preview, 50, "...")
    ctx.current_sp_flat = flatten_sp_summary(pipeline_params) | flatten_prompts(parent_prompts)

    return RoundStartView(
        node=node,
        round=round,
        budget_rounds=budget_rounds,
        standing=opening.standing,
        parent_accuracy=parent_accuracy,
        prompt_preview=preview,
        arms=opening.arms,
        note=opening.note,
        model=model or "(default)",
        run_standing=run_standing,
    )


def propose_exit(
    ctx: ViewContext,
    *,
    round: int,
    opening: RoundOpening,
    proposals: Sequence[Proposal],
    collapses: Mapping[str, int],
) -> CandidatesGeneratedView:
    parent = ctx.current_sp_flat
    columns: list[tuple[str, dict[str, str]]] = [
        ("Start", dict(ctx.original_sp_flat)),
        ("Parent", dict(parent)),
    ]
    clone_labels: list[str] = []
    for i, cp in enumerate(proposals):
        label = candidate_label(round, i)
        flat = build_candidate_flat(
            parent,
            pipeline_overlay=cp.overlay,
            prompt_flat=flatten_prompts(cp.individual.prompts),
        )
        if flat == parent:
            clone_labels.append(label)
        columns.append((label, flat))

    return CandidatesGeneratedView(
        n_candidates=len(proposals),
        source="disk" if opening.replayed else "llm",
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
            row.reading
            for row in round_result.ranked()
            if collapse_reason(round_result.arms[row.reading.arm.key].validation_failures) is None
        ),
        ended_on="" if (head := round_result.head) is None else head.label,
        fold=round_result.fold,
        improved=round_result.improved,
        verdict_reason=None
        if (election := round_result.election) is None
        else election.verdict_reason,
        composite_fitness_formula=ctx.composite_fitness_formula,
        composite_fitness_formula_short=ctx.composite_fitness_formula_short,
        display_metric=ctx.display_metric,
    )


def run_spend_view(
    spent: MeteredSpend,
    *,
    campaign_metered: Budget,
    usd_cap: float | None,
    token_cap: int | None,
    share: Budget,
) -> RunSpendView:
    return RunSpendView(
        billed_usd=spent.billed_usd,
        rate_priced_usd=spent.rate_priced_usd,
        incurred_usd=spent.incurred_usd,
        meter=spent.meter,
        metered_usd=spent.metered_usd,
        metered_tokens=spent.metered_tokens,
        campaign_metered_usd=campaign_metered.usd_amount,
        campaign_metered_tokens=campaign_metered.token_amount,
        usd_cap=usd_cap,
        token_cap=token_cap,
        usd_share=share.usd,
        token_share=share.tokens,
    )
