"""The round's arms are the operator's answer: a kept draft passes untouched, an arm of theirs edits the parent.
The gate's wait (`hold_for_operator`) writes nothing of its own, so it rides the shared heartbeat.

Why the origin panel is fixed at C0 rather than re-chosen: ``domain/origin_panel.py::origin_panel``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
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
    measure_as_parent,
    reread_walk,
    stated_line,
)
from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.bench.round_analysis import ended_on
from promptpotter.application.initialization.session import Session
from promptpotter.application.intelligence.rasch import (
    candidate_abilities,
    theta_lift_over_parent,
)
from promptpotter.application.optimizers.nodes import (
    OPERATOR_SEAT,
    MeasuredRound,
    Proposals,
    RoundOpening,
    RoundWalk,
)
from promptpotter.application.runner.bench import (
    line_outlay,
    reserve_selection_pass,
)
from promptpotter.application.runner.inner.tasks import pin_inner_ruler
from promptpotter.application.runner.termination import panel_gate_tripped, standing_tripped
from promptpotter.application.scoring.arm_states import conclude, cut_past_draw, measuring, refuse
from promptpotter.application.scoring.catch_up import CatchUpPool
from promptpotter.application.scoring.metrics import arm_folds
from promptpotter.application.scoring.paired import (
    MemberRows,
    absent_pair,
    fresh_cells,
    grade_measurands,
    read_pair,
)
from promptpotter.application.scoring.passes import verify_on_saturation
from promptpotter.application.scoring.query_loop import Walk, run_walks
from promptpotter.application.scoring.search_point_scorer import (
    SCORING_ERROR_ABORT,
    close_walk,
    open_walk,
    reread_cells,
    score_search_points,
)
from promptpotter.application.scoring.selection import cells_read, crowning_cells, discordant
from promptpotter.application.views.ingress import propose_enter, propose_exit, select_exit
from promptpotter.application.views.render.phase import stop_next_lines
from promptpotter.domain.arm_reading import ArmFit, DisplayMetric
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
from promptpotter.domain.cell_fold import measured_cells, unscoreable_cells
from promptpotter.domain.command_kinds import SubmittedArm
from promptpotter.domain.crowning_bar import BarRead, BarSubject
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.opt_search_point import Locus, OptSearchPoint, loci_of
from promptpotter.domain.optimizer_state import RoundOutput
from promptpotter.domain.origin_panel import (
    OverlapReading,
    PanelTrigger,
    TriggeredReading,
    election_rows,
    origin_panel,
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
)
from promptpotter.domain.phase_views import MeasureEnterView
from promptpotter.domain.phases import CampaignPhase, RunPhase, StopReason
from promptpotter.domain.pipeline_schema import INITIAL_POPULATION
from promptpotter.domain.records.run_records import (
    ArmRecord,
    ComparisonExit,
    ElectionRecord,
    PanelCoverage,
    PhaseRecord,
    RoundClosedRecord,
    RunPhaseRecord,
    StopLoop,
)
from promptpotter.domain.results import Election, InRunCells, RoundResult, round_document_digest
from promptpotter.domain.run_standing import (
    LineOutlay,
    LineStep,
    RunStanding,
    best_line,
    line_reference,
    rounds_without_advance,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell, Scorer
from promptpotter.domain.search_point import SHOTS_KEY, JobSearchPoint
from promptpotter.domain.wounds import ValidationFailure, collapse_counts
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.heartbeat import heartbeat
from promptpotter.infrastructure.llm.telemetry import active_cycle_ledger
from promptpotter.shared.errors import SendRefusedError, graceful
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import RoundPlan
    from promptpotter.application.optimizers.nodes import (
        Controller,
        Eliminator,
        Panel,
        Proposer,
        Race,
        Selection,
        Selector,
    )
    from promptpotter.application.scoring.query_loop import BlockRace
    from promptpotter.application.scoring.search_point_scorer import ScoredWalk

logger = logging.getLogger(__name__)


_POLL_S = 1.0


async def hold_for_operator[T](
    session: Session, take: Callable[[], T | None], *, node: str, round_num: int
) -> T | None:
    """What *take* first answers, or ``None`` where a pause reached the hold before an answer did."""
    beat = asyncio.create_task(
        heartbeat(
            active_cycle_ledger(),
            call_id=f"{node}:{session.cycle_id}",
            node=node,
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
            axis=locus.name, value=str(len(keys)), allowed=[str(cap)], reason="shots_over_cap"
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
    logger.warning(
        "Arm gate: round %d holds for the operator's arms (%d draft(s)). Answer with "
        "`submit-arms`.",
        ctx.round_num,
        len(drafts),
    )
    ctx.ledger.append(RunPhaseRecord(run_phase=RunPhase.GATE, gate="arms", drafts=list(drafts)))

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
        session.record_intervention("submit-arms")
        session.store.campaigns.leave_comparison(session.hop, ComparisonExit.SUBMIT_ARMS)
        bank_proposals(ctx, arms, submitted=True)
        session.state.control.take_submission(command_id)
        return arms

    arms = await hold_for_operator(session, _take, node=OPERATOR_SEAT.name, round_num=ctx.round_num)
    if arms is None:
        raise StopLoop(StopReason.PAUSED)
    ctx.ledger.append(RunPhaseRecord(run_phase=RunPhase.RUNNING))
    return arms


def round_origin_panel(cycle: Cycle, scoring_pool: list[Sample]) -> list[int]:
    """Fixed before the round's election, so its own pick joins the line's cells from the next."""
    pairs = _OriginPanelPairs(cycle, scoring_pool)
    ordered = sorted(best_line(cycle.rounds), key=lambda s: s.arm.round)
    held = cycle.in_run_cells
    # The origin's OWN round, never every cell read since: a later re-score would move the exam.
    origin_round = next(rr for rr in cycle.rounds if rr.round == ordered[0].arm.round)
    filed = cycle.session.measured_dataset
    return origin_panel(
        pairs.walked(origin_round.results),
        line_cells=set[int]().union(
            *(pairs.measured(pairs.cells_of(s, held)) for s in ordered[1:])
        ),
        poolable={s.id for s in scoring_pool},
        scope_of={s.id: s.measured_dataset or filed for s in scoring_pool},
        size=cycle.optimizer.round_cells(len(scoring_pool)),
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
    """After the election, on the panel it pooled; a member the election's pass read is never topped up again."""
    passed = dict(round_result.overlap_results)
    if round_result.head is None:
        return OverlapReading.unpaired(
            ReadingState.NO_SELECTION, round_result.round, round_result.improved
        ), passed
    pairs = _OriginPanelPairs(cycle, scoring_pool)
    # The line INCLUDING this round, which may have made its own result the new best.
    ordered = sorted(best_line(cycle.rounds), key=lambda s: s.arm.round)
    held = cycle.in_run_cells
    origin = ordered[0]
    keep = set(panel)
    bought = dict(passed)

    def spent() -> dict[str, int]:
        return {cid: fresh_cells(rows) for cid, rows in bought.items()}

    def not_read(state: ReadingState) -> OverlapReading:
        return OverlapReading.of(
            round_result.round,
            round_result.improved,
            positions=panel,
            lead=pairs.absent(state, origin, ordered[-1], held, keep, spent()),
            earlier=(),
            triggered=(),
            vs_parent=None,
            bar=None,
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
        logger.info(
            "overlap: round %d — the origin holds no cell this cycle can still buy, so no "
            "1-to-1 reading is possible",
            round_result.round,
        )
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
    closing = round_result.model_copy(update={"overlap_results": bought})
    closed = [closing if rr.round == round_result.round else rr for rr in cycle.rounds]
    c0, *picks = sorted(best_line(closed), key=lambda s: s.arm.round)
    topped_up = InRunCells(closed)
    paid = spent()
    readings = [pairs.read(c0, pick, topped_up, keep, paid) for pick in picks]
    reading = OverlapReading.of(
        round_result.round,
        round_result.improved,
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
        vs_parent=pairs.read(picks[-2], picks[-1], topped_up, keep, paid)
        if len(picks) >= 2
        else None,
        bar=cycle.crowning_bar(None),
    )
    return reading, bought


def panel_triggers(
    rounds: Sequence[RoundResult], n_min: int
) -> list[tuple[LineStep, PanelTrigger]]:
    """Every arm of the cycle walked to its draw's end a trigger fired for, by first walk."""
    fired: dict[str, tuple[LineStep, PanelTrigger]] = {}
    for rr in rounds:
        for key, arm in rr.arms.items():
            if (
                not isinstance(arm, Measured)
                or walked_short(arm)
                or arm.role is MeasurementRole.PARENT
                or arm.individual.id in fired
            ):
                continue
            if len(measured_cells(rr.rows(key))) >= n_min:
                step = LineStep(arm=arm_name(key, arm, rr.round), opt_sp=arm.individual)
                fired[arm.individual.id] = (step, PanelTrigger.N_MIN)
    return list(fired.values())


class _OriginPanelPairs:
    def __init__(self, cycle: Cycle, scoring_pool: list[Sample]) -> None:
        session = cycle.session
        self._path = (session.hop,)
        self._instrument = session.instrument_id
        self._scorer = session.state.scoring.scorer
        self._dataset = session.bank.dataset_hash
        self._key_of = {int(s.id): s.key for s in scoring_pool}
        self._slot_of = session.bank.slot_of

    @staticmethod
    def cells_of(step: LineStep, held: InRunCells) -> CellSheet:
        return held.sheet(step.opt_sp.id, RoleScope.REPORT)

    def walked(self, rows: Iterable[GradedCell]) -> list[int]:
        """By bank slot, in the order the rows were walked: the panel is served as the sample ids the
        operator selects on."""
        return list(dict.fromkeys(self._slot_of[cell.key] for cell in rows if cell.scored))

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
        state: ReadingState,
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

        return absent_pair(
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
    top_up: BlockRace[ArmKey] | None = None,
) -> dict[str, ScoredWalk]:
    """Every member's gaps in ONE pass, each on its OWN configuration, never the round subject's;
    *top_up* races them on."""
    if not gaps:
        return {}
    for step, samples in gaps.values():
        logger.info(
            "overlap: measuring %s on %d cell(s) of the origin panel", step.arm.label, len(samples)
        )
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
        blocks=top_up,
    )
    scored = {member: raced[ArmKey(member)] for member in gaps}
    for member, walk in scored.items():
        if walk.stopped is not None:
            step, samples = gaps[member]
            logger.warning(
                "overlap: %s stopped after %d/%d cell(s) (%s)",
                step.arm.label,
                len(walk.sheet),
                len(samples),
                walk.stopped,
            )
    return scored


