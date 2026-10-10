from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from statistics import fmean
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.runner.termination import RUN_STOPS, run_stop_reason
from promptpotter.application.scoring.cells import walked_rows
from promptpotter.application.scoring.formula import cell_channels_of
from promptpotter.application.scoring.paired import (
    MemberRows,
    absent_pair,
    grade_measurands,
    read_pair,
)
from promptpotter.application.scoring.query_loop import ArmSlot
from promptpotter.application.scoring.search_point_scorer import (
    SCORING_ERROR_ABORT,
    score_search_point,
)
from promptpotter.application.scoring.selection import level_band
from promptpotter.domain.bench import (
    BENCH_HEADLINE,
    COLUMN_GRADE,
    BandedValue,
    BenchColumn,
    BenchPass,
    BenchPasses,
    BenchReading,
    BenchScore,
    BenchSubject,
    BenchTrigger,
    LiftCost,
    LineRun,
    OwnLevel,
    PassOutcome,
    PassStop,
    bench_status,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    CellSet,
    CellSetBasis,
    CellSetName,
    MemberAddress,
    PairedReading,
    ReadingState,
)
from promptpotter.domain.phase_views import BenchEnterView, BenchGradedView, BenchScoredView
from promptpotter.domain.phases import STOP_REASON_INFO, CampaignPhase, StopOutcome
from promptpotter.domain.results import (
    IndividualWalk,
    declared_selection,
    individual_cells,
    measured_searchpoint,
)
from promptpotter.domain.scoring import ROW_GRADES, WalkedCell
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.store.archive_queries import bench_reads
from promptpotter.shared.measurement_context import NO_ROUND_SLOT, MeasurementRole, RoleScope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.results import RoundOutcome, RoundResult
    from promptpotter.domain.scoring import CellSheet, Scorer
    from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "BenchLine",
    "PassReading",
    "bench_member",
    "bench_members",
    "bench_selection",
    "bench_walk",
    "declare_bench",
    "grade_round_selection",
    "graded",
    "own_level",
    "pair_on_bench",
    "read_bench",
    "read_pass",
    "reserve_selection_pass",
    "score_on_bench",
]


class PassReading(NamedTuple):
    # `None` where the pass read nothing; `outcome.state` then says why.
    reading: BenchReading | None
    outcome: PassOutcome
    sheet: CellSheet


@dataclass(frozen=True, kw_only=True)
class BenchLine:
    hop: CycleHop
    on_line: bool
    held_by: str | None
    trigger: BenchTrigger
    held_out: int
    # The pair refuses two passes whose instruments differ.
    instrument_id: str
    run: LineRun
    spend: SpendRollup | None


class _Graded(NamedTuple):
    origin: PassReading
    selected_pass: BenchPass | None
    selected: PassReading | None
    vs_origin: PairedReading | None


def bench_walk(bench_pass: BenchPass) -> IndividualWalk[WalkedCell]:
    return IndividualWalk(
        bench_pass.candidate_id, frozenset({MeasurementRole.BENCH}), bench_pass.cells
    )


def read_pass(
    stores: Stores, bench_pass: BenchPass, scorer: Scorer, *, tolerance: int
) -> PassReading:
    """A pass stopped short, or past *tolerance* unscored rows, reads NOTHING, never a level over fewer rows."""
    expected = len(bench_pass.sample_keys)
    held_out = individual_cells([bench_walk(bench_pass)], bench_pass.candidate_id, RoleScope.BENCH)
    sheet = walked_rows(stores, held_out, scorer).standing()
    scored = len(sheet.scoreable)

    def unread(state: ReadingState) -> PassReading:
        outcome = PassOutcome(state, bench_pass.stop, scored, expected, bench_pass.reads_before)
        return PassReading(None, outcome, sheet)

    if bench_pass.stop is not None:
        return unread(ReadingState.PASS_STOPPED)
    if expected - scored > tolerance:
        return unread(ReadingState.PAST_TOLERANCE)
    reading = BenchReading.read(
        own_level(sheet),
        round=bench_pass.round,
        sp_hash=bench_pass.sp_hash,
        headline=BENCH_HEADLINE,
    )
    outcome = PassOutcome(ReadingState.READ, None, scored, expected, bench_pass.reads_before)
    return PassReading(reading, outcome, sheet)


