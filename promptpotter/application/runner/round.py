"""One round: the selected manifest's ``default`` pipeline, walked by node type — sampler, the
proposers, the measurement under the eliminator, the selector, the adapters, and the controller at
the boundary — then persisted. The ledger is the sole persistence ingress; bypassing this seam
collapses the round's display AND its audit together."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application import optimizers
from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.bench.round_analysis import compute_round_diagnostics
from promptpotter.application.diagnostics.verify import verify_on_saturation
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizers.nodes import RoundContext, RoundOpening
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.runner.bench import grade_round_selection
from promptpotter.application.runner.measurement import measure_population
from promptpotter.application.runner.output import (
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)
from promptpotter.application.runner.overlap import measure_overlap
from promptpotter.application.runner.termination import BudgetGate, panel_gate_tripped
from promptpotter.application.scoring.metrics import _compute_accuracy
from promptpotter.application.scoring.row_diagnostics import count_degraded_samples
from promptpotter.application.scoring.selection import paired_fitness
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.dashboard_rows import RunStanding
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    CampaignPhase,
    StopLoop,
    StopReason,
    emit_phase,
)
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import (
    CandidateProposal,
    RoundResult,
    candidate_label,
    is_leader_eligible,
    proposal_collapses,
    unscoreable_cells,
)
from promptpotter.domain.results_health import assemble_prior_healths, compute_round_health
from promptpotter.domain.run_records import BenchCheckpointKind, ResumeCheckpointRecord
from promptpotter.domain.scoring import is_unscored
from promptpotter.domain.search_point import strip_rendered_prompt
from promptpotter.infrastructure.llm.telemetry import emit_round_warning
from promptpotter.infrastructure.tracing.bridge import observed_node
from promptpotter.infrastructure.tracing.events import PromptVersion, RoundEnd, RoundStart
from promptpotter.shared.errors import graceful, is_error_result
from promptpotter.shared.statistics import paired_reading

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        Adapter,
        Controller,
        Eliminator,
        Measured,
        Population,
        Proposer,
        Sampler,
        Selection,
        Selector,
    )
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.results import ScoredCandidate
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement

logger = logging.getLogger(__name__)

__all__ = [
    "RoundPlan",
    "announce_opening",
    "announce_population",
    "close_round",
    "emit_origin_round",
    "execute_round",
    "flush_pending_decisions",
    "persist_round",
    "post_round",
    "proposal_summaries",
    "round_plan",
]


@dataclass(frozen=True)
class RoundPlan:
    """The members a manifest's ``default`` pipeline names, by the role their type and position
    give them — nodes before the measurement propose, nodes after the selector adapt."""

    measurement: str
    sampler: Sampler
    proposers: tuple[Proposer, ...]
    eliminator: Eliminator | None
    selector: Selector
    adapters: tuple[Adapter, ...]
    controller: Controller | None

    @property
    def llm_nodes(self) -> list[str]:
        return [m.name for m in (*self.proposers, *self.adapters) if m.kind is NodeKind.LLM]


def round_plan(selected: SelectedOptimizer) -> RoundPlan:
    """Refuses a walk the bench cannot run rather than skipping the node it cannot place."""
    walk = selected.schema.pipelines["default"]
    kinds = [selected.node(name).wire_type for name in walk]
    measured_at = kinds.index(NodeKind.GATEWAY)
    selected_at = next((i for i, kind in enumerate(kinds) if kind is NodeKind.SELECTOR), len(kinds))
    samplers: list[Sampler] = []
    proposers: list[Proposer] = []
    eliminators: list[Eliminator] = []
    selectors: list[Selector] = []
    adapters: list[Adapter] = []
    controllers: list[Controller] = []
    for at, (name, kind) in enumerate(zip(walk, kinds, strict=True)):
        if kind is NodeKind.GATEWAY:
            continue
        member = optimizers.member(name)
        if kind is NodeKind.SAMPLER and at < measured_at:
            samplers.append(cast("Sampler", member))
        elif kind in (NodeKind.LLM, NodeKind.ALGORITHM) and at < measured_at:
            proposers.append(cast("Proposer", member))
        elif kind is NodeKind.ELIMINATOR and at < measured_at:
            eliminators.append(cast("Eliminator", member))
        elif kind is NodeKind.SELECTOR and at > measured_at:
            selectors.append(cast("Selector", member))
        elif kind is NodeKind.LLM and at > selected_at:
            adapters.append(cast("Adapter", member))
        elif kind is NodeKind.CONTROLLER and at > measured_at:
            controllers.append(cast("Controller", member))
        else:
            raise ValueError(
                f"optimizer {selected.name!r}: `default` walks {kind} node {name!r} where the "
                f"bench has no role for it (the {NodeKind.GATEWAY} node is at {measured_at})."
            )
    if len(samplers) != 1 or len(selectors) != 1 or not proposers or len(eliminators) > 1:
        raise ValueError(
            f"optimizer {selected.name!r}: `default` must walk one sampler, at least one "
            "proposer and at most one eliminator before its measurement, and one selector after "
            f"it — it walks {walk}."
        )
    return RoundPlan(
        measurement=walk[measured_at],
        sampler=samplers[0],
        proposers=tuple(proposers),
        eliminator=eliminators[0] if eliminators else None,
        selector=selectors[0],
        adapters=tuple(adapters),
        controller=controllers[0] if controllers else None,
    )


def _separability(round_num: int, electable: list[ScoredCandidate]) -> bool | None:
    # `None` below two shared cells, where there is nothing to be inconclusive ABOUT.
    bracketed = [c for c in electable if c.reference_lift_ci_lo is not None]
    if not bracketed:
        return None
    if any(
        (c.reference_lift_ci_lo or 0.0) > 0.0 or (c.reference_lift_ci_hi or 0.0) < 0.0
        for c in bracketed
    ):
        return True
    widest = max(bracketed, key=lambda c: c.reference_lift_ci_hi or 0.0)
    emit_round_warning(
        kind="round_not_separable",
        message=(
            f"round {round_num} resolved nothing: every one of its {len(bracketed)} readable arms "
            f"has a lift interval spanning 0 (best reaches {widest.reference_lift_ci_hi:+.3f} "
            "at its upper bound). An arm this round selects is the best of what it saw, not a "
            "measured improvement over its reference"
        ),
        detail={"arms": len(bracketed), "best_ci_hi": widest.reference_lift_ci_hi},
    )
    return False


def _round_result(
    ctx: RoundContext,
    population: Population,
    measured: Measured,
    selection: Selection,
    pipeline_params: dict[str, Any] | None,
    *,
    stamps_theta: bool,
) -> RoundResult:
    # A held round's headline is the RETAINED parent re-scored on this panel — never
    # `tracking.current_*`, which unions rows DIFFERENT configurations measured.
    parent = measured.parent
    winner_id = selection.selected_id
    scores = selection.scores
    cs_by_id = {cs.candidate_id: cs for cs in scores}
    params_by_id = {
        ind.lineage.id: pp
        for ind, pp in zip(population.individuals, population.pipeline_params, strict=True)
    }
    best_opt_sp: OptSearchPoint = parent.opt_sp
    best_results: list[QueryMeasurement] = list(measured.parent_rows)
    best_acc, best_comp = parent.report.accuracy, parent.report.composite_fitness
    best_label = parent.report.label
    best_scores: dict[str, float] = dict(parent.report.evaluators)
    if winner_id:
        winner_ind = next(ind for ind in measured.electable if ind.lineage.id == winner_id)
        winner_cs = cs_by_id[winner_id]
        best_acc, best_comp = winner_cs.accuracy, winner_cs.composite_fitness
        best_opt_sp = winner_ind
        best_results = list(measured.rows[winner_id])
        best_label = winner_ind.lineage.changes_description or winner_ind.lineage.id[:12]
        best_scores = dict(winner_cs.evaluators)
    base = _compute_accuracy(best_results)
    p_value: float | None = None
    winner_reference = cs_by_id[winner_id].reference_id if winner_id else None
    if base["total"] > 0 and winner_reference is not None:
        # A recorded diagnostic; it gates nothing. Significance runs on the per-sample FITNESS
        # rather than binary hits, TWO-SIDED to match the winner's `reference_lift_ci_*` beside it.
        cand_fit, parent_fit = paired_fitness(
            best_results, measured.references[winner_reference], grade="fitness"
        )
        _d, _lo, _hi, p_value, _n = paired_reading(cand_fit, parent_fit)
    return RoundResult(
        round=ctx.round_num,
        label=best_label,
        accuracy=best_acc,
        composite_fitness=best_comp,
        # The headline sample count rides the winner's row, so the round header and the
        # per-candidate table cannot disagree.
        total=cs_by_id[winner_id].total if winner_id else base["total"],
        # Cells of the winner's panel never sent. An eliminator stop is one legitimate reason for
        # it, so this is REPORTED here and graded only on the origin.
        not_attempted=(
            max(0, cs_by_id[winner_id].expected_samples - cs_by_id[winner_id].scored_samples)
            if winner_id
            else 0
        ),
        # Counted off the rows: an ungraded cell WAS sent and WAS measured, so it is already
        # inside `scored_samples` and no subtraction can find it.
        unscored=sum(1 for r in best_results if is_unscored(r)),
        improved=bool(winner_id),
        p_value=p_value,
        verdict_reason=selection.verdict_reason,
        stamps_theta=stamps_theta,
        # Over the whole electable field, not the winner's own interval: the question is whether
        # THIS ROUND told the arms apart, and one arm's bracket cannot answer that.
        separable=_separability(
            ctx.round_num, [cs_by_id[ind.lineage.id] for ind in measured.electable]
        ),
        prompt_fields=best_opt_sp.prompt_field_dict(),
        # Stripped, because the round's incoming params carry the PREVIOUS winner's render and
        # nothing re-renders at this write; every reader rebuilds the render from `prompt_fields`.
        pipeline_params=strip_rendered_prompt(
            params_by_id.get(winner_id, pipeline_params) if winner_id else pipeline_params
        ),
        results=cast("list[dict[str, Any]]", best_results),
        all_candidate_results=cast("dict[str, list[dict[str, Any]]]", dict(measured.rows)),
        # Banked with the arms read against them: every scalar this round stamps about a
        # reference is read off exactly these rows.
        reference_results={
            rid: cast("list[dict[str, Any]]", list(rows))
            for rid, rows in measured.references.items()
        },
        candidates_scored=len(measured.scored),
        electable_count=len(measured.electable),
        candidate_scores=scores,
        selected_labels=[cs_by_id[winner_id].label] if winner_id else [],
        degraded_samples=count_degraded_samples(best_results),
        deprecated=base["deprecated"],
        evaluators=best_scores,
        opt_sp=best_opt_sp,
        optimizer_state=selection.optimizer_state,
    )


def _panel_gate(ctx: RoundContext, round_result: RoundResult) -> None:
    session = ctx.cycle.session
    round_num = ctx.round_num
    # A broken arm also carries error rows, but its own outcome already charges it.
    holed = sorted(
        c.candidate_id
        for c in round_result.candidate_scores
        if is_leader_eligible(c)
        and unscoreable_cells(round_result.all_candidate_results.get(c.candidate_id) or [])
    )
    # Sink is the LEDGER: the halt below unwinds before `persist_round`, so a round-local record
    # would never reach disk on exactly the round it is evidence about. ARCHIVAL by gating.
    ledger = session.state.ledger
    assert ledger is not None, "build_run_observers must bind state.ledger before a round runs"
    record_decision(
        ledger,
        BenchCheckpointKind.PANEL_COVERAGE,
        {
            "candidate_ids": [c.candidate_id for c in round_result.candidate_scores],
            "round_num": round_num,
        },
        holed,
        node=None,
        data={
            "holes_by_candidate": {
                c.candidate_id: unscoreable_cells(
                    round_result.all_candidate_results.get(c.candidate_id) or []
                )
                for c in round_result.candidate_scores
            }
        },
        round=round_num,
    )
    # Which of the two resumable halts this is depends on whether a declared bound cut the cell,
    # so the gate reads the holed ROWS, not their candidates' ids.
    holed_rows = [
        (cid, row)
        for cid in holed
        for row in round_result.all_candidate_results.get(cid) or []
        if is_error_result(row)
    ]
    opt = ctx.cycle.config.optimization
    if (reason := panel_gate_tripped([row for _, row in holed_rows], opt.panel_gate)) is None:
        return
    for cid, row in holed_rows:
        logger.warning(
            "round %d panel HOLE: candidate %s, sample %s — %s",
            round_num,
            cid,
            row.get("sample_id"),
            row.get("error") or row.get("error_category"),
        )
    logger.warning(
        "Round %d halted BEFORE electing on an incomplete panel: %d of %d electable "
        "candidate(s) carry cells that returned no measurement. The round is not persisted "
        "— a resume re-runs it, replays the cached candidates and re-measures the holes. %s",
        round_num,
        len(holed),
        sum(1 for c in round_result.candidate_scores if is_leader_eligible(c)),
        STOP_REASON_INFO[reason].next_step,
    )
    raise StopLoop(reason)


def proposal_summaries(proposals: list[CandidateProposal], round_num: int) -> list[dict[str, Any]]:
    """Each proposal as the ``propose:exit`` event and a replayed generation's call record name it."""
    summaries = []
    for i, cp in enumerate(proposals):
        prompt_fields = cp.opt_sp.prompt_fields()
        summary: dict[str, Any] = {
            "idx": i,
            "label": candidate_label(round_num, i),
            "changes_description": cp.opt_sp.lineage.changes_description or "",
        }
        if cp.pipeline_overlay:
            summary["pipeline_overlay"] = cp.pipeline_overlay
        if prompt_fields:
            summary["prompt_fields"] = prompt_fields
        summaries.append(summary)
    return summaries


