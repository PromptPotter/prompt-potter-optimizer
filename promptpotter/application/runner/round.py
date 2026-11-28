"""A round's sequence over the bench's phases (`domain/phases.py::CampaignPhase`): INIT, PROPOSE
(the operator's arms among them), MEASURE (`runner/measure.py`), SELECT, ADAPT, its close.
The round's arms are the operator's answer: a kept draft passes untouched, an arm of theirs edits
the parent. The gate's wait (`hold_for_operator`) writes nothing of its own, so it rides the shared heartbeat."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.bench.cycle import Cycle, RoundContext
from promptpotter.application.bench.difficulty import ruler_clock
from promptpotter.application.bench.line import (
    line_outlay,
    reserve_selection_pass,
)
from promptpotter.application.bench.node_context import (
    Children,
    NodeContext,
    bank_proposals,
    closed_round,
    line_series,
    reread_walk,
)
from promptpotter.application.bench.resume_and_fork.decisions import decision
from promptpotter.application.bench.round_analysis import ended_on
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_contract import (
    OPERATOR_SEAT,
    MeasuredRound,
    Proposals,
    RoundOpening,
    RoundWalk,
)
from promptpotter.application.run_stop import StopLoop, panel_gate_tripped, standing_tripped
from promptpotter.application.runner.measure import (
    measure_overlap,
    measure_population,
)
from promptpotter.application.scoring.passes import verify_on_saturation
from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.arms import (
    ArmState,
    Measured,
    Proposal,
    is_leader_eligible,
)
from promptpotter.domain.cell_fold import repairable_holes
from promptpotter.domain.command_kinds import SubmittedArm
from promptpotter.domain.failures import ValidationFailure, ValidationReason
from promptpotter.domain.opt_search_point import Locus, OptSearchPoint, loci_of
from promptpotter.domain.optimizer_state import RoundOutput
from promptpotter.domain.paired_reading import (
    ArmKey,
    ReadingState,
)
from promptpotter.domain.phase_acts import (
    CountFact,
    MeasureOpened,
    ProposeClosed,
    ProposeOpened,
    TextFact,
)
from promptpotter.domain.phases import CampaignPhase, StopReason
from promptpotter.domain.pipeline_schema import INITIAL_POPULATION
from promptpotter.domain.records.decision import PanelCoverage
from promptpotter.domain.records.lineage import ArmAct
from promptpotter.domain.records.rounds import ArmRecord, ElectionRecord, RoundWarningRecord
from promptpotter.domain.records.run_phase import ArmsGate, PhaseRecord, Running, RunPhaseRecord
from promptpotter.domain.results import Election, RoundResult, round_document_digest
from promptpotter.domain.run_standing import (
    LineOutlay,
    RunStanding,
    line_reference,
    rounds_without_advance,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellSheet
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.infrastructure.llm.heartbeat import CallBeat, heartbeat
from promptpotter.infrastructure.llm.telemetry import append_record
from promptpotter.shared.errors import SendRefusedError, fault_said

if TYPE_CHECKING:
    from promptpotter.application.optimizer_contract import (
        Controller,
        Proposer,
        Selection,
    )
    from promptpotter.application.optimizer_manifest import RoundPlan
    from promptpotter.domain.campaign_config import GateMode


_POLL_S = 1.0


async def hold_for_operator[T](
    session: Session, take: Callable[[], T | None], *, node: str, round_num: int
) -> T | None:
    """What *take* first answers, or ``None`` where a pause reached the hold before an answer did."""
    beat = asyncio.create_task(
        heartbeat(
            CallBeat(f"{node}:{session.cycle_id}", node),
            round_num=round_num,
            start_monotonic=time.monotonic(),
        )
    )
    try:
        while True:
            if session.state.control.pause_requested():
                return None
            answered = take()
            if answered is not None:
                return answered
            await asyncio.sleep(_POLL_S)
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat


def author_arm(ctx: RoundContext, parent: OptSearchPoint, arm: SubmittedArm) -> Proposal:
    """An undeclared node or field, or a shot no proposer of the round could draw, fails at ``Children.child``."""
    cap = ctx.cycle.optimizer.shot_cap
    shots = {Locus(node, SHOTS_KEY): keys for node, keys in arm.shots.items()}
    over = [
        ValidationFailure(
            axis=locus.name,
            value=str(len(keys)),
            allowed=[str(cap)],
            reason=ValidationReason.SHOTS_OVER_CAP,
        )
        for locus, keys in shots.items()
        if len(keys) > cap
    ]
    return Children(ctx, OPERATOR_SEAT).child(
        [parent],
        overlay=arm.pipeline_overlay,
        failures=over,
        prompt_loci={**loci_of(arm.prompt_fields), **shots},
        changes_description=arm.changes_description,
    )


async def operator_arms(ctx: RoundContext, drafts: list[Proposal]) -> list[Proposal]:
    """Holds once per round: an answer already banked against this parent IS the round's arms."""
    session = ctx.cycle.session
    rounds = ctx.cycle.rounds
    standing = session.store.campaigns.standing_rounds(session.hop, moment=None)
    answered = standing.submitted.get(ctx.round_num)
    if answered is not None and rounds and answered.consumed == round_document_digest(rounds[-1]):
        return list(answered.proposals)
    append_record(RunPhaseRecord(state=ArmsGate(drafts=list(drafts))))

    def _take() -> list[Proposal] | None:
        standing = session.state.control.standing_submission()
        if standing is None:
            return None
        command_id, asked = standing
        arms = [
            *(drafts[idx] for idx in asked.keep),
            *(author_arm(ctx, ctx.cycle.parent, arm) for arm in asked.arms),
        ]
        # Recorded and banked BEFORE the ack: a producer lost between them re-reads the answer.
        session.act(ArmAct.SUBMIT_ARMS)
        bank_proposals(ctx, arms, submitted=True)
        session.state.control.take_submission(command_id)
        return arms

    arms = await hold_for_operator(session, _take, node=OPERATOR_SEAT.name, round_num=ctx.round_num)
    if arms is None:
        raise StopLoop(StopReason.PAUSED)
    append_record(RunPhaseRecord(state=Running()))
    return arms