def bench_member(
    hop: CycleHop, bench_pass: BenchPass, read: PassReading, *, instrument_id: str
) -> MemberRows:
    return MemberRows(
        address=MemberAddress(
            path=(hop,),
            individual_id=bench_pass.candidate_id,
            arm=None,
            pass_role=MeasurementRole.BENCH,
        ),
        sheet=read.sheet,
        bought=sum(1 for *_, replayed in bench_pass.cells if not replayed),
        cut=False,
        scope=RoleScope.BENCH,
        instrument_id=instrument_id,
        dataset_hash=None,
        cell_set_id=_bench_split(bench_pass.sample_keys).id,
    )


def _bench_split(sample_keys: Sequence[str]) -> CellSet:
    return CellSet.of(
        CellSetName.BENCH_SPLIT, CellSetBasis.DECLARED, sample_keys, dataset_hash=None
    )


def pair_on_bench(
    a: MemberRows, b: MemberRows, *, sample_keys: Sequence[str], instrument_id: str
) -> PairedReading:
    """*b* over *a*, under the grades *a* was read under: a member graded under another formula is refused."""
    return read_pair(
        a=a,
        b=b,
        cell_set=CellSetName.BENCH_SPLIT,
        cells=sample_keys,
        masked=False,
        dataset_hash=None,
        measurands=grade_measurands(a.sheet.scorer_id or b.sheet.scorer_id),
        spec=ROUND_LIFT_SPEC,
        scope=RoleScope.BENCH,
        instrument_id=instrument_id,
    )


def own_level(sheet: CellSheet) -> OwnLevel:
    def column(name: BenchColumn) -> BandedValue | None:
        level, ci_lo, ci_hi = level_band(sheet, ROW_GRADES[COLUMN_GRADE[name]])
        if level is None:
            return None
        return BandedValue(value=level, ci_lo=ci_lo, ci_hi=ci_hi)

    return OwnLevel(
        accuracy=column("accuracy"), composite=column("composite"), n=len(sheet.scoreable)
    )


def read_bench(
    stores: Stores,
    passes: BenchPasses | None,
    scorer: Scorer,
    *,
    line: BenchLine,
) -> BenchScore:
    """*passes* is ``None`` for a line that banked none and for a cycle beside the line."""
    graded = None if passes is None else _grade_bench(stores, passes, scorer, line)
    selected = None if graded is None else graded.selected
    status = bench_status(
        trigger=line.trigger,
        bench_size=line.held_out if passes is None else len(passes.origin.sample_keys),
        tolerance=0 if passes is None else passes.tolerance,
        on_line=line.on_line,
        held_by=line.held_by,
        origin=None if graded is None else graded.origin.outcome,
        selected=None if selected is None else selected.outcome,
        run=line.run,
    )
    vs_origin = None if graded is None else graded.vs_origin
    if vs_origin is None:
        vs_origin = absent_pair(
            state=status.state,
            a=None,
            b=None,
            cell_set=None if passes is None else _bench_split(passes.origin.sample_keys),
            scope=RoleScope.BENCH,
            spec=ROUND_LIFT_SPEC,
            instrument_id=line.instrument_id,
        )
    return BenchScore.of(
        bench_size=line.held_out if passes is None else len(passes.origin.sample_keys),
        scorer_id=scorer.id,
        headline=BENCH_HEADLINE,
        status=status,
        origin=None if graded is None else graded.origin.reading,
        selected=None if selected is None else selected.reading,
        vs_origin=vs_origin,
        cost=LiftCost.of(vs_origin, line.spend),
    )


def bench_members(
    stores: Stores,
    passes: BenchPasses,
    scorer: Scorer,
    *,
    line: BenchLine,
) -> tuple[MemberRows | None, MemberRows | None]:
    graded = _grade_bench(stores, passes, scorer, line)

    def member(bench_pass: BenchPass | None, read: PassReading | None) -> MemberRows | None:
        if bench_pass is None or read is None or read.reading is None:
            return None
        return bench_member(line.hop, bench_pass, read, instrument_id=line.instrument_id)

    return member(passes.origin, graded.origin), member(graded.selected_pass, graded.selected)


