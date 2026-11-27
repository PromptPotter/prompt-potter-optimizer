"""The round's arms are the operator's answer: a kept draft passes untouched, an arm of theirs edits the parent.
The gate's wait (`hold_for_operator`) writes nothing of its own, so it rides the shared heartbeat.

Why the origin panel is fixed at C0 rather than re-chosen: ``domain/origin_panel.py::origin_panel``."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.bench.cycle import Cycle, ReadArm, RoundContext
from promptpotter.application.bench.difficulty import ruler_clock
from promptpotter.application.bench.node_context import (
    Children,
    NodeContext,
    bank_proposals,
    closed_round,
    measure_as_parent,
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
    RoundSamples,
    RoundWalk,
)
from promptpotter.application.run_stop import StopLoop, panel_gate_tripped, standing_tripped
from promptpotter.application.runner.bench import (
    line_outlay,
    reserve_selection_pass,
)
from promptpotter.application.scoring.arm_states import (
    conclude,
    cut_invalid,
    cut_past_draw,
    measuring,
)
from promptpotter.application.scoring.paired import (
    MemberRows,
    RoundPairs,
    fresh_cells,
    grade_measurands,
    read_pair,
)
from promptpotter.application.scoring.passes import verify_on_saturation
from promptpotter.application.scoring.query_loop import (
    CatchUps,
    Turn,
    Walk,
    filed_entry,
    open_walk,
    run_walks,
)
from promptpotter.application.scoring.search_point_scorer import (
    SCORING_ERROR_ABORT,
    close_walk,
    reread_cells,
    score_search_points,
)
from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.arms import (
    ArmState,
    Concluded,
    Cut,
    Elimination,
    Measured,
    Measuring,
    Proposal,
    Proposed,
    arm_name,
    is_electable,
    is_leader_eligible,
    walked_short,
)
from promptpotter.domain.cell_fold import measured_cells, repairable_holes
from promptpotter.domain.command_kinds import SubmittedArm
from promptpotter.domain.fitness_fold import arm_folds
from promptpotter.domain.opt_search_point import Locus, OptSearchPoint, loci_of
from promptpotter.domain.optimizer_state import RoundOutput
from promptpotter.domain.origin_panel import (
    OverlapReading,
    PanelTrigger,
    TriggeredReading,
    origin_panel,
    panel_trigger,
)
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    ArmKey,
    ArmName,
    CellSet,
    CellSetBasis,
    CellSetName,
    MemberAddress,
    PairedReading,
    PairMember,
    ReadingState,
    UnreadPair,
    UnreadState,
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
from promptpotter.domain.records.run_records import (
    ArmAct,
    ArmRecord,
    ArmsGate,
    ElectionRecord,
    PanelCoverage,
    PhaseRecord,
    RoundWarningRecord,
    Running,
    RunPhaseRecord,
)
from promptpotter.domain.results import Election, InRunCells, RoundResult, round_document_digest
from promptpotter.domain.run_standing import (
    LineOutlay,
    LineStep,
    RunStanding,
    best_line,
    line_reference,
    rounds_without_advance,
    walked_position,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell
from promptpotter.domain.search_point import SHOTS_KEY, JobSearchPoint
from promptpotter.domain.wounds import ValidationFailure, ValidationReason
from promptpotter.infrastructure.llm.heartbeat import CallBeat, heartbeat
from promptpotter.infrastructure.llm.telemetry import append_record
from promptpotter.shared.errors import SendRefusedError, fault_said
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope

if TYPE_CHECKING:
    from promptpotter.application.optimizer_contract import (
        Controller,
        Eliminator,
        Proposer,
        Race,
        Selection,
        Selector,
    )
    from promptpotter.application.optimizer_manifest import RoundPlan
    from promptpotter.application.scoring.race_protocol import Decider
    from promptpotter.application.scoring.search_point_scorer import ScoredWalk


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


def round_origin_panel(cycle: Cycle, scoring_pool: list[Sample]) -> list[int]:
    """Fixed before the round's election, so its own pick joins the line's cells from the next."""
    pairs = _OriginPanelPairs(cycle, scoring_pool)
    ordered = sorted(best_line(cycle.rounds), key=lambda s: s.arm.round)
    held = cycle.in_run_cells
    # The origin's OWN round, never every cell read since: a later re-score would move the exam.
    origin_round = next(rr for rr in cycle.rounds if rr.round == ordered[0].arm.round)
    size = cycle.config.origin_panel_cells
    return origin_panel(
        pairs.walked(origin_round.results),
        line_cells=set[int]().union(
            *(pairs.measured(pairs.cells_of(s, held)) for s in ordered[1:])
        ),
        poolable={s.id for s in scoring_pool},
        benchmark_of={s.id: s.measured_dataset for s in scoring_pool},
        size=cycle.optimizer.round_cells(len(scoring_pool)) if size is None else size,
    )


async def read_origin_panel(
    cycle: Cycle,
    members: Sequence[tuple[LineStep, CellSheet]],
    panel: Sequence[int],
    scoring_pool: list[Sample],
    *,
    buys: bool,
    race: Race | None,
) -> tuple[dict[str, CellSheet], dict[ArmKey, Elimination]]:
    """Each member on the panel cells its draw left, for the election to pool; where *buys* is false the archive alone answers.
    The round's *race* walks on over what it buys, and each arm it reads as beaten there is returned cut."""
    want = set(panel)
    gaps = {
        step.opt_sp.id: (step, samples)
        for step, drawn in members
        if (
            samples := [
                s for s in scoring_pool if s.id in want and s.key not in {c.key for c in drawn}
            ]
        )
    }
    if not buys:
        return {
            member: reread_cells(
                cycle.points.job(step.opt_sp), samples, cycle.session, kind=MeasurementRole.OVERLAP
            )
            for member, (step, samples) in gaps.items()
        }, {}
    top_up = None if race is None else race.top_up
    scored = await _measure_gaps(cycle, gaps, top_up=top_up)
    cuts: dict[ArmKey, Elimination] = {}
    if race is not None and top_up is not None:
        labels = {ArmKey(member): step.arm.label for member, (step, _) in gaps.items()}
        for member, walk in scored.items():
            if (cut := race.judge(walk.signal, ArmKey(member), labels)) is not None:
                cuts[ArmKey(member)] = cut
    return {member: walk.sheet for member, walk in scored.items()}, cuts