OWED_WRITE_ATTEMPTS = 3
"""Each is a whole call: the client's backpressure and 5xx retries sit INSIDE one attempt and do not count against this."""


def run_init(ctx: RoundContext) -> None:
    """Once per cycle, before round 0's adapters: a cycle whose origin closed already holds what it wrote."""
    if ctx.cycle.origin_round.optimizer_state is not None:
        return
    for initializer in ctx.cycle.optimizer.plan.initializers:
        initializer.initialize(NodeContext(ctx, initializer))


async def settle_owed_outputs(ctx: RoundContext) -> None:
    """Before a walk: an adapter that landed nothing on the round before is re-run onto it, and the walk HALTS (``ADAPTER_OWED``, resumable) where it never lands."""
    cycle = ctx.cycle
    prior = cycle.rounds[-1]
    # A replayed walk makes no call, so nothing it would read is re-sent.
    if prior.round == 0 or not prior.results or cycle.banked_walk(ctx.round_num) is not None:
        return
    at_prior = RoundContext(cycle=cycle, round_num=prior.round)
    for adapter in cycle.optimizer.plan.adapters:
        owed = [
            kind
            for kind in adapter.writes
            if issubclass(kind, RoundOutput) and cycle.output(kind, prior.round) == kind()
        ]
        if not owed:
            continue
        last: Exception | None = None
        for _ in range(OWED_WRITE_ATTEMPTS):
            try:
                await adapter.adapt(NodeContext(at_prior, adapter))
                break
            # A refused send is decided: swallowed, it halts as a PAUSE that `resume` re-enters forever.
            except (KeyboardInterrupt, asyncio.CancelledError, SendRefusedError):
                raise
            except Exception as exc:  # noqa: BLE001 -- the last fault is the halt's detail
                last = exc
        else:
            assert last is not None, "a re-run that never landed raised"
            said = f"round {prior.round} {adapter.name}, re-run {OWED_WRITE_ATTEMPTS}x"
            raise StopLoop(
                StopReason.ADAPTER_OWED, detail=f"{said}: {fault_said(last)[0]}"
            ) from last


