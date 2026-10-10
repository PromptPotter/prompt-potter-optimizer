from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application import optimizers
from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.bench.round_analysis import compute_round_diagnostics
from promptpotter.application.diagnostics.verify import verify_on_saturation
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizers.nodes import (
    Proposals,
    RoundContext,
    RoundOpening,
    round_state,
)
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.runner.bench import grade_round_selection, reserve_selection_pass
from promptpotter.application.runner.measurement import measure_population
from promptpotter.application.runner.output import (
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)
from promptpotter.application.runner.overlap import measure_overlap
from promptpotter.application.runner.termination import panel_gate_tripped, standing_tripped
from promptpotter.application.scoring.candidate_report import arm_id, fatal_validation_failures
from promptpotter.application.scoring.row_diagnostics import count_degraded_samples
from promptpotter.application.views.ingress import propose_enter, propose_exit, select_exit
from promptpotter.application.views.render.primitives import overlap_series
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.l4.proxies import panel_precision
from promptpotter.domain.paired_reading import READING_STATE_INFO, ReadingState
from promptpotter.domain.phase_views import MeasureEnterView
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    CampaignPhase,
    StopLoop,
    StopReason,
)
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import (
    ROUND_ADVANCE_INFO,
    CandidateProposal,
    OverlapReading,
    RoundAdvance,
    RoundResult,
    RunStanding,
    ScoredCandidate,
    candidate_label,
    is_leader_eligible,
    recall_at,
    stalls_left,
    unscoreable_cells,
)
from promptpotter.domain.results_health import (
    assemble_prior_healths,
    compute_round_health,
    is_deprecated,
)
from promptpotter.domain.run_records import (
    BenchCheckpointKind,
    CandidateMintedRecord,
    ResumeCheckpointRecord,
)
from promptpotter.domain.scoring import NO_CELLS
from promptpotter.domain.wounds import collapse_counts
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.llm.telemetry import emit_round_warning
from promptpotter.infrastructure.store.campaign_store.ledger_scan import close_spend
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        Adapter,
        Controller,
        Eliminator,
        Measured,
        Panel,
        Proposer,
        Sampler,
        Selection,
        Selector,
    )
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.results import DisplayMetric
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet

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
    "propose_population",
    "round_plan",
]


INITIAL_POPULATION = "initial_population"


@dataclass(frozen=True)
class RoundPlan:
    measurement: str
    sampler: Sampler
    seeders: tuple[Proposer, ...]
    proposers: tuple[Proposer, ...]
    eliminator: Eliminator | None
    selector: Selector
    adapters: tuple[Adapter, ...]
    controller: Controller | None

    @property
    def llm_nodes(self) -> list[str]:
        return [m.name for m in (*self.proposers, *self.adapters) if m.kind is NodeKind.LLM]


def round_plan(selected: SelectedOptimizer) -> RoundPlan:
    walk = selected.schema.pipelines["default"]
    kinds = [selected.node(name).kind for name in walk]
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
    if misplaced := [p.name for at, p in enumerate(proposers) if p.opens is not (at == 0)]:
        raise ValueError(
            f"optimizer {selected.name!r}: `default` must walk a proposer that makes the round's "
            f"population first and only ones editing it after — {misplaced} of "
            f"{[p.name for p in proposers]} are out of place."
        )
    return RoundPlan(
        measurement=walk[measured_at],
        sampler=samplers[0],
        seeders=tuple(
            cast("Proposer", optimizers.member(name))
            for name in selected.schema.pipelines.get(INITIAL_POPULATION, ())
        ),
        proposers=tuple(proposers),
        eliminator=eliminators[0] if eliminators else None,
        selector=selectors[0],
        adapters=tuple(adapters),
        controller=controllers[0] if controllers else None,
    )


def _warn_not_advanced(round_result: RoundResult) -> None:
    overlap = round_result.overlap
    if overlap.advance not in (RoundAdvance.NOT_SEPARATED, RoundAdvance.UNREAD):
        return
    lead = overlap.lead
    evidence = (
        READING_STATE_INFO[lead.state].sentence
        if lead.headline is None
        else f"lift interval [{lead.headline.estimate.ci_lo:+.3f}, "
        f"{lead.headline.estimate.ci_hi:+.3f}] on {overlap_series(overlap)}"
    )
    emit_round_warning(
        kind="round_not_advanced",
        message=(
            f"round {round_result.round} selected {round_result.label}: "
            f"{ROUND_ADVANCE_INFO[overlap.advance].sentence} {evidence}"
        ),
        detail={"advance": overlap.advance.value, "lead_state": lead.state.value},
    )