async def measure_overlap(
    cycle: Cycle, round_result: RoundResult, scoring_pool: list[Sample], panel: Sequence[int]
) -> tuple[OverlapReading, dict[str, CellSheet]]:
    """After the election, on the panel it pooled; a member the election's pass read is never topped up again.
    What it buys is the panel's `bought`, never the race the election read."""
    passed = {key: round_result.panel_rows[key] for key in round_result.origin_panel.raced}
    election = round_result.election
    assert election is not None, "the origin panel is read after the round's election"
    ending = election.ending
    not_separated = cycle.config.optimization.not_separated_stall
    pairs = _OriginPanelPairs(cycle, scoring_pool)
    # The line INCLUDING this round, which may have made its own result the new best.
    ordered = sorted(best_line(cycle.rounds), key=lambda s: s.arm.round)
    held = cycle.in_run_cells
    origin = ordered[0]
    keep = set(panel)
    bought: dict[str, CellSheet] = {}

    def spent() -> dict[str, int]:
        return {cid: fresh_cells(rows) for cid, rows in (passed | bought).items()}

    def not_read(state: UnreadState) -> OverlapReading:
        return OverlapReading.of(
            ending,
            positions=panel,
            lead=pairs.absent(state, origin, ordered[-1], held, keep, spent()),
            earlier=(),
            triggered=(),
            vs_previous_pick=None,
            not_separated=not_separated,
        )

    def gaps(step: LineStep) -> set[int]:
        return keep - pairs.measured(pairs.cells_of(step, held))

    on_line = {s.opt_sp.id for s in ordered}
    fired = [
        (step, trigger)
        for step, trigger in panel_triggers(
            cycle.rounds, cycle.config.optimization.elimination_n_min
        )
        if step.opt_sp.id not in on_line
    ]
    if len(ordered) < 2 and not fired:
        return not_read(ReadingState.SAME_INDIVIDUAL), bought
    if not panel:
        return not_read(ReadingState.NOT_HELD), bought

    # A member short of the set breaks the one claim the reading makes; one the election's pass
    # left short is never topped up, so it breaks the claim before anything is bought.
    if any(gaps(step) and step.opt_sp.id in passed for step in ordered):
        return not_read(ReadingState.PASS_STOPPED), bought
    owed = {
        step.opt_sp.id: (step, [s for s in scoring_pool if s.id in short])
        for step in [*ordered, *(step for step, _ in fired)]
        if (short := gaps(step)) and step.opt_sp.id not in passed
    }
    scored = await _measure_gaps(cycle, owed)
    bought |= {member: walk.sheet for member, walk in scored.items() if walk.stopped is None}

    def whole(step: LineStep) -> bool:
        topped = scored.get(step.opt_sp.id)
        return not gaps(step) or (topped is not None and topped.stopped is None)

    if not all(whole(step) for step in ordered):
        return not_read(ReadingState.PASS_STOPPED), bought
    # Monitoring, never the line's claim: an arm short of the set reads as stopped, the line stands.
    held_whole = [(step, trigger) for step, trigger in fired if whole(step)]
    stopped = [(step, trigger) for step, trigger in fired if not whole(step)]

    # Read again off the round as it will CLOSE, so what was bought joins by the one read.
    closing = round_result.read_on_panel(round_result.origin_panel.reading, bought)
    closed = [closing if rr.round == round_result.round else rr for rr in cycle.rounds]
    c0, *picks = sorted(best_line(closed), key=lambda s: s.arm.round)
    topped_up = InRunCells(closed)
    paid = spent()
    readings = [pairs.read(c0, pick, topped_up, keep, paid) for pick in picks]
    reading = OverlapReading.of(
        ending,
        positions=panel,
        lead=readings[-1]
        if readings
        else pairs.absent(ReadingState.SAME_INDIVIDUAL, c0, c0, held, keep, paid),
        earlier=readings[:-1],
        triggered=[
            *(
                TriggeredReading(
                    trigger=trigger, reading=pairs.read(c0, step, topped_up, keep, paid)
                )
                for step, trigger in held_whole
            ),
            *(
                TriggeredReading(
                    trigger=trigger,
                    reading=pairs.absent(ReadingState.PASS_STOPPED, c0, step, held, keep, paid),
                )
                for step, trigger in stopped
            ),
        ],
        vs_previous_pick=pairs.read(picks[-2], picks[-1], topped_up, keep, paid)
        if len(picks) >= 2
        else None,
        not_separated=not_separated,
    )
    return reading, bought