def announce_opening(ctx: RoundContext, node: str) -> RoundOpening:
    """Open the round's proposing on the ledger, in the bench's words and the optimizer's own."""
    cycle = ctx.cycle
    assert cycle.tracking.current_sp is not None
    opening = cycle.optimizer.runtime.opening(ctx)
    emit_phase(
        ctx.callbacks.on_phase,
        CampaignPhase.PROPOSE,
        "enter",
        round=ctx.round_num,
        node=node,
        max_rounds=cycle.config.optimization.max_rounds,
        current_accuracy=cycle.tracking.current_accuracy,
        prompt_preview=cycle.opt_sp.render()[:120],
        model=cycle.optimizer.model(),
        opening=opening,
        pipeline_params=cycle.tracking.current_sp.pipeline_params,
        parent_prompt_fields={k: v for k, v in cycle.opt_sp.prompt_field_dict().items() if v},
    )
    return opening


def announce_population(
    ctx: RoundContext, opening: RoundOpening, proposals: list[CandidateProposal], n_cells: int
) -> None:
    """Close the round's proposing on the ledger: what was proposed, and what collapsed."""
    emit_phase(
        ctx.callbacks.on_phase,
        CampaignPhase.PROPOSE,
        "exit",
        round=ctx.round_num,
        opening=opening,
        n_scoring_samples=n_cells,
        candidates=proposal_summaries(proposals, ctx.round_num),
        collapses=proposal_collapses(proposals),
    )


