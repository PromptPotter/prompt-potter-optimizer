"""Why the set is fixed at C0 rather than re-chosen: ``domain/results.py::origin_panel``."""

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
from promptpotter.application.scoring.search_point_scorer import score_search_point
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
from promptpotter.domain.results import (
    InRunCells,
    LineStep,
    OverlapReading,
    best_line,
    measured_cells,
    origin_panel,
)
from promptpotter.shared.measurement_context import MeasuredPass, MeasurementRole, RoleScope

if TYPE_CHECKING:
    from collections.abc import Iterable

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet, GradedCell

logger = logging.getLogger(__name__)


async def measure_overlap(
    cycle: Cycle, round_result: RoundResult, scoring_pool: list[Sample]
) -> tuple[OverlapReading, dict[str, CellSheet]]:
    """Called after the election, ruler extension, panel gate and fold: that ordering IS the quarantine."""
    if round_result.head is None:
        return OverlapReading.unpaired(
            ReadingState.NO_SELECTION, round_result.round, round_result.improved
        ), {}
    pairs = _OriginPanelPairs(cycle, scoring_pool)
    # The line INCLUDING this round, which may have made its own result the new best.
    ordered = sorted(best_line(cycle.rounds), key=lambda s: s.arm.round)
    held = InRunCells(cycle.rounds)
    origin = ordered[0]
    # The origin's OWN round, never every cell read since: a later re-score would move the exam.
    origin_round = next(rr for rr in cycle.rounds if rr.round == origin.arm.round)
    scopes = cycle.difficulty.scopes
    panel = origin_panel(
        pairs.measured(origin_round.results),
        line_cells=set[int]().union(
            *(pairs.measured(pairs.cells_of(s, held)) for s in ordered[1:])
        ),
        poolable={s.id for s in scoring_pool},
        scope_of={s.id: scopes.by_key[s.key] for s in scoring_pool},
        size=cycle.optimizer.round_cells(len(scoring_pool)),
    )
    keep = set(panel)

    def not_read(state: ReadingState, bought: dict[str, int]) -> OverlapReading:
        return OverlapReading.of(
            round_result.round,
            round_result.improved,
            positions=panel,
            lead=pairs.absent(state, origin, ordered[-1], held, keep, bought),
            earlier=(),
        )

    if len(ordered) < 2:
        return not_read(ReadingState.SAME_INDIVIDUAL, {}), {}
    if not panel:
        logger.info(
            "overlap: round %d — the origin holds no cell this cycle can still buy, so no "
            "1-to-1 reading is possible",
            round_result.round,
        )
        return not_read(ReadingState.NOT_HELD, {}), {}

    bought: dict[str, CellSheet] = {}
    for step in ordered:
        gaps = sorted(keep - pairs.measured(pairs.cells_of(step, held)))
        if not gaps:
            continue
        fresh = await _measure_gaps(cycle, gaps, scoring_pool, step=step)
        if fresh is None:
            # A member short of the set breaks the one claim the reading makes.
            return not_read(
                ReadingState.PASS_STOPPED,
                {cid: fresh_cells(rows) for cid, rows in bought.items()},
            ), {}
        bought[step.opt_sp.id] = fresh

    # Read again off the round as it will CLOSE, so what was bought joins by the one read.
    closing = round_result.model_copy(update={"overlap_results": bought})
    closed = [closing if rr.round == round_result.round else rr for rr in cycle.rounds]
    c0, *picks = sorted(best_line(closed), key=lambda s: s.arm.round)
    topped_up = InRunCells(closed)
    paid = {cid: fresh_cells(rows) for cid, rows in bought.items()}
    readings = [pairs.read(c0, pick, topped_up, keep, paid) for pick in picks]
    reading = OverlapReading.of(
        round_result.round,
        round_result.improved,
        positions=panel,
        lead=readings[-1],
        earlier=readings[:-1],
    )
    return reading, bought


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
        c0: LineStep,
        pick: LineStep,
        held: InRunCells,
        panel: set[int],
        paid: dict[str, int],
    ) -> PairedReading:
        return read_pair(
            a=self._member(c0, held, paid),
            b=self._member(pick, held, paid),
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
    cycle: Cycle,
    gaps: list[int],
    scoring_pool: list[Sample],
    *,
    step: LineStep,
) -> CellSheet | None:
    """*step*'s OWN configuration, never the round subject's. ``None`` when the pass stopped short."""

    want = set(gaps)
    samples = [s for s in scoring_pool if s.id in want]
    logger.info(
        "overlap: measuring %s on %d cell(s) of the origin panel it had not sat",
        step.arm.label,
        len(samples),
    )
    scored = await score_search_point(
        cycle.points.job(step.opt_sp),
        samples,
        cycle.session,
        # One run per (member, gap set), so a re-run of the same round replays it free.
        label=MeasurementRole.OVERLAP,
        # No sample history: a report-only pass re-sends no degraded cached cell.
        sample_index=None,
        measured=MeasuredPass(individual_id=step.opt_sp.id, role=MeasurementRole.OVERLAP),
    )
    if scored.stopped is not None:
        logger.warning(
            "overlap: %s stopped after %d/%d cell(s) (%s); no reading this round",
            step.arm.label,
            len(scored.sheet),
            len(samples),
            scored.stopped,
        )
        return None
    return scored.sheet