def panel_triggers(
    rounds: Sequence[RoundResult], n_min: int
) -> list[tuple[LineStep, PanelTrigger]]:
    """Every arm of the cycle walked to its draw's end a trigger fired for, by first walk."""
    fired: dict[str, tuple[LineStep, PanelTrigger]] = {}
    for rr in rounds:
        for key, arm in rr.arms.items():
            trigger = panel_trigger(arm, rr.rows(key), n_min)
            if trigger is not None and arm.individual.id not in fired:
                step = LineStep(arm=arm_name(key, arm, rr.round), opt_sp=arm.individual)
                fired[arm.individual.id] = (step, trigger)
    return list(fired.values())


class _OriginPanelPairs:
    def __init__(self, cycle: Cycle, scoring_pool: list[Sample]) -> None:
        session = cycle.session
        self._path = (session.hop,)
        self._instrument = session.instrument_id
        self._scorer = session.state.scoring.scorer
        self._dataset = session.bank.dataset_hash
        self._key_of = {int(s.id): s.key for s in scoring_pool}
        self._position_of = session.bank.position_of

    @staticmethod
    def cells_of(step: LineStep, held: InRunCells) -> CellSheet:
        return held.sheet(step.opt_sp.id, RoleScope.REPORT)

    def walked(self, rows: Iterable[GradedCell]) -> list[int]:
        """By bank slot, in the order the rows were walked: the panel is served as the sample ids the
        operator selects on."""
        return list(dict.fromkeys(self._position_of[cell.key] for cell in rows if cell.scored))

    def measured(self, rows: Iterable[GradedCell]) -> set[int]:
        return set(self.walked(rows))

    def read(
        self,
        a: LineStep,
        b: LineStep,
        held: InRunCells,
        panel: set[int],
        paid: dict[str, int],
    ) -> PairedReading:
        return read_pair(
            a=self._member(a, held, panel, paid),
            b=self._member(b, held, panel, paid),
            cell_set=CellSetName.ORIGIN_PANEL,
            cells=[self._key_of[sid] for sid in panel],
            masked=False,
            dataset_hash=self._dataset,
            measurands=grade_measurands(self._scorer),
            spec=ROUND_LIFT_SPEC,
            scope=RoleScope.REPORT,
            instrument_id=self._instrument,
        )

    def absent(
        self,
        state: UnreadState,
        c0: LineStep,
        pick: LineStep,
        held: InRunCells,
        panel: set[int],
        paid: dict[str, int],
    ) -> PairedReading:
        def member(step: LineStep) -> PairMember:
            return PairMember(
                address=self._address(step),
                n=len(self.measured(self.cells_of(step, held)) & panel),
                bought=paid.get(step.opt_sp.id, 0),
            )

        return UnreadPair(
            state=state,
            a=member(c0),
            b=member(pick),
            cell_set=CellSet.of(
                CellSetName.ORIGIN_PANEL,
                CellSetBasis.DECLARED,
                [self._key_of[sid] for sid in panel],
                dataset_hash=self._dataset,
            ),
            scope=RoleScope.REPORT,
            spec=ROUND_LIFT_SPEC,
            instrument_id=self._instrument,
        )

    def _address(self, step: LineStep) -> MemberAddress:
        return MemberAddress(
            path=self._path,
            individual_id=step.opt_sp.id,
            arm=step.arm,
            # A fold over every pass report scope may see, so no one pass names it.
            pass_role=None,
        )

    def _member(
        self, step: LineStep, held: InRunCells, panel: set[int], paid: dict[str, int]
    ) -> MemberRows:
        sheet = self.cells_of(step, held)
        return MemberRows(
            address=self._address(step),
            sheet=sheet,
            bought=paid.get(step.opt_sp.id, 0),
            # Its walk over the panel, the raced top-up included, stopped short of it.
            cut=bool(panel - self.measured(sheet)),
            scope=RoleScope.REPORT,
            instrument_id=self._instrument,
            dataset_hash=self._dataset,
            cell_set_id=None,
        )