async def measure_population(
    ctx: RoundContext,
    population: Proposals,
    panel: Panel,
    pool: list[Sample],
    eliminator: Eliminator | None,
    selector: Selector,
) -> MeasuredRound:
    cycle = ctx.cycle
    arms, rows, cut, race = await _walk_population(
        ctx, population, panel, eliminator, keep_cut=selector.elects_partial
    )
    # Clamped so a tiny dataset stays electable.
    coverage_floor = min(cycle.config.optimization.elimination_n_min, len(panel.cells))
    seat = NodeContext(ctx, selector)
    # Cold too: a selector elects on that θ, and the round's `AbilityReading.caveat` names it.
    ruler = cycle.difficulty.ruler

    def readable(key: ArmKey, parent: CellSheet) -> bool:
        answered = {cell.key for cell in parent.scoreable}
        return cells_read(selector.elects_on, rows[key], answered, ruler) >= coverage_floor

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
            raise StopLoop(cut)
    parent = await measure_as_parent(
        ctx, cycle.parent, cycle.parent_point, cells, role=MeasurementRole.PARENT
    )
    origin_panel_slots = round_origin_panel(cycle, pool)
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
        origin_panel_slots,
        pool,
        buys=cut is None,
        race=race,
    )
    for key, elimination in topped_cuts.items():
        topped = arms[key]
        assert isinstance(topped, Measured), "only an arm walked to its draw's end is topped up"
        arms[key] = stopped = cut_past_draw(topped, elimination)
        ctx.cut_on(key, stopped)
        ctx.ledger.append(
            ArmRecord(
                round=ctx.round_num,
                arm=key,
                state=stopped,
                anchors=cycle.session.state.view_context.anchors(),
            )
        )
    crowned = crowning_cells(cycle.rounds, parent.key)

    def pooled(key: ArmKey, drawn: CellSheet) -> CellSheet:
        return election_rows(drawn, on_panel.get(key, NO_CELLS), crowned)

    # Every cell the round bought joins the δ fit before a θ is read.
    cycle.difficulty.link(
        [
            (arm.sp_hash, rows[key].merged(on_panel.get(key, NO_CELLS)))
            for key, arm in arms.items()
            if arm.sp_hash
        ]
        + [(parent.state.sp_hash, parent.rows.merged(on_panel.get(parent.key, NO_CELLS)))]
    )
    ruler = cycle.difficulty.ruler
    # Never in a `finally`: an unwound round did not score. Round 0's is `emit_origin_round`.
    cycle.session.state.control.spend_sample_lookahead()
    # The shared anchor's single-draw noise is correlated across arms, so it favours none.
    pair = ReferencePairs.of_round(
        ctx,
        arms,
        declared=[cell.key for cell in parent.rows]
        if cycle.config.optimization.lift_reference == "best_so_far"
        else [sample.key for sample in panel.cells],
    )
    readings = await _reference_readings(ctx, panel, arms, rows, scored, parent, pair)
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

    # Fixed δ decouples the arms, so this is the fit `elect_round_winner` takes of the same rows.
    abilities = candidate_abilities(
        {key: pooled(key, rows[key]) for key in electable}, pooled(parent.key, parent.rows), ruler
    )
    fit = {
        key: ArmFit(
            theta=abilities.theta.get(key),
            theta_se=abilities.theta_se.get(key),
            theta_caveat=cycle.difficulty.scale.caveat(pooled(key, rows[key]).cells),
            vs_reference=readings.get(key),
            lift=theta_lift_over_parent(abilities, key),
        )
        for key in arms
    }
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
            cycle.session.pipeline_schema,
        ),
        fit=fit,
        electable=electable,
        coverage_floor=coverage_floor,
        cut=cut,
        origin_panel_slots=origin_panel_slots,
        on_panel=on_panel,
        crowned=crowned,
    )