async def _adapt(ctx: RoundContext, plan: RoundPlan, round_result: RoundResult) -> None:
    if not plan.adapters:
        return
    emit_phase(ctx.callbacks.on_phase, CampaignPhase.ADAPT, "enter", round=ctx.round_num)
    for adapter in plan.adapters:
        await adapter.adapt(ctx, round_result)
    emit_phase(ctx.callbacks.on_phase, CampaignPhase.ADAPT, "exit", round=ctx.round_num)


async def execute_round(
    cycle: Cycle,
    round_num: int,
    pool: list[Sample],
    callbacks: RunCallbacks,
    *,
    is_final_round: bool = False,
) -> tuple[RoundResult, StopReason | None]:
    """The round, and the budget stop that cut its measurement short — a cut round is the run's
    last. The runner folds the result in via ``absorb_round``; this never mutates ``Cycle``. On
    the final round the adapters are skipped: they write for a NEXT round."""
    session = cycle.session
    obs = session.state.obs
    plan = round_plan(cycle.optimizer)
    ctx = RoundContext(
        cycle=cycle, round_num=round_num, callbacks=callbacks, is_final_round=is_final_round
    )
    if obs:
        with graceful("RoundStart emit failed"):
            obs.emit(RoundStart(campaign_id=session.state.tracing_campaign_id, round_num=round_num))

    panel = plan.sampler.draw(ctx, pool)
    opening = announce_opening(ctx, plan.proposers[0].name)
    population: Population | None = None
    for proposer in plan.proposers:
        population = await proposer.propose(ctx, panel, population)
    assert population is not None and cycle.tracking.current_sp is not None
    announce_population(ctx, opening, population.proposals, len(panel.cells))

    emit_phase(
        callbacks.on_phase,
        CampaignPhase.MEASURE,
        "enter",
        round=round_num,
        node=plan.measurement,
        n_candidates=len(population.proposals),
        n_samples=len(panel.cells),
        current_best_accuracy=cycle.tracking.current_accuracy,
        current_pipeline_params=cycle.tracking.current_sp.pipeline_params,
    )
    async with observed_node(
        f"{plan.measurement}_r{round_num}",
        "scoring",
        obs=obs,
        as_type="span",
        campaign_id=session.state.tracing_campaign_id,
        round_num=round_num,
    ):
        measured = await measure_population(ctx, population, panel, plan.eliminator, plan.selector)
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.MEASURE,
        "exit",
        round=round_num,
        n_scored=len(measured.scored),
        n_electable=len(measured.electable),
    )
    emit_phase(callbacks.on_phase, CampaignPhase.SELECT, "enter", round=round_num)
    selection = plan.selector.select(ctx, measured, population)
    round_result = _round_result(
        ctx,
        population,
        measured,
        selection,
        cycle.tracking.current_sp.pipeline_params,
        stamps_theta=plan.selector.stamps_theta,
    )
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.SELECT,
        "exit",
        round=round_num,
        winner_label=next(iter(round_result.selected_labels), ""),
        stamps_theta=plan.selector.stamps_theta,
        winner_accuracy=round_result.accuracy,
        winner_composite_fitness=round_result.composite_fitness,
        winner_evaluators=dict(round_result.evaluators),
        winner_total=round_result.total,
        improved=round_result.improved,
        verdict_reason=round_result.verdict_reason,
        p_value=round_result.p_value,
        candidate_scores=[c.model_dump() for c in round_result.candidate_scores],
        winner_reference_accuracy=next(
            (s.reference_accuracy for s in round_result.selected_scores), None
        ),
        winner_reference_composite=next(
            (s.reference_composite for s in round_result.selected_scores), None
        ),
    )
    _panel_gate(ctx, round_result)
    # The election, banked AFTER the panel gate: a round halted on a holed panel is unwound and
    # re-run, so crowning it would put a winner on the timeline for a round that never stood.
    callbacks.on_election(round_result)

    # The 1-to-1 series, measured WHILE the adapters run — every decision this round makes is
    # already made, and the fields it writes sit outside `results` / `all_candidate_results`.
    overlap = asyncio.create_task(measure_overlap(cycle, round_result, pool))
    try:
        round_result.diagnostics = compute_round_diagnostics(
            round_result, [*cycle.rounds, round_result], session.pipeline_schema
        )
        # A zero-candidate round leaves `results` holding the parent's rows: there is nothing to
        # adapt to. And an adapter writes for the NEXT round, so none runs when no round follows —
        # the calendar cap knows that before the round, the controller only now.
        will_stop = (
            is_final_round
            or measured.cut is not None
            or (plan.controller is not None and plan.controller.stops_after(ctx, round_result))
        )
        if population.proposals and round_result.results and not will_stop:
            await _adapt(ctx, plan, round_result)
    except BaseException:
        overlap.cancel()
        raise
    await overlap
    winner_opt_sp = round_result.opt_sp
    assert winner_opt_sp is not None
    if obs:
        with graceful("RoundEnd emit failed"):
            obs.emit(
                RoundEnd(
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    accuracy=round_result.accuracy,
                    total=round_result.total,
                    improved=round_result.improved,
                    winner_lineage_id=winner_opt_sp.lineage.id,
                    candidate_scores=[c.model_dump() for c in round_result.candidate_scores],
                    model=cycle.optimizer.model(),
                    n_candidates=len(population.proposals),
                    optimizer_templates=plan.llm_nodes,
                    evaluators=dict(round_result.evaluators),
                )
            )
        with graceful("PromptVersion emit failed"):
            obs.emit(
                PromptVersion(
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    lineage_id=winner_opt_sp.lineage.id,
                    rendered_prompt=winner_opt_sp.render(),
                    layer1_fields={f: getattr(winner_opt_sp, f) for f in PROMPT_STRING_FIELDS},
                    parent_ids=tuple(winner_opt_sp.lineage.parent_ids),
                )
            )
    return round_result, measured.cut