async def _measure_gaps(
    cycle: Cycle,
    gaps: Mapping[str, tuple[LineStep, list[Sample]]],
    *,
    top_up: Decider[ArmKey] | None = None,
) -> dict[str, ScoredWalk]:
    """Every member's gaps in ONE pass, each on its OWN configuration, never the round subject's;
    *top_up* races them on."""
    if not gaps:
        return {}
    raced = await score_search_points(
        {
            ArmKey(member): (
                cycle.points.job(step.opt_sp),
                samples,
                step.opt_sp.id,
            )
            for member, (step, samples) in gaps.items()
        },
        cycle.session,
        # One run per (member, cell set), so a re-run of the same round replays it free.
        kind=MeasurementRole.OVERLAP,
        race=top_up,
    )
    return {member: raced[ArmKey(member)] for member in gaps}


async def measure_population(
    ctx: RoundContext,
    population: Proposals,
    pool: list[Sample],
    eliminator: Eliminator | None,
    race: Race | None,
    selector: Selector,
) -> MeasuredRound:
    cycle = ctx.cycle
    panel = ctx.panel
    arms, rows, cut, caught = await _walk_population(
        ctx, population, eliminator, race, keep_cut=selector.elects_partial
    )
    # Clamped so a tiny dataset stays electable.
    coverage_floor = min(cycle.config.optimization.elimination_n_min, len(panel.cells))
    seat = NodeContext(ctx, selector)

    def readable(key: ArmKey, parent: CellSheet) -> bool:
        answered = {cell.key for cell in parent.scoreable}
        return selector.covered(cycle.optimizer, rows[key], answered) >= coverage_floor

    # The REPLICATION cohort, deliberately the looser predicate: the election admits on `is_electable`.
    scored = [key for key, arm in arms.items() if is_leader_eligible(arm)]
    cells = selector.parent_cells(seat, panel, rows)
    if cut is not None:
        # The spent budget buys the parent no cell: it is read where the archive already holds it.
        held = reread_cells(cycle.parent_point, cells, cycle.session, kind=MeasurementRole.PARENT)
        on_disk = held.by_key()
        cells = [sample for sample in cells if sample.key in on_disk]
        # Nothing the cut left can be elected, so the round is unwound as an unkept cut is.
        if not cells or not any(readable(key, held) for key in rows):
            raise cut
    parent = await measure_as_parent(
        ctx, cycle.parent, cycle.parent_point, cells, role=MeasurementRole.PARENT
    )
    if parent.key not in arms and (caught_up := caught.get(parent.key)):
        # The cells its race caught the parent up on are its round reading's rows, as an arm's are.
        taken = ReadArm(parent.key, parent.state, replace(parent.rows, cells=tuple(caught_up)))
        ctx.walked(taken, ctx.asked(parent.key, ()))
        parent = ctx.walks[parent.key].arm
    key_of = {s.id: s.key for s in pool}
    origin_panel = {slot: key_of[slot] for slot in round_origin_panel(cycle, pool)}
    on_panel, topped_cuts = await read_origin_panel(
        cycle,
        [
            *(
                (LineStep(arm_name(key, arm, ctx.round_num), arm.individual), rows[key])
                for key, arm in arms.items()
                # A stopped arm buys no cell after its stop: the election reads it on its draw.
                if len(measured_cells(rows[key])) >= coverage_floor and not walked_short(arm)
            ),
            (
                LineStep(arm_name(parent.key, parent.state, ctx.round_num), parent.individual),
                parent.rows,
            ),
        ],
        list(origin_panel),
        pool,
        buys=cut is None,
        race=race,
    )
    for key, elimination in topped_cuts.items():
        topped = arms[key]
        assert isinstance(topped, Measured), "only an arm walked to its draw's end is topped up"
        arms[key] = stopped = cut_past_draw(topped, elimination)
        ctx.cut_on(key, stopped)
        append_record(ArmRecord(round=ctx.round_num, arm=key, state=stopped))
    raced = {
        key: sheet.merged(on_panel.get(key, NO_CELLS))
        for key, sheet in {**rows, parent.key: parent.rows}.items()
    }
    # Never in a `finally`: an unwound round did not score. Round 0's is `emit_origin_round`.
    cycle.session.state.control.spend_sample_lookahead()
    # The shared anchor's single-draw noise is correlated across arms, so it favours none.
    session = cycle.session
    pairs = RoundPairs(
        path=(session.hop,),
        instrument_id=session.instrument_id,
        dataset_hash=session.bank.dataset_hash,
        measurands=tuple(grade_measurands(session.state.scoring.scorer)),
    )
    pair = ReferencePairs.of_round(
        ctx,
        arms,
        pairs,
        declared=[cell.key for cell in parent.rows],
    )
    readings = {
        key: pair.read(ReadArm(key, arm, rows[key]), parent.key, parent)
        for key, arm in arms.items()
        if key in rows
    }
    rejects = eliminator is not None and eliminator.stop_disqualifies
    electable: list[ArmKey] = []
    for key in scored:
        arm = arms[key]
        # A collapsed arm is read above and still refused entry — it keeps its reading.
        if not is_electable(arm, rows[key].cells, parent.rows):
            continue
        if rejects and isinstance(arm, Cut) and arm.kind == "eliminated":
            continue
        # An arm the selector cannot read for a reason other than elimination: an operator skip.
        if not readable(key, parent.rows):
            continue
        electable.append(key)

    filed = {key: ctx.walks[key].arm for key in arms} | {
        key: walk.arm for key, walk in ctx.walks.items() if key not in arms
    }
    return MeasuredRound(
        arms=arms,
        rows=rows,
        parent=parent,
        filed=filed,
        folds=arm_folds(
            {key: read.state for key, read in filed.items()},
            {key: read.rows for key, read in filed.items()},
            cycle.session.state.scoring.scorer,
            cycle.session.bound_resolution.chain,
        ),
        vs_reference={key: read for key in arms if (read := readings.get(key)) is not None},
        electable=electable,
        coverage_floor=coverage_floor,
        cut=cut,
        origin_panel=origin_panel,
        on_panel=on_panel,
        raced=raced,
        pairs=pairs,
    )