async def _reference_readings(
    ctx: RoundContext,
    panel: Panel,
    arms: Mapping[ArmKey, Concluded],
    rows: Mapping[ArmKey, CellSheet],
    scored: Sequence[ArmKey],
    parent: ReadArm,
    pair: ReferencePairs,
) -> dict[ArmKey, PairedReading]:
    """Only a SCORED arm buys its parents a cell; any other is read on what those already bought."""
    cycle = ctx.cycle
    read = {key: ReadArm(key, arm, rows[key]) for key, arm in arms.items() if key in rows}
    if cycle.config.optimization.lift_reference == "best_so_far":
        return {key: pair.read(arm, parent.key, parent) for key, arm in read.items()}

    cells_of = {key: {cell.key for cell in sheet} for key, sheet in rows.items()}
    needed: dict[str, set[str]] = {}
    for key in scored:
        for pid in arms[key].individual.lineage.parent_ids:
            needed.setdefault(pid, set()).update(cells_of[key])
    # This round's individuals are not banked yet; every earlier one is, in a closed round.
    populated = {arm.individual.id: arm.individual for arm in arms.values()}
    for pid, keys in needed.items():
        if ArmKey(pid) == parent.key:
            continue
        individual = populated.get(pid) or cycle.measured(pid).individual
        await measure_as_parent(
            ctx,
            individual,
            cycle.points.job(individual),
            [s for s in panel.cells if s.key in keys],
            role=MeasurementRole.PARENT,
        )
    walks = ctx.walks

    def reference(pid: ArmKey, key: ArmKey) -> ReadArm | None:
        if (walk := walks.get(pid)) is None:
            return None
        rows = walk.arm.rows.where(lambda cell: cell.key in cells_of[key])
        return ReadArm(pid, walk.arm.state, rows)

    def against_better(key: ArmKey) -> tuple[ArmKey, PairedReading] | None:
        by_parent = {
            ArmKey(pid): pair.read(read[key], ArmKey(pid), reference(ArmKey(pid), key))
            for pid in read[key].individual.lineage.parent_ids
        }
        if not by_parent:
            return None

        def level(pid: ArmKey) -> float:
            lift = by_parent[pid].lift("fitness")
            return -math.inf if lift is None else lift.rate_a

        # `max` keeps the first of a tie, so a tie falls to `parent_ids` order.
        better = max(by_parent, key=level)
        return better, by_parent[better]

    read_against = {key: against for key in read if (against := against_better(key)) is not None}
    return {key: reading for key, (_, reading) in read_against.items()}


