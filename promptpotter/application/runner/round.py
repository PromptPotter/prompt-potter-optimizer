from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.node_context import NodeContext, bank_proposals
from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.bench.round_analysis import compute_round_diagnostics
from promptpotter.application.diagnostics.verify import verify_on_saturation
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizers.nodes import (
    INITIAL_POPULATION,
    Proposals,
    RoundContext,
    RoundOpening,
    RoundWalk,
)
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.runner.arm_gate import operator_arms
from promptpotter.application.runner.bench import grade_round_selection, reserve_selection_pass
from promptpotter.application.runner.inner.spawn_context import pin_inner_ruler
from promptpotter.application.runner.measurement import measure_population
from promptpotter.application.runner.output import (
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)
from promptpotter.application.runner.overlap import measure_overlap
from promptpotter.application.runner.termination import panel_gate_tripped, standing_tripped
from promptpotter.application.views.ingress import propose_enter, propose_exit, select_exit
from promptpotter.domain.arms import ArmState, Concluded, Measured, Proposal, is_leader_eligible
from promptpotter.domain.cell_fold import unscoreable_cells
from promptpotter.domain.optimizer_state import RoundOutput
from promptpotter.domain.origin_panel import OverlapReading
from promptpotter.domain.paired_reading import ArmKey, ReadingState
from promptpotter.domain.phase_views import MeasureEnterView
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    CampaignPhase,
    StopLoop,
    StopReason,
)
from promptpotter.domain.results import Election, RoundResult
from promptpotter.domain.results_health import assemble_prior_healths, compute_round_health
from promptpotter.domain.run_records import BenchCheckpointKind
from promptpotter.domain.run_standing import line_reference, rounds_without_advance, stalls_left
from promptpotter.domain.scoring import CellSheet
from promptpotter.domain.wounds import collapse_counts
from promptpotter.shared.errors import SendRefusedError, graceful

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.optimizer_manifest import RoundPlan
    from promptpotter.application.optimizers.nodes import (
        Controller,
        MeasuredRound,
        Panel,
        Proposer,
        Selection,
    )
    from promptpotter.domain.arm_reading import DisplayMetric
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)


OWED_WRITE_ATTEMPTS = 3
"""Each is a whole call: the client's backpressure and 5xx retries sit INSIDE one attempt and do not count against this."""


def run_init(cycle: Cycle, cb: RunCallbacks) -> None:
    """Once per cycle, before round 0's adapters: a cycle whose origin closed already holds what it wrote."""
    if cycle.origin_round.optimizer_state is not None:
        return
    ctx = RoundContext(cycle=cycle, round_num=0, callbacks=cb)
    for initializer in cycle.optimizer.plan.initializers:
        initializer.initialize(NodeContext(ctx, initializer))


async def settle_owed_outputs(ctx: RoundContext) -> None:
    """Before a walk: an adapter that landed nothing on the round before is re-run onto it, and the walk HALTS (``ADAPTER_OWED``, resumable) where it never lands."""
    cycle = ctx.cycle
    prior = cycle.rounds[-1]
    # A replayed walk makes no call, so nothing it would read is re-sent.
    if prior.round == 0 or not prior.results or cycle.banked_proposals(ctx.round_num) is not None:
        return
    at_prior = RoundContext(cycle=cycle, round_num=prior.round, callbacks=ctx.callbacks)
    for adapter in cycle.optimizer.plan.adapters:
        owed = [
            kind
            for kind in adapter.writes
            if issubclass(kind, RoundOutput) and cycle.output(kind, prior.round) == kind()
        ]
        if not owed:
            continue
        last: Exception | None = None
        for attempt in range(1, OWED_WRITE_ATTEMPTS + 1):
            try:
                await adapter.adapt(NodeContext(at_prior, adapter))
                break
            # A refused send is decided: swallowed, it halts as a PAUSE that `resume` re-enters forever.
            except (KeyboardInterrupt, asyncio.CancelledError, SendRefusedError):
                raise
            except Exception as exc:
                last = exc
                logger.exception(
                    "round %d %s re-run %d/%d failed: %s",
                    prior.round,
                    adapter.name,
                    attempt,
                    OWED_WRITE_ATTEMPTS,
                    exc,
                )
        else:
            raise StopLoop(StopReason.ADAPTER_OWED) from last
        logger.info(
            "Round %d's %s landed late; round %d reads it.",
            prior.round,
            adapter.name,
            ctx.round_num,
        )