def _round_result(
    ctx: RoundContext,
    measured: MeasuredRound,
    selection: Selection,
    *,
    elects_on: DisplayMetric,
) -> RoundResult:
    cycle = ctx.cycle
    reference = line_reference(cycle.rounds, origin=ArmKey(cycle.origin_round.origin.individual.id))
    assert reference.arm == measured.parent.key, "a round re-reads the candidate its line stands on"
    return ended_on(
        Election(
            leading=selection.leading,
            ending=selection.ending,
            # A round ending on its parent headlines it RE-SCORED on this panel, never a union.
            parent=reference,
            electable=selection.electable,
            vs_reference=measured.vs_reference,
            elects_on=elects_on,
            deciding_pair=selection.deciding_pair,
            verdict_reason=selection.verdict_reason,
        ),
        priors=cycle.rounds,
        panel=ReadingState.PENDING,
        round=ctx.round_num,
        arms={key: read.state for key, read in measured.filed.items()},
        folds=measured.folds,
        cells={key: read.rows for key, read in measured.filed.items()},
        panel_rows=measured.on_panel,
    )


def _unbought_cells(arm: ArmState, rows: CellSheet, cut: StopLoop | None) -> int:
    # Under a cut, a MEASURED arm short of its panel was stopped by no rule and no operator.
    if cut is None or not isinstance(arm, Measured) or arm.locked_in is not None:
        return 0
    return max(0, arm.expected_samples - len(rows))


class PanelGate(NamedTuple):
    holes: dict[ArmKey, int]
    # The leader-eligible arms it holes: a broken arm's error rows its own outcome charges.
    holed: list[ArmKey]
    tripped: StopLoop | None


def panel_gate_on(round_result: RoundResult, cut: StopLoop | None, mode: GateMode) -> PanelGate:
    """The panel gate's decision on a round's election: each arm's holes, a cut's unbought cells
    among them, so a round the budget cut stops in the cut's words only where it left an arm short."""
    proposed = round_result.proposed
    unbought = {
        key: _unbought_cells(arm, round_result.rows(key), cut) for key, arm in proposed.items()
    }
    holes = {key: repairable_holes(round_result.rows(key)) + unbought[key] for key in proposed}
    holed = sorted(key for key, arm in proposed.items() if is_leader_eligible(arm) and holes[key])
    tripped = panel_gate_tripped(
        [cell for key in holed for cell in round_result.rows(key) if cell.facts.hole],
        cut if any(unbought[cid] for cid in holed) else None,
        mode,
    )
    return PanelGate(holes, holed, tripped)


def _panel_gate(ctx: RoundContext, round_result: RoundResult, cut: StopLoop | None) -> None:
    gate = panel_gate_on(round_result, cut, ctx.cycle.config.optimization.panel_gate)
    # Sink is the LEDGER: the halt below unwinds before `close_round`.
    coverage = PanelCoverage(holes_by_arm=gate.holes)
    append_record(decision(coverage, gate.holed, node=None, round=ctx.round_num))
    if gate.tripped is not None:
        raise gate.tripped


def announce_opening(ctx: RoundContext, walk: RoundWalk) -> RoundOpening:
    cycle = ctx.cycle
    bench = RoundOpening(
        facts={
            "rounds without advance": CountFact(value=rounds_without_advance(cycle.rounds)),
            "seeds the population": TextFact(value=INITIAL_POPULATION) if walk.seeding else None,
        },
        arms=None if walk.pipeline == INITIAL_POPULATION else cycle.optimizer.arms_per_round,
        replayed=cycle.banked_walk(ctx.round_num) is not None,
    )
    opening = walk.lead.opening(NodeContext(ctx, walk.lead), bench)
    node = walk.proposer
    append_record(
        PhaseRecord(
            phase=CampaignPhase.PROPOSE,
            step="enter",
            round=ctx.round_num,
            act=ProposeOpened(
                node=node,
                budget_rounds=cycle.config.optimization.budget.rounds,
                parent_round=cycle.elected[0].round,
                arms=opening.arms,
                facts=opening.facts,
                model=cycle.optimizer.model(node),
                run_standing=cycle.standing,
            ),
        )
    )
    return opening