@dataclass(frozen=True, kw_only=True)
class ReferencePairs:
    path: tuple[CycleHop, ...]
    instrument_id: str
    scorer: Scorer
    dataset_hash: str
    declared: Sequence[str]
    names: Mapping[ArmKey, ArmName]

    @classmethod
    def of_round(
        cls,
        ctx: RoundContext,
        arms: Mapping[ArmKey, Concluded],
        *,
        declared: Sequence[str],
    ) -> ReferencePairs:
        session = ctx.cycle.session
        # A re-read individual is named by this round's slot, never the one it was first walked in.
        names = {
            key: arm_name(key, arm, rr.round)
            for rr in ctx.cycle.rounds
            for key, arm in rr.arms.items()
        } | {key: arm_name(key, arm, ctx.round_num) for key, arm in arms.items()}
        return cls(
            path=(session.hop,),
            instrument_id=session.instrument_id,
            scorer=session.state.scoring.scorer,
            dataset_hash=session.bank.dataset_hash,
            declared=declared,
            names=names,
        )

    def read(self, arm: ReadArm, against: ArmKey, reference: ReadArm | None) -> PairedReading:
        """*reference* is ``None`` where the round never read the individual *against* names."""
        return read_pair(
            a=self._member(
                against,
                MeasurementRole.PARENT if reference is None else reference.state.role,
                NO_CELLS if reference is None else reference.rows,
                cut=reference is not None and walked_short(reference.state),
            ),
            b=self._member(arm.key, arm.state.role, arm.rows, cut=walked_short(arm.state)),
            cell_set=CellSetName.REFERENCE_CELLS,
            cells=self.declared,
            masked=False,
            dataset_hash=self.dataset_hash,
            measurands=grade_measurands(self.scorer),
            spec=ROUND_LIFT_SPEC,
            scope=RoleScope.DECISION,
            instrument_id=self.instrument_id,
        )

    def _member(
        self,
        key: ArmKey,
        role: MeasurementRole,
        rows: CellSheet,
        *,
        cut: bool,
    ) -> MemberRows:
        return MemberRows(
            address=MemberAddress(
                path=self.path,
                individual_id=key,
                arm=self.names.get(key),
                pass_role=role,
            ),
            sheet=rows,
            bought=fresh_cells(rows),
            cut=cut,
            scope=RoleScope.DECISION,
            instrument_id=self.instrument_id,
            dataset_hash=self.dataset_hash,
            cell_set_id=None,
        )