def _round_result(
    ctx: RoundContext,
    measured: MeasuredRound,
    selection: Selection,
    *,
    elects_on: DisplayMetric,
) -> RoundResult:
    winner, leading = selection.selected, selection.leading
    if winner is not None and leading != winner:
        raise ValueError(
            f"round {ctx.round_num}: the selector kept {winner[:12]} and named "
            f"{(leading or 'no arm')[:12]} as the one the round is read off"
        )
    cycle = ctx.cycle
    arms: dict[ArmKey, ArmState] = {key: read.state for key, read in measured.filed.items()}
    cells = {key: read.rows for key, read in measured.filed.items()}
    # A held round headlines the parent RE-SCORED on this panel, never a union of its cells.
    ended_on = winner or measured.parent.key
    reference = line_reference(cycle.rounds, origin=ArmKey(cycle.origin_round.origin.individual.id))
    assert reference.arm == measured.parent.key, "a round re-reads the candidate its line stands on"
    head, head_rows = arms[ended_on], cells[ended_on]
    assert isinstance(head, Concluded)
    not_attempted = max(0, head.expected_samples - len(head_rows)) if winner else 0
    elected_round = RoundResult(
        round=ctx.round_num,
        arms=arms,
        reference=reference,
        folds=measured.folds,
        cells=cells,
        overlap_results=measured.on_panel,
        election=Election(
            selected=winner,
            leading=leading,
            ended_on=ended_on,
            electable=len(measured.electable),
            fit=measured.fit,
            elects_on=elects_on,
            verdict_reason=selection.verdict_reason,
            ruler=cycle.difficulty.stamp,
        ),
        health=compute_round_health(
            results=head_rows.cells,
            prior_healths=assemble_prior_healths(ctx.cycle.rounds, ctx.round_num),
            not_attempted=not_attempted,
            # Counted off the rows: an ungraded cell is already inside `fold.walked`.
            unscored=sum(1 for cell in head_rows if cell.grade.unscored is not None),
        ),
        # Until `measure_overlap` lands, after every pick this round makes.
        overlap=OverlapReading.unpaired(ReadingState.PENDING, ctx.round_num, winner is not None),
    )
    diagnostics = compute_round_diagnostics(elected_round, cycle.session.pipeline_schema)
    return elected_round.model_copy(update={"diagnostics": diagnostics})


def _unbought_cells(arm: ArmState, rows: CellSheet, cut: StopReason | None) -> int:
    # Under a cut, a MEASURED arm short of its panel was stopped by no rule and no operator.
    if cut is None or not isinstance(arm, Measured) or arm.locked_in is not None:
        return 0
    return max(0, arm.expected_samples - len(rows))


def _panel_gate(ctx: RoundContext, round_result: RoundResult, cut: StopReason | None) -> None:
    session = ctx.cycle.session
    round_num = ctx.round_num
    panel = round_result.panel
    unbought = {
        key: _unbought_cells(arm, round_result.rows(key), cut) for key, arm in panel.items()
    }
    holes = {key: unscoreable_cells(round_result.rows(key)) + unbought[key] for key in panel}
    # A broken arm also carries error rows, but its own outcome already charges it.
    holed = sorted(key for key, arm in panel.items() if is_leader_eligible(arm) and holes[key])
    # Sink is the LEDGER: the halt below unwinds before `close_round`.
    ledger = session.state.ledger
    assert ledger is not None, "build_run_observers must bind state.ledger before a round runs"
    record_decision(
        ledger,
        BenchCheckpointKind.PANEL_COVERAGE,
        {"arms": list(panel), "round_num": round_num},
        holed,
        node=None,
        data={"holes_by_arm": holes},
        round=round_num,
    )
    holed_rows = [
        (key, cell) for key in holed for cell in round_result.rows(key) if cell.facts.errored
    ]
    reason = panel_gate_tripped(
        [cell for _, cell in holed_rows],
        cut if any(unbought[cid] for cid in holed) else None,
        ctx.cycle.config.optimization.panel_gate,
    )
    if reason is None:
        return
    slot_of = ctx.cycle.session.bank.slot_of
    for cid, cell in holed_rows:
        logger.warning(
            "round %d panel HOLE: candidate %s, sample %s — %s",
            round_num,
            cid,
            slot_of[cell.key],
            cell.facts.error or cell.facts.error_category,
        )
    for cid in holed:
        if unbought[cid]:
            logger.warning(
                "round %d panel HOLE: candidate %s — %d cell(s) the %s stop left unbought",
                round_num,
                cid,
                unbought[cid],
                reason.value,
            )
    logger.warning(
        "Round %d halted BEFORE electing on an incomplete panel: %d of %d electable "
        "candidate(s) carry cells that returned no measurement. The round is not persisted "
        "— a resume re-runs it, replays the cached candidates and re-measures the holes. %s",
        round_num,
        len(holed),
        round_result.candidates_scored,
        STOP_REASON_INFO[reason].next_step,
    )
    raise StopLoop(reason)