@dataclass(frozen=True, kw_only=True)
class ReferencePairs:
    pairs: RoundPairs
    declared: Sequence[str]
    names: Mapping[ArmKey, ArmName]

    @classmethod
    def of_round(
        cls,
        ctx: RoundContext,
        arms: Mapping[ArmKey, Concluded],
        pairs: RoundPairs,
        *,
        declared: Sequence[str],
    ) -> ReferencePairs:
        # A re-read individual is named by this round's slot, never the one it was first walked in.
        names = {
            key: arm_name(key, arm, rr.round)
            for rr in ctx.cycle.rounds
            for key, arm in rr.arms.items()
        } | {key: arm_name(key, arm, ctx.round_num) for key, arm in arms.items()}
        return cls(pairs=pairs, declared=declared, names=names)

    def read(self, arm: ReadArm, against: ArmKey, reference: ReadArm | None) -> PairedReading:
        """*reference* is ``None`` where the round never read the individual *against* names."""
        pairs = self.pairs
        return pairs.read(
            pairs.member(
                against,
                NO_CELLS if reference is None else reference.rows,
                name=self.names.get(against),
                role=MeasurementRole.PARENT if reference is None else reference.state.role,
                cut=reference is not None and walked_short(reference.state),
            ),
            pairs.member(
                arm.key,
                arm.rows,
                name=self.names.get(arm.key),
                role=arm.state.role,
                cut=walked_short(arm.state),
            ),
            cell_set=CellSetName.REFERENCE_CELLS,
            cells=self.declared,
            measurands=pairs.measurands,
        )