def _round_result(
    ctx: RoundContext,
    measured: Measured,
    selection: Selection,
    *,
    elects_on: DisplayMetric,
) -> RoundResult:
    # A held round headlines the parent RE-SCORED on this panel, never `tracking.current_*`, a union.
    parent = measured.parent
    winner_id = selection.selected_id
    scores = selection.scores
    cs_by_id = {cs.candidate_id: cs for cs in scores}
    if winner_id and selection.leading_id != winner_id:
        raise ValueError(
            f"round {ctx.round_num}: the selector kept {winner_id[:12]} and named "
            f"{selection.leading_id[:12] or 'no arm'} as the one the round is read off"
        )
    elected: ScoredCandidate = parent.report
    best_opt_sp: OptSearchPoint = parent.opt_sp
    best_results: CellSheet = measured.parent_rows
    if winner_id:
        elected = cs_by_id[winner_id]
        best_opt_sp = next(ind for ind in measured.electable if ind.id == winner_id)
        best_results = measured.rows[winner_id]
    lift_reference = ctx.cycle.config.optimization.lift_reference
    not_attempted = max(0, elected.expected_samples - elected.scored_samples) if winner_id else 0
    # Counted off the rows: an ungraded cell is already inside `scored_samples`.
    unscored = sum(1 for cell in best_results if cell.grade.unscored is not None)
    cycle = ctx.cycle
    elected_round = RoundResult(
        round=ctx.round_num,
        label=elected.label,
        accuracy=elected.accuracy,
        composite_fitness=elected.composite_fitness,
        total=elected.total,
        not_attempted=not_attempted,
        unscored=unscored,
        health=compute_round_health(
            results=best_results.cells,
            prior_healths=assemble_prior_healths(ctx.cycle.rounds, ctx.round_num),
            not_attempted=not_attempted,
            unscored=unscored,
        ),
        improved=bool(winner_id),
        # Until `measure_overlap` lands, after every pick this round makes.
        overlap=OverlapReading.unpaired(ReadingState.PENDING, ctx.round_num, bool(winner_id)),
        reference_rule=lift_reference,
        verdict_reason=selection.verdict_reason,
        elects_on=elects_on,
        prompt_fields=best_opt_sp.prompt_field_dict(),
        pipeline_params=best_opt_sp.pipeline_params,
        results=best_results,
        all_candidate_results=dict(measured.rows),
        reference_results=dict(measured.references),
        candidates_scored=len(measured.scored),
        electable_count=len(measured.electable),
        candidate_scores=scores,
        selected_labels=[elected.label] if winner_id else [],
        leading_label=cs_by_id[selection.leading_id].label if selection.leading_id else None,
        degraded_samples=count_degraded_samples(best_results.cells),
        deprecated=sum(1 for cell in best_results if is_deprecated(cell.facts)),
        recall_at=recall_at(best_results),
        ability=cycle.ability_after(best_results),
        evaluators=dict(elected.evaluators),
        # A HELD round ends on the parent the cycle already stands on, lineage and all.
        opt_sp=best_opt_sp if best_opt_sp.id != cycle.opt_sp.id else cycle.opt_sp,
        optimizer_state=round_state(
            cycle.optimizer, cycle.working_state.round_payload(), cycle.population
        ),
    )
    diagnostics = compute_round_diagnostics(
        elected_round, [*cycle.rounds, elected_round], cycle.session.pipeline_schema
    )
    return elected_round.model_copy(update={"diagnostics": diagnostics})