def announce_opening(ctx: RoundContext, walk: RoundWalk) -> RoundOpening:
    cycle = ctx.cycle
    bench = RoundOpening(
        standing=f"no advance {rounds_without_advance(cycle.rounds)}",
        note=f"seeds the population ({INITIAL_POPULATION})" if walk.seeding else "",
        arms=None if walk.pipeline == INITIAL_POPULATION else cycle.optimizer.arms_per_round,
        proposer=walk.proposer,
        replayed=False,
    )
    opening = walk.lead.opening(NodeContext(ctx, walk.lead), bench)
    fold = cycle.parent_fold
    node = walk.proposer
    cb = ctx.callbacks
    cb.on_phase(
        CampaignPhase.PROPOSE,
        "enter",
        round=ctx.round_num,
        view=propose_enter(
            cb.view_context,
            round=ctx.round_num,
            node=node,
            opening=opening,
            budget_rounds=cycle.config.optimization.budget.rounds,
            parent_accuracy=None if fold is None else fold.accuracy,
            prompt_preview="\n\n".join(
                cycle.parent.render(prompt_node)
                for prompt_node in cycle.session.pipeline_schema.prompt_nodes()
            )[:120],
            model=cycle.optimizer.model(node),
            pipeline_params=cycle.parent_point.pipeline_params,
            parent_prompts=cycle.parent.prompts,
            run_standing=cycle.standing,
        ),
    )
    return opening


def announce_population(ctx: RoundContext, opening: RoundOpening, population: Proposals) -> None:
    for key, arm in population.arms(ctx.round_num).items():
        ctx.callbacks.on_arm(ctx.round_num, key, arm)
    ctx.callbacks.on_phase(
        CampaignPhase.PROPOSE,
        "exit",
        round=ctx.round_num,
        view=propose_exit(
            ctx.callbacks.view_context,
            round=ctx.round_num,
            opening=opening,
            proposals=population.proposals,
            collapses=collapse_counts(cp.validation_failures for cp in population.proposals),
        ),
    )


async def _walk(ctx: RoundContext, proposers: Sequence[Proposer], panel: Panel) -> Proposals:
    proposals = Proposals([])
    for proposer in proposers:
        proposals = await proposer.propose(NodeContext(ctx, proposer), panel, proposals)
    return proposals


def _one_arm_each(ctx: RoundContext, proposals: list[Proposal]) -> list[Proposal]:
    seen: dict[ArmKey, Proposal] = {}
    for idx, proposal in enumerate(proposals):
        key = proposal.key(ctx.round_num, idx)
        if (arm := seen.get(key)) is None:
            seen[key] = proposal
            continue
        logger.info(
            "round %d: proposal %d reaches %s, which this round already measures — one arm",
            ctx.round_num,
            idx + 1,
            key[:12],
        )
        lineage = arm.individual.lineage
        edges = list(dict.fromkeys([*lineage.parent_ids, *proposal.individual.lineage.parent_ids]))
        if edges != lineage.parent_ids:
            individual = arm.individual.model_copy(
                update={"lineage": lineage.model_copy(update={"parent_ids": edges})}
            )
            seen[key] = arm.model_copy(update={"individual": individual})
    return list(seen.values())


def bank_walk(ctx: RoundContext, proposals: list[Proposal]) -> None:
    """Once per round: a resume re-walking a banked round reads its record and writes no second."""
    session = ctx.cycle.session
    # An EMPTY bank replays as zero arms on every resume: a permanent failure to propose.
    if not proposals:
        return
    if session.store.campaigns.round_proposals(session.hop, ctx.round_num) is None:
        bank_proposals(ctx, proposals, submitted=False)


async def propose_population(ctx: RoundContext, walk: RoundWalk, panel: Panel) -> Proposals:
    seeds, proposers, seated = walk.walked
    if seeds:
        seeded = await _walk(ctx, seeds, panel)
        ctx.cycle.population = [p.individual for p in seeded.proposals if not p.refused]
    population = await _walk(ctx, proposers, panel)
    bank_walk(ctx, population.proposals)
    arms = _one_arm_each(ctx, population.proposals)
    if seated:
        arms = _one_arm_each(ctx, await operator_arms(ctx, arms))
    return replace(population, proposals=arms)