def announce_population(ctx: RoundContext, opening: RoundOpening, population: Proposals) -> None:
    append_record(
        *(
            ArmRecord(round=ctx.round_num, arm=key, state=arm)
            for key, arm in population.arms(ctx.round_num).items()
        ),
        PhaseRecord(
            phase=CampaignPhase.PROPOSE,
            step="exit",
            round=ctx.round_num,
            act=ProposeClosed(replayed=opening.replayed),
        ),
    )


async def _walk(ctx: RoundContext, proposers: Sequence[Proposer]) -> Proposals:
    proposals = Proposals([])
    for proposer in proposers:
        proposals = await proposer.propose(NodeContext(ctx, proposer), proposals)
    return proposals


def _one_arm_each(ctx: RoundContext, proposals: list[Proposal]) -> list[Proposal]:
    seen: dict[ArmKey, Proposal] = {}
    for idx, proposal in enumerate(proposals):
        key = proposal.key(ctx.round_num, idx)
        if (arm := seen.get(key)) is None:
            seen[key] = proposal
            continue
        lineage = arm.individual.lineage
        edges = list(dict.fromkeys([*lineage.parent_ids, *proposal.individual.lineage.parent_ids]))
        if edges != lineage.parent_ids:
            individual = arm.individual.model_copy(
                update={"lineage": lineage.model_copy(update={"parent_ids": edges})}
            )
            seen[key] = arm.model_copy(update={"individual": individual})
    return list(seen.values())


def bank_walk(ctx: RoundContext, proposals: list[Proposal]) -> None:
    """Once per round, with the state the walk left and the decisions it made."""
    # An EMPTY bank replays as zero arms on every resume: a permanent failure to propose.
    if not proposals:
        return
    flush_pending_decisions(ctx.cycle)
    bank_proposals(ctx, proposals, submitted=False)


async def _walked(ctx: RoundContext, walk: RoundWalk) -> Proposals:
    cycle = ctx.cycle
    if (banked := cycle.banked_walk(ctx.round_num)) is not None:
        cycle.seat_walk(ctx.round_num, banked.walked)
        await reread_walk(ctx, banked.reads)
        return Proposals(list(banked.proposals))
    seeds, proposers, _ = walk.walked
    invalid: list[Proposal] = []
    if seeds:
        seeded = await _walk(ctx, seeds)
        cycle.population = [p.individual for p in seeded.proposals if not p.invalid]
        # An invalid seed is an arm of its round, as every invalid child is: its fault is served.
        invalid = [p for p in seeded.proposals if p.invalid]
    population = await _walk(ctx, proposers)
    population = replace(population, proposals=[*population.proposals, *invalid])
    bank_walk(ctx, population.proposals)
    return population


async def propose_population(ctx: RoundContext, walk: RoundWalk) -> Proposals:
    population = await _walked(ctx, walk)
    arms = _one_arm_each(ctx, population.proposals)
    seated = walk.walked[2]
    if seated:
        arms = _one_arm_each(ctx, await operator_arms(ctx, arms))
    return replace(population, proposals=arms)


async def _adapt(ctx: RoundContext, plan: RoundPlan) -> None:
    if not plan.adapters:
        return
    append_record(PhaseRecord(phase=CampaignPhase.ADAPT, step="enter", round=ctx.round_num))
    for adapter in plan.adapters:
        # Survived here: the next walk re-runs it first (`settle_owed_outputs`).
        try:
            await adapter.adapt(NodeContext(ctx, adapter))
        except (KeyboardInterrupt, asyncio.CancelledError, SendRefusedError):
            raise
        except Exception as fault:  # noqa: BLE001 -- any fault of an adapter is its round's warning
            said = f"round {ctx.round_num} {adapter.name} landed nothing"
            append_record(
                RoundWarningRecord.handled(
                    "adapter_landed_nothing", fault, said=said, round=ctx.round_num
                )
            )
    append_record(PhaseRecord(phase=CampaignPhase.ADAPT, step="exit", round=ctx.round_num))


class ControllerAct(NamedTuple):
    """The rule a controller banked off its round, acted on once the round closes (`post_round`)."""

    controller: Controller
    rule: str