def _panel_gate(ctx: RoundContext, round_result: RoundResult) -> None:
    session = ctx.cycle.session
    round_num = ctx.round_num
    arm_rows = round_result.all_candidate_results
    # A broken arm also carries error rows, but its own outcome already charges it.
    holed = sorted(
        c.candidate_id
        for c in round_result.candidate_scores
        if is_leader_eligible(c) and unscoreable_cells(arm_rows.get(c.candidate_id, NO_CELLS))
    )
    # Sink is the LEDGER: the halt below unwinds before `persist_round`.
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
                c.candidate_id: unscoreable_cells(arm_rows.get(c.candidate_id, NO_CELLS))
                for c in round_result.candidate_scores
            }
        },
        round=round_num,
    )
    holed_rows = [(cid, cell) for cid in holed for cell in arm_rows[cid] if cell.facts.errored]
    opt = ctx.cycle.config.optimization
    if (reason := panel_gate_tripped([cell for _, cell in holed_rows], opt.panel_gate)) is None:
        return
    for cid, cell in holed_rows:
        logger.warning(
            "round %d panel HOLE: candidate %s, sample %s — %s",
            round_num,
            cid,
            cell.sample_id,
            cell.facts.error or cell.facts.error_category,
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


def announce_opening(ctx: RoundContext) -> RoundOpening:
    cycle = ctx.cycle
    assert cycle.tracking.current_sp is not None
    opening = cycle.optimizer.runtime.opening(ctx)
    cb = ctx.callbacks
    cb.on_phase(
        CampaignPhase.PROPOSE,
        "enter",
        round=ctx.round_num,
        view=propose_enter(
            cb.view_context,
            round=ctx.round_num,
            node=cycle.optimizer.proposer,
            opening=opening,
            max_rounds=cycle.config.optimization.max_rounds,
            parent_accuracy=cycle.tracking.current_accuracy,
            prompt_preview=cycle.opt_sp.render()[:120],
            model=cycle.optimizer.model(),
            pipeline_params=cycle.tracking.current_sp.pipeline_params,
            parent_prompt_fields=cycle.opt_sp.prompt_field_dict(),
        ),
    )
    return opening


def announce_population(
    ctx: RoundContext, opening: RoundOpening, proposals: list[CandidateProposal], n_cells: int
) -> None:
    """A mint is an EDGE SET: a held individual is minted again only for a parent no earlier mint names."""
    if (ledger := ctx.cycle.session.state.ledger) is not None:
        minted: dict[str, set[str]] = {}
        for _, rec in ledger.iter():
            if isinstance(rec, CandidateMintedRecord):
                minted.setdefault(rec.candidate_id, set()).update(rec.lineage.parent_ids)
        for idx, proposal in enumerate(proposals):
            lineage = proposal.opt_sp.lineage
            candidate_id = arm_id(proposal, ctx.round_num, idx)
            if candidate_id in minted and minted[candidate_id].issuperset(lineage.parent_ids):
                continue
            ledger.append(
                CandidateMintedRecord(
                    round=ctx.round_num,
                    idx=idx,
                    candidate_id=candidate_id,
                    label=candidate_label(ctx.round_num, idx),
                    lineage=lineage,
                )
            )
    ctx.callbacks.on_phase(
        CampaignPhase.PROPOSE,
        "exit",
        round=ctx.round_num,
        view=propose_exit(
            ctx.callbacks.view_context,
            round=ctx.round_num,
            opening=opening,
            candidates=proposal_summaries(proposals, ctx.round_num),
            collapses=collapse_counts(cp.validation_failures for cp in proposals),
            n_scoring_samples=n_cells,
        ),
    )


async def _walk(ctx: RoundContext, proposers: Sequence[Proposer], panel: Panel) -> Proposals:
    proposals = Proposals([])
    for proposer in proposers:
        proposals = await proposer.propose(NodeContext(ctx, proposer.name), panel, proposals)
    return proposals


async def propose_population(ctx: RoundContext, plan: RoundPlan, panel: Panel) -> Proposals:
    if plan.seeders and not ctx.cycle.population:
        seeded = await _walk(ctx, plan.seeders, panel)
        ctx.cycle.population = [
            p.opt_sp
            for p in seeded.proposals
            if not fatal_validation_failures(p.validation_failures)
        ]
    population = await _walk(ctx, plan.proposers, panel)
    seen: dict[str, CandidateProposal] = {}
    for idx, proposal in enumerate(population.proposals):
        candidate_id = arm_id(proposal, ctx.round_num, idx)
        if (arm := seen.get(candidate_id)) is None:
            seen[candidate_id] = proposal
            continue
        logger.info(
            "round %d: proposal %d reaches %s, which this round already measures — one arm",
            ctx.round_num,
            idx + 1,
            candidate_id[:12],
        )
        edges = list(
            dict.fromkeys([*arm.opt_sp.lineage.parent_ids, *proposal.opt_sp.lineage.parent_ids])
        )
        if edges != arm.opt_sp.lineage.parent_ids:
            lineage = arm.opt_sp.lineage.model_copy(update={"parent_ids": edges})
            arm.opt_sp = arm.opt_sp.model_copy(update={"lineage": lineage})
    return replace(population, proposals=list(seen.values()))


async def _adapt(ctx: RoundContext, plan: RoundPlan, round_result: RoundResult) -> None:
    if not plan.adapters:
        return
    ctx.callbacks.on_phase(CampaignPhase.ADAPT, "enter", round=ctx.round_num)
    for adapter in plan.adapters:
        await adapter.adapt(NodeContext(ctx, adapter.name), round_result)
    ctx.callbacks.on_phase(CampaignPhase.ADAPT, "exit", round=ctx.round_num)


async def execute_round(
    cycle: Cycle,
    round_num: int,
    pool: list[Sample],
    callbacks: RunCallbacks,
    *,
    is_final_round: bool = False,
) -> tuple[RoundResult, StopReason | None]:
    plan = round_plan(cycle.optimizer)
    ctx = RoundContext(
        cycle=cycle, round_num=round_num, callbacks=callbacks, is_final_round=is_final_round
    )

    panel = plan.sampler.draw(NodeContext(ctx, plan.sampler.name), pool)
    opening = announce_opening(ctx)
    population = await propose_population(ctx, plan, panel)
    assert cycle.tracking.current_sp is not None
    announce_population(ctx, opening, population.proposals, len(panel.cells))

    callbacks.on_phase(
        CampaignPhase.MEASURE,
        "enter",
        round=round_num,
        view=MeasureEnterView(
            node=plan.measurement,
            n_candidates=len(population.proposals),
            n_samples=len(panel.cells),
        ),
    )
    measured = await measure_population(ctx, population, panel, plan.eliminator, plan.selector)
    callbacks.on_phase(CampaignPhase.MEASURE, "exit", round=round_num)
    callbacks.on_phase(CampaignPhase.SELECT, "enter", round=round_num)
    selection = plan.selector.select(NodeContext(ctx, plan.selector.name), measured, population)
    round_result = _round_result(ctx, measured, selection, elects_on=plan.selector.elects_on)
    callbacks.on_phase(
        CampaignPhase.SELECT,
        "exit",
        round=round_num,
        view=select_exit(callbacks.view_context, round_result),
    )
    _panel_gate(ctx, round_result)
    # AFTER the panel gate: a round halted on a holed panel is unwound and re-run, never crowned.
    callbacks.on_election(round_result)
    cycle.absorb_round(round_result)

    overlap = asyncio.create_task(measure_overlap(cycle, round_result, pool))
    try:
        # An adapter writes for the NEXT round, so none runs when no round follows.
        lives = cycle.config.optimization.lives
        will_stop = (
            is_final_round
            or measured.cut is not None
            or (lives is not None and stalls_left(cycle.rounds, lives.bank) == 0)
        )
        if population.proposals and round_result.results and not will_stop:
            await _adapt(ctx, plan, round_result)
    except BaseException:
        overlap.cancel()
        raise
    reading, bought = await overlap
    round_result = cycle.seat(
        round_result.model_copy(update={"overlap": reading, "overlap_results": bought})
    )
    _warn_not_advanced(round_result)
    return round_result, measured.cut


async def emit_origin_round(cycle: Cycle, session: Session, cb: RunCallbacks) -> None:
    round_result = cycle.origin_round
    if round_result.results:
        await _adapt(
            RoundContext(cycle=cycle, round_num=0, callbacks=cb),
            round_plan(cycle.optimizer),
            round_result,
        )

    # Here, never from `close_round`, which round 0 reaches TWICE.
    cb.on_election(round_result)
    # Unspent here, a look-ahead press during the origin's scoring widens round 1 too.
    session.control.spend_sample_lookahead()
    await close_round(cycle, round_result, 0, session, cb)


def persist_round(
    cycle: Cycle,
    round_result: RoundResult,
    session: Session,
    cb: RunCallbacks,
) -> RoundResult:
    flushed: list[ResumeCheckpointRecord] = []
    if cycle.pending_decisions:
        flushed = list(cycle.pending_decisions)
        cycle.pending_decisions.clear()

    if (ledger := session.state.ledger) is not None:
        for d in flushed:
            ledger.append(d)
        round_result = cycle.seat(
            round_result.model_copy(update={"at_offset": cb.on_round_close(round_result)})
        )

    if session.state.cycle_id:
        write_hard_samples_artifacts(session, cycle)
        write_log_md(session, cycle.config)
        write_review_md(
            session,
            accuracy_ceiling=cycle.config.accuracy_ceiling,
            optimizer=cycle.optimizer,
            framing=cycle.framing,
        )

    if _rr := session.state.audit_projection:
        _rr.flush()
    return round_result


def flush_pending_decisions(cycle: Cycle, session: Session) -> int:
    """The controller acts after its round persisted; unflushed at a stop, a resume re-spends the act."""
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
) -> tuple[RoundResult, RunStanding]:
    if cycle.origin_restamped:
        # A ruler that warmed this round gave round 0 a θ it could not have had at its own close.
        cycle.origin_restamped = False
        persist_round(cycle, cycle.origin_round, session, cb)
    state = round_result.optimizer_state.model_copy(
        update={"payload": cycle.working_state.round_payload()}
    )
    stood = round_result.model_copy(update={"optimizer_state": state})
    facts = cycle.optimizer.runtime.round_facts(cycle.optimizer, stood)
    round_result = cycle.seat(stood.model_copy(update={"optimizer_facts": facts}))
    lives = cycle.config.optimization.lives
    standing = RunStanding.after(
        cycle.rounds,
        lives=None if lives is None else lives.bank,
        spent=close_spend(ledger_chain(CycleDir(session.store.campaigns.cycle_dir(session.hop))))
        if session.state.cycle_id
        else None,
    )
    # The close first: a subscriber of the standing reads the round it names off that record.
    round_result = persist_round(cycle, round_result, session, cb)
    leading = next(
        (
            c.candidate_id
            for c in round_result.candidate_scores
            if round_result.round and c.label == round_result.leading_label
        ),
        None,
    )
    origin = cycle.origin_round
    cb.on_round_complete(
        round_result,
        standing,
        None
        if leading is None
        else panel_precision(
            round_result.all_candidate_results.get(leading, NO_CELLS).cells,
            origin.all_candidate_results[origin.origin.candidate_id].cells,
        ),
    )
    if cycle.sample_index is not None:
        cycle.sample_index.refresh(
            session.store,
            scorer=session.scoring.require_scorer(),
            dataset_name=session.dataset_name,
        )
    return round_result, standing


async def post_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    session: Session,
    cb: RunCallbacks,
    *,
    is_final_round: bool = False,
) -> None:
    controller = round_plan(cycle.optimizer).controller
    acts = False
    if controller is not None:
        ctx = NodeContext[Any](
            RoundContext(
                cycle=cycle, round_num=round_num, callbacks=cb, is_final_round=is_final_round
            ),
            controller.name,
        )
        acts = controller.observe(ctx, round_result)

    round_result, standing = await close_round(cycle, round_result, round_num, session, cb)

    # Before the stop below, so a perfect round that also ends the campaign still gets its check.
    await verify_on_saturation(
        stores=session.store,
        hop=CycleHop(campaign_id=session.campaign_id, cycle_id=session.state.cycle_id),
        round_num=round_num,
        accuracy=round_result.accuracy,
        winner_id=next((c.candidate_id for c in round_result.selected_scores), None),
        control=session.control,
    )
    await grade_round_selection(cycle, session, round_result, cb=cb)
    reserve_selection_pass(cycle, session)

    if (stop := standing_tripped(cycle, standing)) is not None:
        raise StopLoop(stop)
    if acts and not is_final_round:
        assert controller is not None
        await controller.act(ctx)
