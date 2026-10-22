from __future__ import annotations

from dataclasses import dataclass, replace
from functools import partial
from statistics import fmean
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.runner.termination import RUN_STOPS, run_stop_reason
from promptpotter.application.scoring.closed_rounds import walked_rows
from promptpotter.application.scoring.formula.compiler import cell_channels_of
from promptpotter.application.scoring.paired import (
    MemberRows,
    absent_pair,
    grade_measurands,
    read_pair,
)
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
    BenchScope,
    BenchScore,
    BenchSubject,
    BenchTrigger,
    LiftCost,
    LineRun,
    OwnLevel,
    PassOutcome,
    PassStop,
    bench_split,
    bench_status,
)
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    ArmKey,
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
)
from promptpotter.domain.scoring import ROW_GRADES, WalkedCell
from promptpotter.domain.spend import Budget, SpendRollup
from promptpotter.infrastructure.store.archive_queries import bench_reads
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.domain.results import PointContext, RoundOutcome, RoundResult
    from promptpotter.domain.scoring import CellSheet, Scorer
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store.stores import Stores


class _PassReading(NamedTuple):
    bench_pass: BenchPass
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
    # The pair refuses two passes whose instruments differ; a cycle that drew no bank names none.
    instrument_id: str | None
    run: LineRun
    spend: SpendRollup | None


class _Graded(NamedTuple):
    origin: _PassReading
    selected: _PassReading | None
    members: tuple[MemberRows | None, MemberRows | None]
    vs_origin: PairedReading | None


def _bench_walk(bench_pass: BenchPass) -> IndividualWalk[WalkedCell]:
    return IndividualWalk(
        bench_pass.candidate_id, frozenset({MeasurementRole.BENCH}), bench_pass.cells
    )


def _read_pass(
    stores: Stores, bench_pass: BenchPass, scorer: Scorer, *, tolerance: int
) -> _PassReading:
    """A pass stopped short, or past *tolerance* unscored rows, reads NOTHING, never a level over fewer rows."""
    expected = len(bench_pass.sample_keys)
    held_out = individual_cells([_bench_walk(bench_pass)], bench_pass.candidate_id, RoleScope.BENCH)
    sheet = walked_rows(stores, held_out, scorer).standing()
    scored = len(sheet.scoreable)

    def unread(state: ReadingState) -> _PassReading:
        outcome = PassOutcome(state, bench_pass.stop, scored, expected, bench_pass.reads_before)
        return _PassReading(bench_pass, None, outcome, sheet)

    if bench_pass.stop is not None:
        return unread(ReadingState.PASS_STOPPED)
    if expected - scored > tolerance:
        return unread(ReadingState.PAST_TOLERANCE)
    # Rows on several datasets read no level: `BenchScore.scopes` reads each apart.
    own = (
        own_level(sheet)
        if len(bench_pass.by_dataset()) < 2
        else OwnLevel(accuracy=None, composite=None, n=scored)
    )
    reading = BenchReading.read(
        own,
        round=bench_pass.round,
        sp_hash=bench_pass.sp_hash,
        headline=BENCH_HEADLINE,
    )
    outcome = PassOutcome(ReadingState.READ, None, scored, expected, bench_pass.reads_before)
    return _PassReading(bench_pass, reading, outcome, sheet)


def bench_series(
    stores: Stores, passes: BenchPasses | None, scorer: Scorer, *, rounds: Sequence[RoundOutcome]
) -> tuple[float | None, list[float | None]]:
    """Per L1 round, the STANDING selection's headline on the bench rows, held forward across rounds that held; ``None`` where its pass read nothing or was never sent."""
    if passes is None:
        return None, []
    read = partial(_read_pass, stores, scorer=scorer, tolerance=passes.tolerance)

    def headline(bench_pass: BenchPass) -> float | None:
        reading = read(bench_pass).reading
        return None if reading is None or reading.level is None else reading.level.value

    standing = origin = headline(passes.origin)
    series: list[float | None] = []
    for outcome in rounds:
        if outcome.election is not None and outcome.election.selected:
            head = outcome.head
            assert head is not None, "a round that selected ends on the arm it selected"
            taken = passes.pass_of(head.individual.id)
            # Ungraded (an `at_end` line's, or a pause cut its pass): never the one it displaced.
            standing = None if taken is None else headline(taken)
        series.append(standing)
    return origin, series