def _standing_pass(passes: BenchPasses, run: LineRun) -> BenchPass | None:
    if run.selection is None or not run.rounds_closed:
        return None
    return passes.pass_of(run.selection.candidate_id)


def _grade_bench(stores: Stores, passes: BenchPasses, scorer: Scorer, line: BenchLine) -> _Graded:
    read = partial(read_pass, stores, scorer=scorer, tolerance=passes.tolerance)
    origin = read(passes.origin)
    selected_pass = _standing_pass(passes, line.run)
    if selected_pass is None:
        return _Graded(origin, None, None, None)
    # One pass read twice is one member: the pair says `same_individual`, never a 0.0 lift.
    selected = origin if selected_pass == passes.origin else read(selected_pass)
    if origin.reading is None or selected.reading is None:
        return _Graded(origin, selected_pass, selected, None)
    instrument_id = line.instrument_id
    vs_origin = pair_on_bench(
        bench_member(line.hop, passes.origin, origin, instrument_id=instrument_id),
        bench_member(line.hop, selected_pass, selected, instrument_id=instrument_id),
        sample_keys=passes.origin.sample_keys,
        instrument_id=instrument_id,
    )
    return _Graded(origin, selected_pass, selected, vs_origin)


def _reads_before(session: Session) -> int:
    return bench_reads(
        session.store,
        dataset_name=session.measured_dataset,
        sample_ids=frozenset(s.id for s in session.scoring.require_partition().bench),
    )


async def score_on_bench(
    session: Session,
    search_point: JobSearchPoint,
    *,
    individual_id: str,
    subject: BenchSubject,
    label: str,
    round_num: int,
    cb: RunCallbacks,
) -> BenchPass:
    sp_hash = search_point.sp_hash(session.pipeline_schema)
    bench = list(session.scoring.require_partition().bench)
    # Counted before the send, so the pass is never one of its own readers.
    reads_before = _reads_before(session)
    # No `round=` on the bracket: it would move the dashboard's round back to the one being graded.
    cb.on_phase(
        CampaignPhase.BENCH,
        "enter",
        view=BenchEnterView(
            subject=subject, label=label, sp_hash=sp_hash, round=round_num, rows=len(bench)
        ),
    )
    try:
        scored = await score_search_point(
            search_point,
            bench,
            session,
            label=MeasurementRole.BENCH,
            measured=None,
            slot=ArmSlot(NO_ROUND_SLOT, 0, individual_id),
        )
    finally:
        cb.on_phase(CampaignPhase.BENCH, "exit")
    stop: PassStop | None = None
    if scored.stopped is not None:
        signal = scored.signal
        stop = PassStop(
            cause=scored.stopped,
            warning=signal.check_result["dominant_warning"]
            if signal is not None and signal.check_name == SCORING_ERROR_ABORT
            else None,
        )
    return BenchPass(
        round=round_num,
        candidate_id=individual_id,
        sp_hash=sp_hash,
        cells=scored.cells,
        sample_keys=[s.key for s in bench],
        stop=stop,
        scorer_id=session.scoring.require_scorer().id,
        reads_before=reads_before,
    )


def declare_bench(cb: RunCallbacks, score: BenchScore) -> BenchScore:
    cb.on_phase(CampaignPhase.BENCH, "scored", view=BenchScoredView(bench=score))
    return score


def _tolerance(session: Session) -> int:
    split = session.scoring.require_partition().split
    return split.tolerance if split is not None else 0