async def _adapt(ctx: RoundContext, plan: RoundPlan) -> None:
    if not plan.adapters:
        return
    ctx.callbacks.on_phase(CampaignPhase.ADAPT, "enter", round=ctx.round_num)
    for adapter in plan.adapters:
        # Survived here: the next walk re-runs it first (`settle_owed_outputs`).
        with graceful(f"round {ctx.round_num} {adapter.name} landed nothing"):
            await adapter.adapt(NodeContext(ctx, adapter))
    ctx.callbacks.on_phase(CampaignPhase.ADAPT, "exit", round=ctx.round_num)


class ControllerAct(NamedTuple):
    """The rule a controller banked off its round, acted on once the round closes (`post_round`)."""

    controller: Controller
    rule: str


class RoundWalked(NamedTuple):
    result: RoundResult
    cut: StopReason | None
    act: ControllerAct | None


async def _proposed(ctx: RoundContext, panel: Panel) -> Proposals:
    walk = ctx.cycle.optimizer.plan.walk(stands=bool(ctx.cycle.population))
    opening = announce_opening(ctx, walk)
    population = await propose_population(ctx, walk, panel)
    announce_population(ctx, opening, population)
    return population


async def _measured(
    ctx: RoundContext, population: Proposals, panel: Panel, pool: list[Sample]
) -> MeasuredRound:
    plan, callbacks = ctx.cycle.optimizer.plan, ctx.callbacks
    callbacks.on_phase(
        CampaignPhase.MEASURE,
        "enter",
        round=ctx.round_num,
        view=MeasureEnterView(
            n_candidates=len(population.proposals),
            n_samples=len(panel.cells),
        ),
    )
    measured = await measure_population(
        ctx, population, panel, pool, plan.eliminator, plan.selector
    )
    callbacks.on_phase(CampaignPhase.MEASURE, "exit", round=ctx.round_num)
    return measured


def _survivors(ctx: RoundContext, selection: Selection) -> list[OptSearchPoint]:
    survivor = ctx.cycle.optimizer.plan.survivor
    if survivor is None:
        return list(selection.ranked)
    return list(survivor.survive(NodeContext(ctx, survivor), selection.ranked))


def _crowned(ctx: RoundContext, measured: MeasuredRound, selection: Selection) -> RoundResult:
    """The bench's close of the election: the round's result, gated on a whole panel, then absorbed."""
    cycle, callbacks = ctx.cycle, ctx.callbacks
    result = _round_result(
        ctx, measured, selection, elects_on=cycle.optimizer.plan.selector.elects_on
    )
    callbacks.on_phase(
        CampaignPhase.SELECT,
        "exit",
        round=ctx.round_num,
        view=select_exit(callbacks.view_context, result),
    )
    _panel_gate(ctx, result, measured.cut)
    # AFTER the panel gate: a round halted on a holed panel is unwound and re-run, never crowned.
    assert result.election is not None
    callbacks.on_election(ctx.round_num, result.election)
    cycle.absorb_round(result)
    return result


async def _adapt_beside_overlap(
    ctx: RoundContext,
    round_result: RoundResult,
    measured: MeasuredRound,
    population: Proposals,
    pool: list[Sample],
    *,
    is_final_round: bool,
) -> RoundResult:
    cycle = ctx.cycle
    overlap = asyncio.create_task(
        measure_overlap(cycle, round_result, pool, measured.origin_panel_slots)
    )
    try:
        # An adapter writes for the NEXT round, so none runs when no round follows.
        lives = cycle.config.optimization.lives
        will_stop = (
            is_final_round
            or measured.cut is not None
            or (lives is not None and stalls_left(cycle.rounds, lives.bank) == 0)
        )
        if population.proposals and round_result.results and not will_stop:
            await _adapt(ctx, cycle.optimizer.plan)
    except BaseException:
        overlap.cancel()
        raise
    reading, bought = await overlap
    # Re-read: an adapter emits onto the round it adapts to.
    return cycle.seat(
        cycle.rounds[-1].model_copy(update={"overlap": reading, "overlap_results": bought})
    )