class RoundWalked(NamedTuple):
    ctx: RoundContext
    result: RoundResult
    cut: StopLoop | None
    act: ControllerAct | None


async def _proposed(ctx: RoundContext) -> Proposals:
    walk = ctx.cycle.optimizer.plan.walk(stands=bool(ctx.cycle.population))
    opening = announce_opening(ctx, walk)
    population = await propose_population(ctx, walk)
    announce_population(ctx, opening, population)
    return population


async def _measured(ctx: RoundContext, population: Proposals, pool: list[Sample]) -> MeasuredRound:
    plan = ctx.cycle.optimizer.plan
    eliminator = plan.eliminator
    race = None if eliminator is None else eliminator.race(NodeContext(ctx, eliminator), ctx.panel)
    append_record(
        PhaseRecord(
            phase=CampaignPhase.MEASURE,
            step="enter",
            round=ctx.round_num,
            act=MeasureOpened(
                candidates=len(population.proposals),
                cells=len(ctx.panel.cells),
                rows="random" if race is None else race.rows_ran,
            ),
        )
    )
    measured = await measure_population(ctx, population, pool, eliminator, race, plan.selector)
    append_record(PhaseRecord(phase=CampaignPhase.MEASURE, step="exit", round=ctx.round_num))
    return measured


def _survivors(ctx: RoundContext, selection: Selection) -> list[OptSearchPoint]:
    survivor = ctx.cycle.optimizer.plan.survivor
    if survivor is None:
        return list(selection.ranked)
    return list(survivor.survive(NodeContext(ctx, survivor), selection.ranked))


def _crowned(ctx: RoundContext, measured: MeasuredRound, selection: Selection) -> RoundResult:
    """The bench's close of the election: the round's result, gated on a whole panel, then absorbed."""
    cycle = ctx.cycle
    result = _round_result(ctx, measured, selection, elects_on=cycle.optimizer.elects_on)
    append_record(PhaseRecord(phase=CampaignPhase.SELECT, step="exit", round=ctx.round_num))
    _panel_gate(ctx, result, measured.cut)
    # AFTER the panel gate: a round halted on a holed panel is unwound and re-run, never crowned.
    assert result.election is not None
    append_record(ElectionRecord(round=ctx.round_num, **dict(result.election)))
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
    reading, bought = await measure_overlap(cycle, round_result, pool, list(measured.origin_panel))
    seated = cycle.seat(round_result.read_on_panel(reading, bought))
    # An adapter writes for the NEXT round, so none runs when no round follows: the standing the
    # close will state decides it, the one reading the close stops on.
    lives = cycle.config.optimization.lives
    standing = RunStanding.after(
        [*(rr for rr in cycle.rounds if rr.round < seated.round), seated],
        lives=None if lives is None else lives.bank,
        # No stop reads what the line paid.
        outlay=LineOutlay(search_usd=None, worked_s=None),
    )
    will_stop = (
        is_final_round
        or measured.cut is not None
        or standing_tripped(standing, cycle.config.optimization) is not None
    )
    if population.proposals and round_result.results and not will_stop:
        await _adapt(ctx, cycle.optimizer.plan)
    # Re-read: an adapter emits onto the round it adapts to.
    return cycle.rounds[-1]


async def walk_round(cycle: Cycle, round_num: int) -> tuple[RoundContext, list[Sample], Proposals]:
    """A round's open through its banked walk: a measured round's prefix."""
    # The search split alone: a bench or demo row a round could draw would elect on held-out rows.
    pool = list(cycle.session.state.scoring.partition.search)
    ctx = RoundContext(cycle=cycle, round_num=round_num)
    await settle_owed_outputs(ctx)
    # Before the draw, which reads the scale the election will.
    banked = cycle.banked_walk(round_num)
    reach = ruler_clock(cycle.session) if banked is None else banked.opened_on
    cycle.difficulty.open_round(cycle.rounds, reach)
    sampler = cycle.optimizer.plan.sampler
    ctx.drew = sampler.draw(NodeContext(ctx, sampler), pool)
    return ctx, pool, await _proposed(ctx)