async def emit_origin_round(cycle: Cycle, session: Session, cb: RunCallbacks) -> None:
    """Close **round 0**, the origin's measurement, through the standard path — the adapters
    first, then ``close_round``, so the round file, ``index.json`` and ``dashboard.json`` carry
    one shape. Diag forks inherit round 0 and never reach here, so round 1 stays bit-identical
    across cheap forks."""
    round_result = cycle.origin_round
    # Without the adapters' pass over the origin's misses, round 1 opens blind to the per-sample
    # failure pattern and falls back to surface-axis guesses.
    if round_result.results:
        round_result.diagnostics = compute_round_diagnostics(
            round_result, [round_result], session.pipeline_schema
        )
        await _adapt(
            RoundContext(cycle=cycle, round_num=0, callbacks=cb),
            round_plan(cycle.optimizer),
            round_result,
        )

    # Round 0 elects too: it adopts C0. Emitted here rather than from `close_round`, which round
    # 0 reaches TWICE — the second time carrying the warm ruler's θ.
    cb.on_election(round_result)
    # And it spends the look-ahead arming, exactly as round N's measurement does — one rule, at
    # the two places a round elects. Unspent here, a press during the origin's scoring outlives
    # the round it paid for and silently widens round 1 too.
    if session.sample_lookahead_consume is not None:
        session.sample_lookahead_consume()
    await close_round(cycle, round_result, 0, session, cb)