async def execute_round(
    cycle: Cycle,
    round_num: int,
    callbacks: RunCallbacks,
    *,
    is_final_round: bool = False,
) -> RoundWalked:
    """One round, each role handed what the ones before it left."""
    # The search split alone: a bench or demo row a round could draw would elect on held-out rows.
    pool = list(cycle.session.state.scoring.require_partition().search)
    ctx = RoundContext(cycle=cycle, round_num=round_num, callbacks=callbacks)
    await settle_owed_outputs(ctx)
    # Before the draw, which reads the scale the election will, and before an arm spawns a cell.
    cycle.difficulty.open_round(cycle.rounds)
    pin_inner_ruler(cycle.difficulty.stamp.reach)
    plan = cycle.optimizer.plan
    panel = plan.sampler.draw(NodeContext(ctx, plan.sampler), pool)
    population = await _proposed(ctx, panel)
    measured = await _measured(ctx, population, panel, pool)
    callbacks.on_phase(CampaignPhase.SELECT, "enter", round=round_num)
    selection = plan.selector.select(NodeContext(ctx, plan.selector), measured)
    cycle.population = _survivors(ctx, selection)
    result = _crowned(ctx, measured, selection)
    result = await _adapt_beside_overlap(
        ctx, result, measured, population, pool, is_final_round=is_final_round
    )
    act = None
    if (controller := plan.controller) is not None:
        seat = RoundContext(cycle=cycle, round_num=round_num, callbacks=callbacks)
        if (rule := controller.bank_round(NodeContext(seat, controller), result)) is not None:
            act = ControllerAct(controller, rule)
    return RoundWalked(result, measured.cut, act)


async def emit_origin_round(cycle: Cycle, session: Session, cb: RunCallbacks) -> None:
    if cycle.origin_round.results:
        await _adapt(RoundContext(cycle=cycle, round_num=0, callbacks=cb), cycle.optimizer.plan)
    round_result = cycle.origin_round

    # The origin's cells fold after the election that adopted it, and before the close.
    assert round_result.election is not None, "round 0 elects the origin it adopted"
    cb.on_election(0, round_result.election)
    cycle.difficulty.fold(round_result)
    # Unspent here, a look-ahead press during the origin's scoring widens round 1 too.
    session.state.control.spend_sample_lookahead()
    await close_round(cycle, round_result, 0, session, cb)


def flush_pending_decisions(cycle: Cycle, ledger: CycleEventLog) -> None:
    """The controller acts after its round persisted; unflushed at a stop, a resume re-spends the act."""
    while cycle.pending_decisions:
        ledger.append(cycle.pending_decisions[0])
        cycle.pending_decisions.pop(0)


async def close_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    session: Session,
    cb: RunCallbacks,
) -> RoundResult:
    stood = round_result.model_copy(update={"optimizer_state": cycle.close_state(round_num)})
    facts = cycle.optimizer.round_facts(stood)
    closing = cycle.seat(stood.model_copy(update={"optimizer_facts": facts}))
    flush_pending_decisions(cycle, cb.ledger)
    offset, cycle.standing = cb.on_round_close(
        closing,
        cycle.rounds,
        session.bank.slot_of,
        cycle.difficulty,
        lives=cycle.config.optimization.lives,
    )
    round_result = cycle.seat(closing.model_copy(update={"at_offset": offset}))
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
    if cycle.sample_index is not None:
        cycle.sample_index.refresh(
            session.store,
            scorer=session.state.scoring.require_scorer(),
        )
    return round_result


async def post_round(
    cycle: Cycle,
    walked: RoundWalked,
    round_num: int,
    session: Session,
    cb: RunCallbacks,
    *,
    is_final_round: bool = False,
) -> None:
    round_result = await close_round(cycle, walked.result, round_num, session, cb)

    # Before the stop below, so a perfect round that also ends the campaign still gets its check.
    fold, election = round_result.fold, round_result.election
    await verify_on_saturation(
        stores=session.store,
        hop=session.hop,
        round_num=round_num,
        accuracy=None if fold is None else fold.accuracy,
        winner_id=None if election is None else election.selected,
        control=session.state.control,
        seed=cycle.rng(round_num, "verify").getrandbits(31),
    )
    await grade_round_selection(cycle, session, round_result, cb=cb)
    reserve_selection_pass(cycle, session)

    if (stop := standing_tripped(cycle)) is not None:
        raise StopLoop(stop)
    if (act := walked.act) is not None and not is_final_round:
        seat = RoundContext(cycle=cycle, round_num=round_num, callbacks=cb)
        await act.controller.act(NodeContext(seat, act.controller), act.rule)