async def _walk_population(
    ctx: RoundContext,
    population: Proposals,
    panel: Panel,
    eliminator: Eliminator | None,
    *,
    keep_cut: bool,
) -> tuple[dict[ArmKey, Concluded], dict[ArmKey, CellSheet], StopReason | None, Race | None]:
    cycle = ctx.cycle
    round_num = ctx.round_num
    schema = cycle.session.pipeline_schema
    proposed = population.arms(round_num)
    rows: dict[ArmKey, CellSheet] = {}
    concluded: dict[ArmKey, Concluded] = {}

    labels = {key: arm.label for key, arm in proposed.items()}
    order = [int(s.id) for s in panel.order]
    sps = {key: cycle.points.job(arm.individual) for key, arm in proposed.items()}
    race: Race | None = None
    pool: CatchUpPool | None = None
    if eliminator is not None:
        race = eliminator.race(NodeContext(ctx, eliminator), panel)
        pool = CatchUpPool(
            race,
            cycle.session,
            cycle.sample_index,
            eliminator.name,
            {ArmKey(cycle.parent.id): cycle.parent_point, **sps},
        )
    asked = {key: ctx.asked(key, panel.order) for key in proposed}
    states = {
        key: measuring(arm, sps[key], schema, expected=len(asked[key]), sample_order=order)
        for key, arm in proposed.items()
    }
    session = cycle.session
    skipped = session.store.campaigns.standing_rounds(session.hop, moment=None).skipped
    walks: dict[ArmKey, Walk | None] = {}
    for key, arm in proposed.items():
        walks[key] = _open_arm(ctx, arm, sps[key], panel.order, skipped.get((round_num, key)))

    blocks = race.blocks if race is not None else None

    def on_turn(key: ArmKey, block: int | None) -> None:
        if race is not None:
            race.open_turn(key)
        held = states[key].block
        racing = sum(1 for w in walks.values() if w is not None and w.outcome is None)
        # A turn inside a block race files the arm once per checkpoint that moved the field.
        if block and held is not None and held["racing"] == racing:
            return
        states[key] = states[key].model_copy(
            update={
                "n_priors": race.n_priors if race is not None else 0,
                "block": None
                if block is None or blocks is None
                else {
                    "n": block + 1,
                    "of": -(-len(order) // blocks.block_size),
                    "size": blocks.block_size,
                    "racing": racing,
                },
            }
        )
        ctx.ledger.append(
            ArmRecord(
                round=round_num,
                arm=key,
                state=states[key],
                anchors=session.state.view_context.anchors(),
            )
        )

    def on_decided(key: ArmKey) -> None:
        arm, results = _conclude_arm(ctx, states[key], walks[key], key, race, labels)
        rows[key] = results
        concluded[key] = arm
        ctx.walked(ReadArm(key, arm, results), asked[key])
        ctx.ledger.append(
            ArmRecord(
                round=round_num, arm=key, state=arm, anchors=session.state.view_context.anchors()
            )
        )

    cut = await run_walks(
        walks,
        cycle.session,
        keep_cut=keep_cut,
        backfills=pool,
        blocks=blocks,
        on_turn=on_turn,
        on_decided=on_decided,
    )
    # A prior's caught-up cells are its round reading's rows, so the race and the election read one sheet.
    for key, cells in ({} if pool is None else pool.caught).items():
        if key in concluded:
            taken = CellSheet(rows[key].scorer_id, tuple(cells))
            rows[key] = rows[key].merged(taken)
            ctx.walked(ReadArm(key, concluded[key], taken), asked[key])
    ranked = [key for key in proposed if key in concluded]
    return {key: concluded[key] for key in ranked}, {key: rows[key] for key in ranked}, cut, race


def _open_arm(
    ctx: RoundContext,
    arm: Proposed,
    sp: JobSearchPoint,
    dataset: list[Sample],
    skip_at: int | None,
) -> Walk | None:
    if arm.refused:
        return None
    return open_walk(
        sp,
        dataset,
        ctx.cycle.session,
        kind=MeasurementRole.PANEL,
        sample_index=ctx.cycle.sample_index,
        # Handed to the gateway rather than bound here, so no re-entrant asker inherits it.
        measured=arm.individual.id,
        skip_at=skip_at,
        held=ctx.held(ArmKey(arm.individual.id)),
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
        return refuse(arm), NO_CELLS

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


def run_init(cycle: Cycle, ledger: CycleEventLog) -> None:
    """Once per cycle, before round 0's adapters: a cycle whose origin closed already holds what it wrote."""
    if cycle.origin_round.optimizer_state is not None:
        return
    ctx = RoundContext(cycle=cycle, round_num=0, ledger=ledger)
    for initializer in cycle.optimizer.plan.initializers:
        initializer.initialize(NodeContext(ctx, initializer))


async def settle_owed_outputs(ctx: RoundContext) -> None:
    """Before a walk: an adapter that landed nothing on the round before is re-run onto it, and the walk HALTS (``ADAPTER_OWED``, resumable) where it never lands."""
    cycle = ctx.cycle
    prior = cycle.rounds[-1]
    # A replayed walk makes no call, so nothing it would read is re-sent.
    if prior.round == 0 or not prior.results or cycle.banked_walk(ctx.round_num) is not None:
        return
    at_prior = RoundContext(cycle=cycle, round_num=prior.round, ledger=ctx.ledger)
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


def _crowning_read(cycle: Cycle, measured: MeasuredRound, winner: ArmKey | None) -> BarRead | None:
    """The ONE bar's read of the pick, every electable arm its round's multiplicity. Today's reading
    is the selector's own admission, so under it the pick clears as its selector picked it."""
    if winner is None:
        return None
    parent = measured.pooled(measured.parent.key)
    subjects = {
        key: BarSubject(
            *discordant(measured.pooled(key), parent),
            None if (fit := measured.fit.get(key)) is None else fit.lift,
            key == winner,
        )
        for key in {*measured.electable, winner}
    }
    return cycle.crowning_bar(cycle.optimizer.admission).read(subjects)[winner]


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
    reference = line_reference(cycle.rounds, origin=ArmKey(cycle.origin_round.origin.individual.id))
    assert reference.arm == measured.parent.key, "a round re-reads the candidate its line stands on"
    return ended_on(
        Election(
            selected=winner,
            leading=leading,
            # A held round headlines the parent RE-SCORED on this panel, never a union of its cells.
            ended_on=winner or measured.parent.key,
            electable=len(measured.electable),
            fit=measured.fit,
            elects_on=elects_on,
            verdict_reason=selection.verdict_reason,
            ruler=cycle.difficulty.stamp,
            bar=_crowning_read(cycle, measured, winner),
        ),
        priors=cycle.rounds,
        schema=cycle.session.pipeline_schema,
        overlap=ReadingState.PENDING,
        round=ctx.round_num,
        arms={key: read.state for key, read in measured.filed.items()},
        reference=reference,
        folds=measured.folds,
        cells={key: read.rows for key, read in measured.filed.items()},
        overlap_results=measured.on_panel,
    )


def _unbought_cells(arm: ArmState, rows: CellSheet, cut: StopReason | None) -> int:
    # Under a cut, a MEASURED arm short of its panel was stopped by no rule and no operator.
    if cut is None or not isinstance(arm, Measured) or arm.locked_in is not None:
        return 0
    return max(0, arm.expected_samples - len(rows))


def _panel_gate(ctx: RoundContext, round_result: RoundResult, cut: StopReason | None) -> None:
    round_num = ctx.round_num
    panel = round_result.panel
    unbought = {
        key: _unbought_cells(arm, round_result.rows(key), cut) for key, arm in panel.items()
    }
    holes = {key: unscoreable_cells(round_result.rows(key)) + unbought[key] for key in panel}
    # A broken arm also carries error rows, but its own outcome already charges it.
    holed = sorted(key for key, arm in panel.items() if is_leader_eligible(arm) and holes[key])
    # Sink is the LEDGER: the halt below unwinds before `close_round`.
    record_decision(
        ctx.ledger, PanelCoverage(holes_by_arm=holes), holed, node=None, round=round_num
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
        " ".join(stop_next_lines(reason)),
    )
    raise StopLoop(reason)


def announce_opening(ctx: RoundContext, walk: RoundWalk) -> RoundOpening:
    cycle = ctx.cycle
    bench = RoundOpening(
        standing=f"no advance {rounds_without_advance(cycle.rounds)}",
        note=f"seeds the population ({INITIAL_POPULATION})" if walk.seeding else "",
        arms=None if walk.pipeline == INITIAL_POPULATION else cycle.optimizer.arms_per_round,
        proposer=walk.proposer,
        replayed=cycle.banked_walk(ctx.round_num) is not None,
    )
    opening = walk.lead.opening(NodeContext(ctx, walk.lead), bench)
    fold = cycle.parent_fold
    node = walk.proposer
    ctx.ledger.append(
        PhaseRecord(
            phase=CampaignPhase.PROPOSE,
            event="enter",
            round=ctx.round_num,
            view=propose_enter(
                cycle.session.state.view_context,
                round=ctx.round_num,
                node=node,
                opening=opening,
                budget_rounds=cycle.config.optimization.budget.rounds,
                parent_fitness=None if fold is None else fold.fitness,
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
    )
    return opening


def announce_population(ctx: RoundContext, opening: RoundOpening, population: Proposals) -> None:
    anchors = ctx.cycle.session.state.view_context.anchors()
    ctx.ledger.append(
        *(
            ArmRecord(round=ctx.round_num, arm=key, state=arm, anchors=anchors)
            for key, arm in population.arms(ctx.round_num).items()
        ),
        PhaseRecord(
            phase=CampaignPhase.PROPOSE,
            event="exit",
            round=ctx.round_num,
            view=propose_exit(
                ctx.cycle.session.state.view_context,
                round=ctx.round_num,
                opening=opening,
                proposals=population.proposals,
                collapses=collapse_counts(cp.validation_failures for cp in population.proposals),
            ),
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
    """Once per round, with the state the walk left and the decisions it made."""
    # An EMPTY bank replays as zero arms on every resume: a permanent failure to propose.
    if not proposals:
        return
    flush_pending_decisions(ctx.cycle, ctx.ledger)
    bank_proposals(ctx, proposals, submitted=False)


async def _walked(ctx: RoundContext, walk: RoundWalk, panel: Panel) -> Proposals:
    cycle = ctx.cycle
    if (banked := cycle.banked_walk(ctx.round_num)) is not None:
        cycle.seat_walk(ctx.round_num, banked.walked)
        await reread_walk(ctx, banked.reads)
        return Proposals(list(banked.proposals))
    seeds, proposers, _ = walk.walked
    if seeds:
        seeded = await _walk(ctx, seeds, panel)
        cycle.population = [p.individual for p in seeded.proposals if not p.refused]
    population = await _walk(ctx, proposers, panel)
    bank_walk(ctx, population.proposals)
    return population


async def propose_population(ctx: RoundContext, walk: RoundWalk, panel: Panel) -> Proposals:
    population = await _walked(ctx, walk, panel)
    arms = _one_arm_each(ctx, population.proposals)
    seated = walk.walked[2]
    if seated:
        arms = _one_arm_each(ctx, await operator_arms(ctx, arms))
    return replace(population, proposals=arms)


async def _adapt(ctx: RoundContext, plan: RoundPlan) -> None:
    if not plan.adapters:
        return
    ctx.ledger.append(PhaseRecord(phase=CampaignPhase.ADAPT, event="enter", round=ctx.round_num))
    for adapter in plan.adapters:
        # Survived here: the next walk re-runs it first (`settle_owed_outputs`).
        with graceful(f"round {ctx.round_num} {adapter.name} landed nothing"):
            await adapter.adapt(NodeContext(ctx, adapter))
    ctx.ledger.append(PhaseRecord(phase=CampaignPhase.ADAPT, event="exit", round=ctx.round_num))


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
    plan = ctx.cycle.optimizer.plan
    ctx.ledger.append(
        PhaseRecord(
            phase=CampaignPhase.MEASURE,
            event="enter",
            round=ctx.round_num,
            view=MeasureEnterView(
                n_candidates=len(population.proposals),
                n_samples=len(panel.cells),
            ),
        )
    )
    measured = await measure_population(
        ctx, population, panel, pool, plan.eliminator, plan.selector
    )
    ctx.ledger.append(PhaseRecord(phase=CampaignPhase.MEASURE, event="exit", round=ctx.round_num))
    return measured


def _survivors(ctx: RoundContext, selection: Selection) -> list[OptSearchPoint]:
    survivor = ctx.cycle.optimizer.plan.survivor
    if survivor is None:
        return list(selection.ranked)
    return list(survivor.survive(NodeContext(ctx, survivor), selection.ranked))


def _crowned(ctx: RoundContext, measured: MeasuredRound, selection: Selection) -> RoundResult:
    """The bench's close of the election: the round's result, gated on a whole panel, then absorbed."""
    cycle = ctx.cycle
    result = _round_result(
        ctx, measured, selection, elects_on=cycle.optimizer.plan.selector.elects_on
    )
    ctx.ledger.append(
        PhaseRecord(
            phase=CampaignPhase.SELECT,
            event="exit",
            round=ctx.round_num,
            view=select_exit(cycle.session.state.view_context, result),
        )
    )
    _panel_gate(ctx, result, measured.cut)
    # AFTER the panel gate: a round halted on a holed panel is unwound and re-run, never crowned.
    assert result.election is not None
    ctx.ledger.append(ElectionRecord(round=ctx.round_num, **dict(result.election)))
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
    reading, bought = await measure_overlap(cycle, round_result, pool, measured.origin_panel_slots)
    seated = cycle.seat(
        round_result.model_copy(update={"overlap": reading, "overlap_results": bought})
    )
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


async def walk_round(
    cycle: Cycle, round_num: int, ledger: CycleEventLog
) -> tuple[RoundContext, list[Sample], Panel, Proposals]:
    """A round's open through its banked walk: a measured round's prefix."""
    # The search split alone: a bench or demo row a round could draw would elect on held-out rows.
    pool = list(cycle.session.state.scoring.partition.search)
    ctx = RoundContext(cycle=cycle, round_num=round_num, ledger=ledger)
    await settle_owed_outputs(ctx)
    # Before the draw, which reads the scale the election will, and before an arm spawns a cell.
    banked = cycle.banked_walk(round_num)
    reach = ruler_clock(cycle.session) if banked is None else banked.opened_on
    cycle.difficulty.open_round(cycle.rounds, reach)
    pin_inner_ruler(cycle.difficulty.stamp.reach)
    sampler = cycle.optimizer.plan.sampler
    panel = sampler.draw(NodeContext(ctx, sampler), pool)
    return ctx, pool, panel, await _proposed(ctx, panel)


async def execute_round(
    cycle: Cycle,
    round_num: int,
    ledger: CycleEventLog,
    *,
    is_final_round: bool = False,
) -> RoundWalked:
    """One round, each role handed what the ones before it left."""
    ctx, pool, panel, population = await walk_round(cycle, round_num, ledger)
    plan = cycle.optimizer.plan
    measured = await _measured(ctx, population, panel, pool)
    ctx.ledger.append(PhaseRecord(phase=CampaignPhase.SELECT, event="enter", round=round_num))
    selection = plan.selector.select(NodeContext(ctx, plan.selector), measured)
    cycle.population = _survivors(ctx, selection)
    result = _crowned(ctx, measured, selection)
    result = await _adapt_beside_overlap(
        ctx, result, measured, population, pool, is_final_round=is_final_round
    )
    act = None
    if (controller := plan.controller) is not None:
        seat = RoundContext(cycle=cycle, round_num=round_num, ledger=ledger)
        if (rule := controller.bank_round(NodeContext(seat, controller), result)) is not None:
            act = ControllerAct(controller, rule)
    return RoundWalked(result, measured.cut, act)


async def emit_origin_round(cycle: Cycle, session: Session, ledger: CycleEventLog) -> None:
    if cycle.origin_round.results:
        await _adapt(RoundContext(cycle=cycle, round_num=0, ledger=ledger), cycle.optimizer.plan)
    round_result = cycle.origin_round

    # The origin's cells fold after the election that adopted it, and before the close.
    assert round_result.election is not None, "round 0 elects the origin it adopted"
    ledger.append(ElectionRecord(round=0, **dict(round_result.election)))
    cycle.difficulty.fold(round_result)
    # Unspent here, a look-ahead press during the origin's scoring widens round 1 too.
    session.state.control.spend_sample_lookahead()
    await close_round(cycle, round_result, 0, session, ledger)


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
    ledger: CycleEventLog,
) -> RoundResult:
    stood = round_result.model_copy(update={"optimizer_state": cycle.close_state(round_num)})
    facts = cycle.optimizer.round_facts(stood)
    closing = cycle.seat(stood.model_copy(update={"optimizer_facts": facts}))
    flush_pending_decisions(cycle, ledger)
    line, series, cycle.standing = stated_line(
        closing,
        cycle.rounds,
        cycle.difficulty,
        lives=cycle.config.optimization.lives,
        outlay=line_outlay(session, cycle.optimizer.phases),
    )
    offset = ledger.append(
        RoundClosedRecord.of(
            line,
            session.bank.slot_of,
            series,
            cycle.standing,
            session.state.view_context.anchors(),
        )
    )
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
    round_num: int,
    session: Session,
    ledger: CycleEventLog,
    *,
    is_final_round: bool = False,
) -> None:
    round_result = await close_round(cycle, walked.result, round_num, session, ledger)

    # Before the stop below, so a perfect round that also ends the campaign still gets its check.
    fold, election = round_result.fold, round_result.election
    await verify_on_saturation(
        stores=session.store,
        hop=session.hop,
        round_num=round_num,
        fitness=None if fold is None else fold.fitness,
        winner_id=None if election is None else election.selected,
        seed=cycle.rng(round_num, "verify").getrandbits(31),
        ledger=ledger,
    )
    reserve_selection_pass(cycle, session)

    assert cycle.standing is not None, "a boundary stop reads the close it follows"
    if (stop := standing_tripped(cycle.standing, cycle.config.optimization)) is not None:
        raise StopLoop(stop)
    if (act := walked.act) is not None and not is_final_round:
        seat = RoundContext(cycle=cycle, round_num=round_num, ledger=ledger)
        await act.controller.act(NodeContext(seat, act.controller), act.rule)