async def _walk_population(
    ctx: RoundContext,
    population: Proposals,
    eliminator: Eliminator | None,
    race: Race | None,
    *,
    keep_cut: bool,
) -> tuple[
    dict[ArmKey, Concluded],
    dict[ArmKey, CellSheet],
    StopLoop | None,
    Mapping[ArmKey, Sequence[GradedCell]],
]:
    cycle = ctx.cycle
    round_num = ctx.round_num
    panel = ctx.panel
    proposed = population.arms(round_num)
    rows: dict[ArmKey, CellSheet] = {}
    concluded: dict[ArmKey, Concluded] = {}

    labels = {key: arm.label for key, arm in proposed.items()}
    order = [int(s.id) for s in panel.order]
    sps = {key: cycle.points.job(arm.individual) for key, arm in proposed.items()}
    catch_ups: CatchUps | None = None
    if eliminator is not None and race is not None:
        if (parent := ArmKey(cycle.parent.id)) not in proposed:
            # Announced before the walks, so its catch-up is its arm's pass, which its read continues.
            at = walked_position(cycle.rounds, parent)
            state = Proposed(role=MeasurementRole.PARENT, position=at, individual=cycle.parent)
            append_record(ArmRecord(round=round_num, arm=parent, state=state))
        catch_ups = CatchUps(
            race,
            eliminator.name,
            {parent: (cycle.parent_point, MeasurementRole.PARENT)}
            | {key: (sp, MeasurementRole.PANEL) for key, sp in sps.items()},
            cycle.sample_index,
        )
    asked = {key: ctx.asked(key, panel.order) for key in proposed}
    states = {
        key: measuring(
            arm,
            sps[key],
            filed_entry(sps[key], cycle.session),
            expected=len(asked[key]),
            sample_order=order,
        )
        for key, arm in proposed.items()
    }
    session = cycle.session
    standing = session.store.campaigns.standing_rounds(session.hop, moment=None)
    walks: dict[ArmKey, Walk | None] = {}
    for key, arm in proposed.items():
        walks[key] = _open_arm(ctx, arm, sps[key], panel, standing.skipped.get((round_num, key)))

    def on_turn(key: ArmKey, turn: Turn) -> None:
        states[key] = states[key].model_copy(
            update={
                # The rows its race chose, which the readout heads.
                "sample_order": order if turn.order is None else [int(s.id) for s in turn.order],
                "n_priors": race.n_priors if race is not None else 0,
                "block": None
                if turn.block is None
                else {
                    "n": turn.block + 1,
                    "of": -(-len(order) // panel.block_size),
                    "size": panel.block_size,
                    "racing": turn.racing,
                },
            }
        )
        append_record(ArmRecord(round=round_num, arm=key, state=states[key]))

    def on_decided(key: ArmKey, walked: Sequence[Sample] | None) -> None:
        if walked is not None:
            asked[key] = ctx.asked(key, walked)
        walk = walks[key]
        arm, results = _conclude_arm(ctx, states[key], walk, key, race, labels)
        rows[key] = results
        concluded[key] = arm
        ctx.walked(ReadArm(key, arm, results), asked[key])
        append_record(ArmRecord(round=round_num, arm=key, state=arm))

    cut, caught = await run_walks(
        walks,
        cycle.session,
        keep_cut=keep_cut,
        catch_ups=catch_ups,
        race=race,
        on_turn=on_turn,
        on_decided=on_decided,
    )
    # A prior's caught-up cells are its round reading's rows, so the race and the election read one
    # sheet; the parent's are handed on to its read, which continues its arm.
    for key, cells in caught.items():
        if key in concluded:
            taken = replace(rows[key], cells=tuple(cells))
            rows[key] = rows[key].filled_from(taken)
            ctx.walked(ReadArm(key, concluded[key], taken), asked[key])
    ranked = [key for key in proposed if key in concluded]
    return {key: concluded[key] for key in ranked}, {key: rows[key] for key in ranked}, cut, caught


def _open_arm(
    ctx: RoundContext,
    arm: Proposed,
    sp: JobSearchPoint,
    panel: RoundSamples,
    skip_at: int | None,
) -> Walk | None:
    if arm.invalid:
        return None
    return open_walk(
        sp,
        panel.walked,
        ctx.cycle.session,
        kind=MeasurementRole.PANEL,
        sample_index=ctx.cycle.sample_index,
        # Handed to the gateway rather than bound here, so no re-entrant asker inherits it.
        measured=arm.individual.id,
        skip_at=skip_at,
        budget=len(panel.order),
    )


def _conclude_arm(
    ctx: RoundContext,
    arm: Measuring,
    walk: Walk | None,
    key: ArmKey,
    race: Race | None,
    labels: Mapping[ArmKey, str],
) -> tuple[Concluded, CellSheet]:
    if walk is None:
        return cut_invalid(arm), NO_CELLS

    scored = close_walk(walk)
    results, signal = scored.sheet, scored.signal
    elimination = race.judge(signal, key, labels) if race is not None else None
    # Every cell taken, or locked in, so a later arm races the leader, caught up where it stopped;
    # never a walk the gateway gave up on, whose rows are synthetic.
    gave_up = signal is not None and signal.check_name == SCORING_ERROR_ABORT
    locked = signal is not None and signal.outcome == "locked_in"
    if race is not None and (scored.stopped is None or locked) and not gave_up:
        race.admit(key, results.cells)
    return conclude(arm, scored, round_num=ctx.round_num, elimination=elimination), results


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


def _panel_gate(ctx: RoundContext, round_result: RoundResult, cut: StopLoop | None) -> None:
    round_num = ctx.round_num
    proposed = round_result.proposed
    unbought = {
        key: _unbought_cells(arm, round_result.rows(key), cut) for key, arm in proposed.items()
    }
    holes = {key: repairable_holes(round_result.rows(key)) + unbought[key] for key in proposed}
    # A broken arm also carries error rows, but its own outcome already charges it.
    holed = sorted(key for key, arm in proposed.items() if is_leader_eligible(arm) and holes[key])
    # Sink is the LEDGER: the halt below unwinds before `close_round`.
    append_record(decision(PanelCoverage(holes_by_arm=holes), holed, node=None, round=round_num))
    tripped = panel_gate_tripped(
        [cell for key in holed for cell in round_result.rows(key) if cell.facts.hole],
        cut if any(unbought[cid] for cid in holed) else None,
        ctx.cycle.config.optimization.panel_gate,
    )
    if tripped is not None:
        raise tripped


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
        cycle.difficulty,
        lives=cycle.config.optimization.lives,
        position_of=cycle.session.bank.position_of,
        outlay=line_outlay(session, cycle.optimizer.phases),
    )
    cycle.standing = close.run_standing
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
