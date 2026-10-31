"""Why the set is fixed at C0 rather than re-chosen: ``domain/origin_panel.py::origin_panel``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.scoring.paired import (
    MemberRows,
    absent_pair,
    fresh_cells,
    grade_measurands,
    read_pair,
)
from promptpotter.application.scoring.search_point_scorer import reread_cells, score_search_points
from promptpotter.domain.arms import Measured, arm_name, walked_short
from promptpotter.domain.cell_fold import measured_cells
from promptpotter.domain.origin_panel import (
    OverlapReading,
    PanelTrigger,
    TriggeredReading,
    origin_panel,
)
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    CellSet,
    CellSetBasis,
    CellSetName,
    MemberAddress,
    PairedReading,
    PairMember,
    ReadingState,
)
from promptpotter.domain.results import InRunCells
from promptpotter.domain.run_standing import LineStep, best_line
from promptpotter.shared.measurement_context import MeasuredPass, MeasurementRole, RoleScope

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.scoring.search_point_scorer import ScoredWalk
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet, GradedCell

logger = logging.getLogger(__name__)


def round_origin_panel(cycle: Cycle, scoring_pool: list[Sample]) -> list[int]:
    """Fixed before the round's election, so its own pick joins the line's cells from the next."""
    pairs = _OriginPanelPairs(cycle, scoring_pool)
    ordered = sorted(best_line(cycle.rounds), key=lambda s: s.arm.round)
    held = InRunCells(cycle.rounds)
    # The origin's OWN round, never every cell read since: a later re-score would move the exam.
    origin_round = next(rr for rr in cycle.rounds if rr.round == ordered[0].arm.round)
    scopes = cycle.difficulty.scopes
    return origin_panel(
        pairs.measured(origin_round.results),
        line_cells=set[int]().union(
            *(pairs.measured(pairs.cells_of(s, held)) for s in ordered[1:])
        ),
        poolable={s.id for s in scoring_pool},
        scope_of={s.id: scopes.by_key[s.key] for s in scoring_pool},
        size=cycle.optimizer.round_cells(len(scoring_pool)),
    )


async def read_origin_panel(
    cycle: Cycle,
    members: Sequence[tuple[LineStep, CellSheet]],
    panel: Sequence[int],
    scoring_pool: list[Sample],
    *,
    buys: bool,
) -> dict[str, CellSheet]:
    """Each member on the panel cells its draw left, for the election to pool: a held cell
    replays free, and where *buys* is false the archive alone answers."""
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
    if buys:
        return {
            member: scored.sheet for member, scored in (await _measure_gaps(cycle, gaps)).items()
        }
    return {
        member: reread_cells(
            cycle.points.job(step.opt_sp), samples, cycle.session, label=MeasurementRole.OVERLAP
        )
        for member, (step, samples) in gaps.items()
    }


async def measure_overlap(
    cycle: Cycle, round_result: RoundResult, scoring_pool: list[Sample], panel: Sequence[int]
) -> tuple[OverlapReading, dict[str, CellSheet]]:
    """After the election, on the panel it pooled; a member the election's pass read is never
    topped up again, so a replay pools exactly what it did."""
    passed = dict(round_result.overlap_results)
    if round_result.head is None:
        return OverlapReading.unpaired(
            ReadingState.NO_SELECTION, round_result.round, round_result.improved
        ), passed
    pairs = _OriginPanelPairs(cycle, scoring_pool)
    # The line INCLUDING this round, which may have made its own result the new best.
    ordered = sorted(best_line(cycle.rounds), key=lambda s: s.arm.round)
    held = InRunCells(cycle.rounds)
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
    )
    return reading, bought


def panel_triggers(
    rounds: Sequence[RoundResult], n_min: int
) -> list[tuple[LineStep, PanelTrigger]]:
    """Every arm of the cycle walked to its draw's end a trigger fired for, by first walk: a stop
    buys no cell after it, and a parent re-read is no walk."""
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
        self._scorer = session.state.scoring.require_scorer().id
        self._dataset = session.bank.dataset_hash
        self._key_of = {int(s.id): s.key for s in scoring_pool}
        self._slot_of = session.bank.slot_of

    @staticmethod
    def cells_of(step: LineStep, held: InRunCells) -> CellSheet:
        return held.sheet(step.opt_sp.id, RoleScope.REPORT)

    def measured(self, rows: Iterable[GradedCell]) -> set[int]:
        """By bank slot: the panel is served as the sample ids the operator selects on."""
        return {self._slot_of[key] for key in measured_cells(rows)}

    def read(
        self,
        a: LineStep,
        b: LineStep,
        held: InRunCells,
        panel: set[int],
        paid: dict[str, int],
    ) -> PairedReading:
        return read_pair(
            a=self._member(a, held, paid),
            b=self._member(b, held, paid),
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

    def _member(self, step: LineStep, held: InRunCells, paid: dict[str, int]) -> MemberRows:
        return MemberRows(
            address=self._address(step),
            sheet=self.cells_of(step, held),
            bought=paid.get(step.opt_sp.id, 0),
            # The top-up is what completes a member an eliminator stopped short of the panel.
            cut=False,
            scope=RoleScope.REPORT,
            instrument_id=self._instrument,
            dataset_hash=self._dataset,
            cell_set_id=None,
        )


async def _measure_gaps(
    cycle: Cycle, gaps: Mapping[str, tuple[LineStep, list[Sample]]]
) -> dict[str, ScoredWalk]:
    """Every member's gaps in ONE pass, each on its OWN configuration, never the round subject's;
    no sample history, so the pass re-sends no degraded cached cell."""
    if not gaps:
        return {}
    for step, samples in gaps.values():
        logger.info(
            "overlap: measuring %s on %d cell(s) of the origin panel", step.arm.label, len(samples)
        )
    scored = await score_search_points(
        {
            member: (
                cycle.points.job(step.opt_sp),
                samples,
                MeasuredPass(individual_id=step.opt_sp.id, role=MeasurementRole.OVERLAP),
            )
            for member, (step, samples) in gaps.items()
        },
        cycle.session,
        # One run per (member, cell set), so a re-run of the same round replays it free.
        label=MeasurementRole.OVERLAP,
    )
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