def _bench_member(path: CyclePath, read: _PassReading, *, instrument_id: str) -> MemberRows | None:
    """A pass that read nothing is no member."""
    if read.reading is None:
        return None
    bench_pass = read.bench_pass
    return MemberRows(
        address=MemberAddress(
            path=path,
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
        cell_set_id=bench_split(bench_pass.sample_keys).id,
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
            cell_set=None if passes is None else bench_split(passes.origin.sample_keys),
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
        scopes=[] if graded is None else _read_scopes(graded),
    )


def _on_rows(sheet: CellSheet, keys: Sequence[str]) -> CellSheet:
    held = set(keys)
    return sheet.where(lambda cell: cell.key in held)


def _read_scopes(graded: _Graded) -> list[BenchScope]:
    """Each dataset's rows read and paired apart; none where the bench measures one."""
    groups = graded.origin.bench_pass.by_dataset()
    if len(groups) < 2:
        return []

    def level(read: _PassReading | None, keys: list[str]) -> OwnLevel | None:
        if read is None or read.reading is None:
            return None
        return own_level(_on_rows(read.sheet, keys))

    def narrowed(member: MemberRows | None, keys: list[str]) -> MemberRows | None:
        if member is None:
            return None
        return replace(member, sheet=_on_rows(member.sheet, keys), cell_set_id=bench_split(keys).id)

    scopes: list[BenchScope] = []
    for dataset, keys in sorted(groups.items(), key=lambda group: group[0] or ""):
        a, b = (narrowed(m, keys) for m in graded.members)
        scopes.append(
            BenchScope(
                dataset=dataset,
                rows=len(keys),
                origin=level(graded.origin, keys),
                selected=level(graded.selected, keys),
                vs_origin=None
                if a is None or b is None or graded.vs_origin is None
                else pair_on_bench(a, b, sample_keys=keys, instrument_id=a.instrument_id),
            )
        )
    return scopes


def bench_members(
    stores: Stores,
    passes: BenchPasses,
    scorer: Scorer,
    *,
    line: BenchLine,
) -> tuple[MemberRows | None, MemberRows | None]:
    return _grade_bench(stores, passes, scorer, line).members


def _standing_pass(passes: BenchPasses, run: LineRun) -> BenchPass | None:
    if run.selection is None or not run.rounds_closed:
        return None
    return passes.pass_of(run.selection.key)


def _grade_bench(stores: Stores, passes: BenchPasses, scorer: Scorer, line: BenchLine) -> _Graded:
    instrument_id = line.instrument_id
    assert instrument_id is not None, "a line that banked a pass drew its bank"
    read = partial(_read_pass, stores, scorer=scorer, tolerance=passes.tolerance)
    member = partial(_bench_member, (line.hop,), instrument_id=instrument_id)
    origin = read(passes.origin)
    a = member(origin)
    selected_pass = _standing_pass(passes, line.run)
    if selected_pass is None:
        return _Graded(origin, None, (a, None), None)
    # One pass read twice is one member: the pair says `same_individual`, never a 0.0 lift.
    selected = origin if selected_pass == passes.origin else read(selected_pass)
    b = member(selected)
    if a is None or b is None:
        return _Graded(origin, selected, (a, b), None)
    vs_origin = pair_on_bench(
        a, b, sample_keys=passes.origin.sample_keys, instrument_id=instrument_id
    )
    return _Graded(origin, selected, (a, b), vs_origin)


def _reads_before(session: Session) -> int:
    return bench_reads(
        session.store,
        bank=session.bank,
        sample_keys=frozenset(s.key for s in session.state.scoring.require_partition().bench),
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
    # No `round=` on the bracket: it would move the dashboard's round back to the one being graded.
    cb.on_phase(
        CampaignPhase.BENCH,
        "enter",
        view=BenchEnterView(
            subject=subject,
            arm=ArmKey(individual_id),
            label=label,
            sp_hash=search_point.sp_hash(session.pipeline_schema),
            round=round_num,
            rows=len(session.state.scoring.require_partition().bench),
        ),
    )
    try:
        return await _pass_on_bench(
            session, search_point, individual_id=individual_id, round_num=round_num
        )
    finally:
        cb.on_phase(CampaignPhase.BENCH, "exit")


async def member_on_bench(
    session: Session,
    search_point: JobSearchPoint,
    *,
    path: CyclePath,
    individual_id: str,
    round_num: int,
) -> MemberRows | None:
    """A pass outside the line's record, read on the line's rule."""
    taken = await _pass_on_bench(
        session, search_point, individual_id=individual_id, round_num=round_num
    )
    scorer = session.state.scoring.require_scorer()
    read = _read_pass(session.store, taken, scorer, tolerance=_tolerance(session))
    return _bench_member(path, read, instrument_id=session.instrument_id)


async def _pass_on_bench(
    session: Session, search_point: JobSearchPoint, *, individual_id: str, round_num: int
) -> BenchPass:
    bench = list(session.state.scoring.require_partition().bench)
    # Counted before the send, so the pass is never one of its own readers.
    reads_before = _reads_before(session)
    scored = await score_search_point(
        search_point,
        bench,
        session,
        label=MeasurementRole.BENCH,
        measured=None,
    )
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
        sp_hash=search_point.sp_hash(session.pipeline_schema),
        cells=scored.cells,
        sample_keys=[s.key for s in bench],
        stop=stop,
        scorer_id=session.state.scoring.require_scorer().id,
        reads_before=reads_before,
        measured_datasets={s.key: s.measured_dataset for s in bench if s.measured_dataset},
    )


def declare_bench(cb: RunCallbacks, score: BenchScore) -> BenchScore:
    cb.on_phase(CampaignPhase.BENCH, "scored", view=BenchScoredView(bench=score))
    return score


def _tolerance(session: Session) -> int:
    split = session.state.scoring.require_partition().split
    return split.tolerance if split is not None else 0


def graded(
    cb: RunCallbacks,
    session: Session,
    bench_pass: BenchPass,
    *,
    subject: BenchSubject,
    label: str,
    reserve: Budget | None = None,
) -> None:
    """The one write a line's passes are folded from: a pass no call here follows is lost with the process."""
    tolerance = _tolerance(session)
    read = _read_pass(
        session.store, bench_pass, session.state.scoring.require_scorer(), tolerance=tolerance
    )
    cb.on_phase(
        CampaignPhase.BENCH,
        "graded",
        view=BenchGradedView(
            subject=subject,
            bench_pass=bench_pass,
            tolerance=tolerance,
            reserve_usd=None if reserve is None else reserve.usd_amount,
            reserve_tokens=None if reserve is None else reserve.token_amount,
            reading=read.reading,
            state=read.outcome.state,
            label=label,
        ),
    )


async def grade_round_selection(
    cycle: Cycle, session: Session, round_result: RoundResult, *, cb: RunCallbacks
) -> None:
    passes, election, head = cycle.bench_passes, round_result.election, round_result.head
    if not (
        cycle.config.bench_trigger == "each_round"
        and election is not None
        and election.selected
        and passes is not None
    ):
        return
    assert head is not None, "a round that selected ends on the arm it selected"
    label = head.label
    taken = await score_on_bench(
        session,
        cycle.selected_sp,
        individual_id=head.individual.id,
        subject="selected",
        label=label,
        round_num=round_result.round,
        cb=cb,
    )
    graded(cb, session, taken, subject="selected", label=label)
    cycle.bench_passes = _with_selection(passes, taken)


def _with_selection(passes: BenchPasses, taken: BenchPass) -> BenchPasses:
    return passes.model_copy(
        update={"selections": {**passes.selections, taken.candidate_id: taken}}
    )


def reserve_selection_pass(cycle: Cycle, session: Session) -> None:
    """The set-aside opens at the ORIGIN's price; a selection that writes more would end its pass short."""
    book = session.state.control.book
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
    usd = len(session.state.scoring.require_partition().bench) * fmean(costs) + max(costs)
    if usd > book.set_aside_usd:
        book.set_aside(usd, round(book.set_aside_tokens * usd / book.set_aside_usd))


async def bench_selection(
    session: Session,
    rounds: Sequence[RoundOutcome],
    *,
    points: PointContext,
    banked: BenchPasses,
    cb: RunCallbacks,
) -> BenchPasses:
    """Only a pause escapes the pass: any other stop ends it short, banked as a stopped pass."""
    origin = banked.origin
    picked = declared_selection(rounds)
    head = picked.head
    assert head is not None, "a closed round names the individual it ended on"
    if head.individual.id == origin.candidate_id:
        return banked
    selected_sp = points.measured(head)
    selected_hash = selected_sp.sp_hash(session.pipeline_schema)
    held = banked.pass_of(head.individual.id)
    if held is not None and held.answers(
        selected_hash, origin.sample_keys, session.state.scoring.require_scorer().id
    ):
        return banked
    try:
        selected = await score_on_bench(
            session,
            selected_sp,
            individual_id=head.individual.id,
            subject="selected",
            label=head.label,
            round_num=picked.round,
            cb=cb,
        )
    except RUN_STOPS as stop:
        reason = run_stop_reason(stop)
        if STOP_REASON_INFO[reason].outcome is StopOutcome.PAUSED:
            raise
        selected = BenchPass(
            round=picked.round,
            candidate_id=head.individual.id,
            sp_hash=selected_hash,
            cells=[],
            sample_keys=origin.sample_keys,
            stop=PassStop(cause=reason, warning=None),
            scorer_id=session.state.scoring.require_scorer().id,
            reads_before=_reads_before(session),
            measured_datasets=origin.measured_datasets,
        )
    graded(cb, session, selected, subject="selected", label=head.label)
    return _with_selection(banked, selected)