def persist_round(
    cycle: Cycle,
    round_result: RoundResult,
    session: Session,
    cb: RunCallbacks,
) -> None:
    """The ledger emit is unconditional — every completed round lands on the ledger."""
    # ONE destination. Every pending record goes to the ledger, which is its chronological
    # place and the only home it needs: each carries its own ``round`` stamp, so the round that
    # MADE a decision is a fact about the record rather than about when it happened to be
    # flushed. Assemble no second copy onto the round document: a record stamped for a round
    # whose file is already written reaches no document at all.
    flushed: list[ResumeCheckpointRecord] = []
    if cycle.pending_decisions:
        flushed = list(cycle.pending_decisions)
        cycle.pending_decisions.clear()

    if (ledger := session.state.ledger) is not None:
        for d in flushed:
            ledger.append(d)
        # The close IS the document's address: fold the ledger to this offset and you get
        # exactly this round. Stamped between the emit and the write, the only window where
        # both are true.
        round_result.at_offset = cb.on_round_close(round_result)

    if session.state.cycle_id:
        with graceful("Round checkpoint failed"):
            session.store.campaigns.save_round_file(
                session.hop,
                round_result,
            )
        write_hard_samples_artifacts(session, cycle)
        write_log_md(session, cycle.config)
        write_review_md(session, cycle)

    if _rr := session.state.audit_projection:
        _rr.flush()


