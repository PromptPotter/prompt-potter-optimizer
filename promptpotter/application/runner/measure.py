"""The round's MEASURE phase: its population walked on the round's cells, and the origin
panel's overlap measured beside it (`measure_overlap`).

Why the origin panel is fixed at C0 rather than re-chosen: ``domain/origin_panel.py::origin_panel``."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from promptpotter.application.bench.cycle import Cycle, ReadArm, RoundContext
from promptpotter.application.bench.node_context import (
    NodeContext,
    measure_as_parent,
)
from promptpotter.application.optimizer_contract import (
    MeasuredRound,
    Proposals,
    RoundSamples,
)
from promptpotter.application.run_stop import StopLoop
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
from promptpotter.domain.arms import (
    Concluded,
    Cut,
    Elimination,
    Measured,
    Measuring,
    Proposed,
    arm_name,
    is_electable,
    is_leader_eligible,
    walked_short,
)
from promptpotter.domain.cell_fold import measured_cells
from promptpotter.domain.fitness_fold import arm_folds
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
from promptpotter.domain.records.run_records import (
    ArmRecord,
)
from promptpotter.domain.results import InRunCells, RoundResult
from promptpotter.domain.run_standing import (
    LineStep,
    best_line,
    walked_position,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.infrastructure.llm.telemetry import append_record
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope

if TYPE_CHECKING:
    from promptpotter.application.optimizer_contract import (
        Eliminator,
        Race,
        Selector,
    )
    from promptpotter.application.scoring.race_protocol import Decider
    from promptpotter.application.scoring.search_point_scorer import ScoredWalk


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