async def execute_round(
    cycle: Cycle,
    round_num: int,
    *,
    is_final_round: bool = False,
) -> RoundWalked:
    """One round, each role handed what the ones before it left."""
    ctx, pool, population = await walk_round(cycle, round_num)
    plan = cycle.optimizer.plan
    measured = await _measured(ctx, population, pool)
    append_record(PhaseRecord(phase=CampaignPhase.SELECT, step="enter", round=round_num))
    selection = plan.selector.select(NodeContext(ctx, plan.selector), measured)
    cycle.population = _survivors(ctx, selection)
    result = _crowned(ctx, measured, selection)
    # δ folds after the election, which read the race's ruler: the round's cells reach the next draw.
    cycle.difficulty.fold(result)

    result = await _adapt_beside_overlap(
        ctx, result, measured, population, pool, is_final_round=is_final_round
    )
    act = None
    if (controller := plan.controller) is not None and (
        rule := controller.bank_round(NodeContext(ctx, controller), result)
    ) is not None:
        act = ControllerAct(controller, rule)
    return RoundWalked(ctx, result, measured.cut, act)


async def emit_origin_round(ctx: RoundContext, session: Session) -> None:
    cycle = ctx.cycle
    if cycle.origin_round.results:
        await _adapt(ctx, cycle.optimizer.plan)
    round_result = cycle.origin_round

    # The origin's cells fold after the election that adopted it, and before the close.
    assert round_result.election is not None, "round 0 elects the origin it adopted"
    append_record(ElectionRecord(round=0, **dict(round_result.election)))
    cycle.difficulty.fold(round_result)
    # Unspent here, a look-ahead press during the origin's scoring widens round 1 too.
    session.state.control.spend_sample_lookahead()
    await close_round(cycle, round_result, 0, session)


def flush_pending_decisions(cycle: Cycle) -> None:
    """The controller acts after its round persisted; unflushed at a stop, a resume re-spends the act."""
    while cycle.pending_decisions:
        append_record(cycle.pending_decisions[0])
        cycle.pending_decisions.pop(0)


async def close_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    session: Session,
) -> RoundResult:
    stood = round_result.model_copy(update={"optimizer_state": cycle.close_state(round_num)})
    facts = cycle.optimizer.round_facts(stood)
    closing = cycle.seat(stood.model_copy(update={"optimizer_facts": facts}))
    flush_pending_decisions(cycle)
    close = closed_round(
        closing,
        cycle.rounds,
        lives=cycle.config.optimization.lives,
        position_of=cycle.session.bank.position_of,
        outlay=line_outlay(session, cycle.optimizer.phases),
    )
    cycle.standing = close.run_standing
    # Before the close it reads beside: a series no close stands on is read by nothing.
    append_record(line_series(closing, cycle.rounds, cycle.difficulty))
    offset = append_record(close)
    assert offset is not None, "a round closes on the run's ledger, never a muted one"
    round_result = cycle.seat(closing.model_copy(update={"at_offset": offset}))
    if cycle.sample_index is not None:
        cycle.sample_index.refresh(
            session.store,
            scorer=session.state.scoring.scorer,
        )
    return round_result


async def post_round(
    cycle: Cycle,
    walked: RoundWalked,
    session: Session,
    *,
    is_final_round: bool = False,
) -> None:
    round_num = walked.ctx.round_num
    round_result = await close_round(cycle, walked.result, round_num, session)

    # Before the stop below, so a perfect round that also ends the campaign still gets its check.
    fold, election = round_result.fold, round_result.election
    await verify_on_saturation(
        stores=session.store,
        at=session.at,
        round_num=round_num,
        fitness=None if fold is None else fold.fitness,
        winner_id=None if election is None else election.selected,
        seed=cycle.rng(round_num, "verify").getrandbits(31),
    )
    reserve_selection_pass(cycle, session)

    assert cycle.standing is not None, "a boundary stop reads the close it follows"
    if (stop := standing_tripped(cycle.standing, cycle.config.optimization)) is not None:
        raise StopLoop(stop)
    if (act := walked.act) is not None and not is_final_round:
        await act.controller.act(NodeContext(walked.ctx, act.controller), act.rule)