def flush_pending_decisions(cycle: Cycle, session: Session) -> int:
    """Teardown's half of ``persist_round``'s drain — the ledger only, since no round is closing.

    The controller acts AFTER the round it belongs to has already persisted, so its decision waits
    for the next ``persist_round``. A cycle that stops right there — max_rounds, a terminate
    proposal, a spend halt, Ctrl+C — has no next round, and an unflushed record is an act a
    resume re-spends.
    """
    if not cycle.pending_decisions or (ledger := session.state.ledger) is None:
        return 0
    pending = list(cycle.pending_decisions)
    cycle.pending_decisions.clear()
    with graceful("Decision flush failed"):
        for d in pending:
            ledger.append(d)
    return len(pending)


async def close_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    session: Session,
    cb: RunCallbacks,
) -> None:
    """Round-completion bookkeeping, and the SINGLE degradation-verdict compute site (origin and
    every later round funnel here): ``health`` is stamped BEFORE the dashboard emit and the
    round-file write."""
    if cycle.origin_restamped:
        # A ruler that warmed this round gave round 0 the θ it could not have had at its own
        # close; unsaved, every non-live reader shows a θ-less C0 beside candidates that have one.
        cycle.origin_restamped = False
        persist_round(cycle, cycle.origin_round, session, cb)
    round_result.health = compute_round_health(
        results=round_result.results,
        prior_healths=assemble_prior_healths(cycle.rounds, round_num),
        is_origin=round_num == 0,
        not_attempted=round_result.not_attempted,
        unscored=round_result.unscored,
    )
    round_result.optimizer_facts = cycle.optimizer.runtime.round_facts(
        cycle.optimizer, round_result
    )
    stall, stalls_left = cycle.working_state.standing()
    bank = cycle.optimizer.pacing.stalls_left
    standing = RunStanding(
        rounds_without_advance=stall,
        stalls_left=stalls_left,
        stalls_left_cap=None if bank is None else bank[1],
    )
    cb.on_round_complete(round_result, standing)
    persist_round(cycle, round_result, session, cb)
    if cycle.sample_index is not None and session.store:
        cycle.sample_index.refresh(
            session.store,
            scorer=session.scoring.require_scorer(),
            scorer_id=session.scoring.scorer_id,
            dataset_name=session.dataset_name,
        )