def graded(
    cb: RunCallbacks,
    session: Session,
    bench_pass: BenchPass,
    *,
    subject: BenchSubject,
    label: str,
    reserve: tuple[float, int] | None = None,
) -> None:
    """The one write a line's passes are folded from: a pass no call here follows is lost with the process."""
    tolerance = _tolerance(session)
    read = read_pass(
        session.store, bench_pass, session.scoring.require_scorer(), tolerance=tolerance
    )
    cb.on_phase(
        CampaignPhase.BENCH,
        "graded",
        view=BenchGradedView(
            subject=subject,
            bench_pass=bench_pass,
            tolerance=tolerance,
            reserve_usd=None if reserve is None else reserve[0],
            reserve_tokens=None if reserve is None else reserve[1],
            reading=read.reading,
            state=read.outcome.state,
            label=label,
        ),
    )


async def grade_round_selection(
    cycle: Cycle, session: Session, round_result: RoundResult, *, cb: RunCallbacks
) -> None:
    passes = cycle.bench_passes
    if not (
        cycle.config.bench_trigger == "each_round"
        and round_result.selected_labels
        and passes is not None
    ):
        return
    label = round_result.selected_labels[0]
    assert round_result.opt_sp is not None, "a round that selected names the individual"
    taken = await score_on_bench(
        session,
        cycle.selected_sp,
        individual_id=round_result.opt_sp.id,
        subject="selected",
        label=label,
        round_num=round_result.round,
        cb=cb,
    )
    graded(cb, session, taken, subject="selected", label=label)
    cycle.bench_passes = _with_selection(passes, taken)


def _with_selection(passes: BenchPasses, taken: BenchPass) -> BenchPasses:
    return passes.model_copy(update={"selections": {**passes.selections, taken.round: taken}})


def reserve_selection_pass(cycle: Cycle, session: Session) -> None:
    """The set-aside opens at the ORIGIN's price; a selection that writes more would end its pass short."""
    book = session.control.book
    if not book.set_aside_usd:
        return
    costs = [
        cost
        for cell in cycle.selection.results
        if (cost := cell_channels_of(cell.facts, cell.grade.fitness).get("cost")) is not None
    ]
    if not costs:
        return
    # One cell on top of the mean: the last row is admitted at the dearest bill, never the average.
    usd = len(session.scoring.require_partition().bench) * fmean(costs) + max(costs)
    if usd > book.set_aside_usd:
        book.set_aside(usd, round(book.set_aside_tokens * usd / book.set_aside_usd))


async def bench_selection(
    session: Session,
    rounds: Sequence[RoundOutcome],
    *,
    framing: TaskDecomposition,
    banked: BenchPasses,
    cb: RunCallbacks,
) -> BenchPasses:
    """Only a pause escapes the pass: any other stop ends it short, banked as a stopped pass."""
    origin = banked.origin
    picked = declared_selection(rounds)
    assert picked.opt_sp is not None, "a closed round names the individual it ended on"
    if picked.opt_sp.id == origin.candidate_id:
        return banked
    selected_sp = measured_searchpoint(
        rounds,
        picked.opt_sp.id,
        schema=session.pipeline_schema,
        framing=framing,
        demo=session.scoring.require_partition().demo,
    )
    selected_hash = selected_sp.sp_hash(session.pipeline_schema)
    held = banked.pass_of(picked.opt_sp.id)
    if (
        held is not None
        and held.stop is None
        and (held.sp_hash, held.sample_keys, held.scorer_id)
        == (selected_hash, origin.sample_keys, session.scoring.require_scorer().id)
    ):
        return banked
    try:
        selected = await score_on_bench(
            session,
            selected_sp,
            individual_id=picked.opt_sp.id,
            subject="selected",
            label=picked.selected_labels[0],
            round_num=picked.round,
            cb=cb,
        )
    except RUN_STOPS as stop:
        reason = run_stop_reason(stop)
        if STOP_REASON_INFO[reason].outcome is StopOutcome.PAUSED:
            raise
        selected = BenchPass(
            round=picked.round,
            candidate_id=picked.opt_sp.id,
            sp_hash=selected_hash,
            cells=[],
            sample_keys=origin.sample_keys,
            stop=PassStop(cause=reason, warning=None),
            scorer_id=session.scoring.require_scorer().id,
            reads_before=_reads_before(session),
        )
    graded(cb, session, selected, subject="selected", label=picked.selected_labels[0])
    return _with_selection(banked, selected)