async def post_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    session: Session,
    cb: RunCallbacks,
    budget_gate: BudgetGate,
    *,
    is_final_round: bool = False,
) -> None:
    """The boundary: the controller reads the closed round, the round is persisted, and the
    controller stops the run or acts on its reading. ``is_final_round`` withholds the act — what
    it writes is read by a NEXT round that never comes. Raises ``StopLoop`` on a stop."""
    controller = round_plan(cycle.optimizer).controller
    ctx = RoundContext(
        cycle=cycle, round_num=round_num, callbacks=cb, is_final_round=is_final_round
    )
    boundary = controller.observe(ctx, round_result) if controller is not None else None

    await close_round(cycle, round_result, round_num, session, cb)

    # After close_round and before the stop below, so a perfect round that also ends the campaign
    # still gets its check. Bounded and never fatal — the model is in ``verify_on_saturation``.
    await verify_on_saturation(
        stores=session.store,
        identity=session.identity,
        hop=CycleHop(campaign_id=session.campaign_id, cycle_id=session.state.cycle_id),
        round_num=round_num,
        accuracy=round_result.accuracy,
        winner_label=next(iter(round_result.selected_labels), None),
        budget=budget_gate,
        log=logger.info,
    )
    await grade_round_selection(cycle, session, round_result, cb=cb)

    if boundary is None:
        return
    if boundary.stop is not None:
        raise StopLoop(boundary.stop)
    if not is_final_round and boundary.act:
        assert controller is not None
        await controller.act(ctx)
