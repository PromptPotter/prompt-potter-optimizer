"""Only the cycle holding the campaign's LINE banks or grades; one beside it does neither."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import partial
from statistics import fmean
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.datasets.authored import scorer_of
from promptpotter.application.jobs.launcher.admission import campaign_refusal
from promptpotter.application.pipeline_resolve import cycle_config
from promptpotter.application.runner.termination import run_stop_reason
from promptpotter.application.scoring.closed_rounds import (
    closed_rounds,
    cycle_instrument,
    walked_rows,
)
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
from promptpotter.domain.arms import PointContext, candidate_label
from promptpotter.domain.bench import (
    BENCH_HEADLINE,
    COLUMN_GRADE,
    BenchColumn,
    BenchReading,
    BenchScope,
    BenchScore,
    BenchSubject,
    BenchTrigger,
    CampaignRefusal,
    LiftCost,
    LineRun,
    OwnLevel,
    PassOutcome,
    PassStop,
    bench_split,
    bench_status,
)
from promptpotter.domain.campaign import Campaign, worked_seconds
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.l4.proxies import line_round_levels, spent_on_line
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    ArmKey,
    ArmName,
    CellSetName,
    Estimate,
    MeasurandUnit,
    MemberAddress,
    PairedReading,
    ReadingState,
)
from promptpotter.domain.pass_kinds import PASSES, individual_cells
from promptpotter.domain.phase_views import (
    BenchScoredView,
    VerificationEnterView,
    VerificationGradedView,
)
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    CampaignPhase,
    RunPhase,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.records.partition import PartitionRecord
from promptpotter.domain.records.run_records import LaunchClockRecord, PhaseRecord
from promptpotter.domain.results import RoundOutcome, RoundResult
from promptpotter.domain.run_standing import LineOutlay, declared_selection
from promptpotter.domain.scoring import ROW_GRADES, CellSheet, Scorer, WalkedCell
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.domain.spend import Budget, SpendRollup
from promptpotter.domain.verification import BenchPass, BenchPasses
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.spend_book import require_spend_book
from promptpotter.infrastructure.store.archive_queries import bench_reads
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_bench_open,
    scan_bench_passes,
    scan_launch_clocks,
    scan_ledger_spend,
    scan_ledger_wall_clock,
)
from promptpotter.infrastructure.store.read_model import LedgerSpan
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.initialization.session import LaunchClock, Session


_BRACKET = PASSES[MeasurementRole.BENCH].bracket


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
    inner: bool
    # The campaign's own refusal (`admission.py::campaign_refusal`): its pass is refused with it.
    refused: CampaignRefusal | None
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


def _read_pass(
    stores: Stores, bench_pass: BenchPass, scorer: Scorer, *, tolerance: int
) -> _PassReading:
    """A pass stopped short, or past *tolerance* unscored rows, reads NOTHING, never a level over fewer rows."""
    expected = len(bench_pass.sample_keys)
    held_out = individual_cells([bench_pass.walk()], bench_pass.arm.key, RoleScope.BENCH)
    sheet = walked_rows(stores, held_out, scorer).standing()
    scored = len(sheet.scoreable)

    def unread(state: ReadingState) -> _PassReading:
        outcome = PassOutcome(state, bench_pass.stop, scored, expected, bench_pass.reads_before)
        return _PassReading(bench_pass, None, outcome, sheet)

    if bench_pass.stop is not None:
        return unread(bench_pass.stop.state)
    if expected - scored > tolerance:
        return unread(ReadingState.PAST_TOLERANCE)
    # Rows on several datasets read no level: `BenchScore.scopes` reads each apart.
    own = (
        own_level(sheet, scorer.fitness_unit)
        if len(bench_pass.by_dataset()) < 2
        else OwnLevel(fitness=None, composite=None, n=scored)
    )
    reading = BenchReading.read(
        own,
        round=bench_pass.arm.round,
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
        if outcome.election is not None and outcome.election.selected is not None:
            head = outcome.head
            assert head is not None, "a round that selected ends on the arm it selected"
            taken = passes.pass_of(head.individual.id)
            # Ungraded (an `at_end` line's) or stopped short: never the one it displaced.
            standing = None if taken is None else headline(taken)
        series.append(standing)
    return origin, series


class LineSpan(NamedTuple):
    """One cycle of a line: every round it stood on, and the round its fork cut at."""

    rounds: list[RoundResult]
    cut_round: int | None

    @property
    def own(self) -> list[RoundResult]:
        return [rr for rr in self.rounds if spent_on_line(rr.round, self.cut_round)]


def line_spans(stores: Stores, scorer: Scorer, hop: CycleHop) -> list[LineSpan]:
    """The supersede line ending at *hop*, oldest first: a round a rebase discarded was still spent."""
    spans: list[LineSpan] = []
    for at in stores.campaigns.line_to(hop):
        index = stores.campaigns.load(at, moment=None)
        assert index is not None, "a cycle on a line folds to an index"
        spans.append(LineSpan(closed_rounds(stores, at, scorer, moment=None), index.cut_round))
    return spans


def line_bench_series(
    stores: Stores, passes: BenchPasses | None, scorer: Scorer, spans: Sequence[LineSpan]
) -> tuple[float | None, list[float | None]]:
    """`bench_series` per cycle of the line, folded by the hashed `proxies.py::line_round_levels`."""
    if passes is None:
        return None, []
    origin: float | None = None
    per_cycle: list[tuple[list[tuple[int, float | None]], int | None]] = []
    for span in spans:
        origin, series = bench_series(stores, passes, scorer, rounds=span.rounds)
        rounds = [rr.round for rr in span.rounds]
        per_cycle.append((list(zip(rounds, series, strict=True)), span.cut_round))
    return origin, line_round_levels(per_cycle)


def _bench_member(path: CyclePath, read: _PassReading, *, instrument_id: str) -> MemberRows | None:
    """A pass that read nothing is no member."""
    if read.reading is None:
        return None
    bench_pass = read.bench_pass
    return MemberRows(
        address=MemberAddress(
            path=path,
            individual_id=bench_pass.arm.key,
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
    a: MemberRows,
    b: MemberRows,
    *,
    scorer: Scorer,
    sample_keys: Sequence[str],
    instrument_id: str,
) -> PairedReading:
    """*b* over *a*, under *scorer*'s grades: a member graded under another formula is refused."""
    return read_pair(
        a=a,
        b=b,
        cell_set=CellSetName.BENCH_SPLIT,
        cells=sample_keys,
        masked=False,
        dataset_hash=None,
        measurands=grade_measurands(scorer),
        spec=ROUND_LIFT_SPEC,
        scope=RoleScope.BENCH,
        instrument_id=instrument_id,
    )


def own_level(sheet: CellSheet, fitness_unit: MeasurandUnit) -> OwnLevel:
    def column(name: BenchColumn) -> Estimate | None:
        unit = fitness_unit if name == "fitness" else "score"
        return level_band(sheet, ROW_GRADES[COLUMN_GRADE[name]], unit)

    return OwnLevel(
        fitness=column("fitness"),
        composite=column("composite"),
        n=len(sheet.scoreable),
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
        on_line=line.on_line,
        inner=line.inner,
        refused=line.refused,
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
    return BenchScore(
        bench_size=line.held_out if passes is None else len(passes.origin.sample_keys),
        scorer_id=scorer.id,
        headline=BENCH_HEADLINE,
        status=status,
        origin=None if graded is None else graded.origin.reading,
        selected=None if selected is None else selected.reading,
        vs_origin=vs_origin,
        cost=LiftCost.of(vs_origin, None if line.spend is None else line.spend.search_incurred_usd),
        scopes=[] if graded is None else _read_scopes(graded, scorer),
    )


def _on_rows(sheet: CellSheet, keys: Sequence[str]) -> CellSheet:
    held = set(keys)
    return sheet.where(lambda cell: cell.key in held)


def _read_scopes(graded: _Graded, scorer: Scorer) -> list[BenchScope]:
    """Each dataset's rows read and paired apart; none where the bench measures one."""
    groups = graded.origin.bench_pass.by_dataset()
    if len(groups) < 2:
        return []

    def level(read: _PassReading | None, keys: list[str]) -> OwnLevel | None:
        if read is None or read.reading is None:
            return None
        return own_level(_on_rows(read.sheet, keys), scorer.fitness_unit)

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
                else pair_on_bench(
                    a, b, scorer=scorer, sample_keys=keys, instrument_id=a.instrument_id
                ),
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
        a, b, scorer=scorer, sample_keys=passes.origin.sample_keys, instrument_id=instrument_id
    )
    return _Graded(origin, selected, (a, b), vs_origin)


def _reads_before(session: Session) -> int:
    return bench_reads(
        session.store,
        bank=session.bank,
        sample_keys=frozenset(s.key for s in session.state.scoring.partition.bench),
    )


async def score_on_bench(
    session: Session,
    search_point: JobSearchPoint,
    *,
    individual_id: str,
    subject: BenchSubject,
    label: str,
    round_num: int,
    ledger: CycleEventLog,
) -> BenchPass:
    """A run stop escapes once the pass it cut is graded on the cells it held."""
    taken, stop = await _send_on_bench(
        session,
        search_point,
        arm=ArmName(key=ArmKey(individual_id), round=round_num, label=label),
        subject=subject,
        ledger=ledger,
    )
    if stop is not None:
        raise stop
    return taken


async def _send_on_bench(
    session: Session,
    search_point: JobSearchPoint,
    *,
    arm: ArmName,
    subject: BenchSubject,
    ledger: CycleEventLog,
) -> tuple[BenchPass, BaseException | None]:
    """The pass, and the run stop that cut it: a stopped pass is graded here, a whole one by its caller."""
    # No `round=` on the bracket: it would move the dashboard's round back to the one being graded.
    ledger.append(
        PhaseRecord(
            phase=_BRACKET,
            event="enter",
            view=VerificationEnterView(
                verification=MeasurementRole.BENCH,
                subject=subject,
                arm=arm,
                sp_hash=search_point.sp_hash(session.pipeline_schema),
                rows=len(session.state.scoring.partition.bench),
                strategy=None,
            ),
        )
    )
    # Counted before the send, so the pass is never one of its own readers.
    reads_before = _reads_before(session)
    try:
        scored = await score_search_point(
            search_point,
            list(session.state.scoring.partition.bench),
            session,
            kind=MeasurementRole.BENCH,
            measured=arm.key,
        )
        signal = scored.signal
        stop = (
            # Every run stop banks the pass as stopped, on the cells its walk took before it (bl3.B4).
            PassStop(cause=run_stop_reason(scored.halt), warning=None)
            if scored.halt is not None
            else None
            if scored.stopped is None
            else PassStop(
                cause=scored.stopped,
                warning=signal.check_result["dominant_warning"]
                if signal is not None and signal.check_name == SCORING_ERROR_ABORT
                else None,
            )
        )
        taken = _bench_pass(
            session, search_point, arm=arm, cells=scored.cells, stop=stop, reads_before=reads_before
        )
        if scored.halt is not None:
            graded(ledger, session, taken, subject=subject)
        return taken, scored.halt
    finally:
        ledger.append(PhaseRecord(phase=_BRACKET, event="exit"))


def _bench_pass(
    session: Session,
    search_point: JobSearchPoint,
    *,
    arm: ArmName,
    cells: list[WalkedCell],
    stop: PassStop | None,
    reads_before: int,
) -> BenchPass:
    bench = session.state.scoring.partition.bench
    return BenchPass(
        arm=arm,
        sp_hash=search_point.sp_hash(session.pipeline_schema),
        cells=cells,
        sample_keys=[s.key for s in bench],
        stop=stop,
        scorer_id=session.state.scoring.scorer.id,
        reads_before=reads_before,
        measured_datasets={s.key: s.measured_dataset for s in bench if s.measured_dataset},
    )


def _tolerance(session: Session) -> int:
    split = session.state.scoring.partition.split
    return split.tolerance if split is not None else 0


def graded(
    ledger: CycleEventLog,
    session: Session,
    bench_pass: BenchPass,
    *,
    subject: BenchSubject,
    reserve: Budget | None = None,
) -> None:
    """The one write a line's passes are folded from: a pass no call here follows is lost with the process."""
    tolerance = _tolerance(session)
    read = _read_pass(session.store, bench_pass, session.state.scoring.scorer, tolerance=tolerance)
    ledger.append(
        PhaseRecord(
            phase=_BRACKET,
            event="graded",
            view=VerificationGradedView(
                verification=bench_pass,
                subject=subject,
                tolerance=tolerance,
                reserve_usd=None if reserve is None else reserve.usd_amount,
                reserve_tokens=None if reserve is None else reserve.token_amount,
                reading=read.reading,
                state=read.outcome.state,
                refusal=None,
            ),
        )
    )


def _with_selection(passes: BenchPasses, taken: BenchPass) -> BenchPasses:
    return passes.model_copy(update={"selections": {**passes.selections, taken.arm.key: taken}})


def reserve_selection_pass(cycle: Cycle, session: Session) -> None:
    """The set-aside opens at the ORIGIN's price; a selection that writes more would end its pass short."""
    book = require_spend_book("the selection's set-aside")
    if not book.set_aside_usd:
        return
    costs = [
        cost
        for cell in cycle.selection.results
        if (cost := cell_channels_of(cell.facts, cell.grade).get("cost")) is not None
    ]
    if not costs:
        return
    # One cell on top of the mean: the last row is admitted at the dearest bill, never the average.
    usd = len(session.state.scoring.partition.bench) * fmean(costs) + max(costs)
    if usd > book.set_aside_usd:
        book.set_aside(usd, round(book.set_aside_tokens * usd / book.set_aside_usd))


async def bench_selection(
    session: Session,
    rounds: Sequence[RoundOutcome],
    *,
    points: PointContext,
    banked: BenchPasses,
    ledger: CycleEventLog,
) -> BenchPasses:
    """Only a pause escapes the pass: any other stop ends it short, banked as a stopped pass."""
    origin = banked.origin
    picked = declared_selection(rounds)
    head = picked.head
    assert head is not None, "a closed round names the individual it ended on"
    if head.individual.id == origin.arm.key:
        return banked
    selected_sp = points.measured(head)
    selected_hash = selected_sp.sp_hash(session.pipeline_schema)
    held = banked.pass_of(head.individual.id)
    if held is not None and held.answers(
        selected_hash, origin.sample_keys, session.state.scoring.scorer.id
    ):
        return banked
    selected, stop = await _send_on_bench(
        session,
        selected_sp,
        arm=ArmName(key=ArmKey(head.individual.id), round=picked.round, label=head.label),
        subject="selected",
        ledger=ledger,
    )
    if stop is None:
        graded(ledger, session, selected, subject="selected")
    elif STOP_REASON_INFO[run_stop_reason(stop)].outcome is StopOutcome.PAUSED:
        raise stop
    return _with_selection(banked, selected)


def _line(stores: Stores, hop: CycleHop) -> list[CycleHop] | None:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        return None
    line = stores.campaigns.line(campaign.root_hop)
    return line if line[-1] == hop else None


def bank_campaign_result(
    stores: Stores,
    hop: CycleHop,
    *,
    ledger: CycleEventLog,
    clock: LaunchClock,
    finished_at: str,
    optimizer_phases: frozenset[str],
) -> None:
    """Once per launch, on the cycle holding the line as it ends: a rebased parent banks none."""
    line = _line(stores, hop)
    if line is None:
        return
    ledger.append(launch_clock_record(stores, line, clock, finished_at, optimizer_phases))


def launch_clock_record(
    stores: Stores,
    line: list[CycleHop],
    clock: LaunchClock,
    finished_at: str,
    phases: frozenset[str],
) -> LaunchClockRecord:
    """This launch's clock over the line's ledgers, from its start to *finished_at*."""
    return LaunchClockRecord(
        started_at=clock.started_at,
        finished_at=finished_at,
        clock=scan_ledger_wall_clock(
            [stores.campaigns.ledger(h) for h in line],
            started_at=clock.started_at,
            finished_at=finished_at,
            optimizer_phases=phases,
        ),
    )


def line_launches(stores: Stores, campaign: Campaign) -> list[LaunchClockRecord]:
    """Each cycle's OWN appends: a launch banks on the cycle holding the line as it ends."""
    return scan_launch_clocks(
        LedgerSpan(stores.campaigns.ledger(h)) for h in stores.campaigns.line(campaign.root_hop)
    )


def line_outlay(session: Session, phases: frozenset[str]) -> LineOutlay:
    """What the line has paid by now: every cycle's spend read whole, and the worked seconds of
    each ended launch and of this one to now."""
    stores, clock = session.store, session.state.clock
    campaign = stores.campaigns.load_campaign(session.hop.campaign_id)
    assert campaign is not None and clock is not None, "a closing round's launch is on a line"
    line = stores.campaigns.line(campaign.root_hop)
    closing = launch_clock_record(stores, line, clock, utcnow_iso(), phases)
    return LineOutlay(
        search_usd=scan_ledger_spend(
            LedgerSpan(stores.campaigns.ledger(h)) for h in line
        ).spend.search_incurred_usd,
        worked_s=worked_seconds([*line_launches(stores, campaign), closing]),
    )


def _bench_counted(spend: SpendRollup) -> Budget:
    bench = spend.by_kind["bench"]
    return Budget.amount(bench.incurred_usd, bench.input_tokens + bench.output_tokens)


async def bench_origin(
    session: Session,
    origin_sp: JobSearchPoint,
    *,
    origin_id: str,
    spend: SpendRollup,
    ledger: CycleEventLog,
) -> BenchPasses | None:
    stores, hop = session.store, session.hop
    if _line(stores, hop) is None:
        return None
    held = line_passes(stores, hop.campaign_id)
    if held is not None and held.origin.answers(
        origin_sp.sp_hash(session.pipeline_schema),
        [s.key for s in session.state.scoring.partition.bench],
        session.state.scoring.scorer.id,
    ):
        passes = held
    else:
        # The bench bucket's own delta: a search call landing while this pass is out is not reserve.
        before = _bench_counted(spend)
        origin_pass = await score_on_bench(
            session,
            origin_sp,
            individual_id=origin_id,
            subject="origin",
            label=candidate_label(0, 0),
            round_num=0,
            ledger=ledger,
        )
        split = session.state.scoring.partition.split
        after = _bench_counted(spend)
        passes = BenchPasses(
            tolerance=split.tolerance if split is not None else 0,
            origin=origin_pass,
            reserve_usd=after.usd_amount - before.usd_amount,
            reserve_tokens=after.token_amount - before.token_amount,
            # A pass sent beside another reference pairs with nothing on this one.
            selections={},
        )
    graded(
        ledger,
        session,
        passes.origin,
        subject="origin",
        reserve=Budget.amount(passes.reserve_usd, passes.reserve_tokens),
    )
    return passes


def line_run(stores: Stores, hop: CycleHop) -> LineRun:
    # A launch stopped before run init names no cycle.
    named = bool(hop.cycle_id)
    index = stores.campaigns.load(hop, moment=None) if named else None
    standing = None if index is None else index.standing
    # A pass holds the cycle too, and reads SCORING: only the loop's hold is a run still selecting.
    run = stores.run_state(hop) if named else None
    attached = run is not None and run.producer.attached
    scoring = run is not None and run.run_phase is RunPhase.SCORING
    return LineRun(
        selecting=attached and not scoring,
        # The bracket, not the lock alone: `grade-bench` reads this under its own pass's lock.
        pass_open=attached and scan_bench_open(stores.campaigns.ledger(hop)),
        ending=None if index is None else index.stop_reason,
        selection=None if standing is None else standing.selection,
        rounds_closed=0 if standing is None else standing.rounds_closed,
    )


def line_passes(stores: Stores, campaign_id: str) -> BenchPasses | None:
    """The line's OWN ledgers: a root cut from another tree graded its prefix in another epoch."""
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        return None
    return scan_bench_passes(
        LedgerSpan(stores.campaigns.ledger(h)) for h in stores.campaigns.line(campaign.root_hop)
    )


def read_cycle_bench(stores: Stores, campaign: Campaign, hop: CycleHop) -> BenchScore:
    return cycle_bench(stores, campaign, hop, cycle_config(stores, campaign, hop).config)


def cycle_bench(
    stores: Stores, campaign: Campaign, hop: CycleHop, config: CampaignConfig
) -> BenchScore:
    return bench_under(
        stores,
        campaign,
        hop,
        line_passes(stores, campaign.campaign_id),
        config,
        scorer_of(config, verifier_graded=False),
        run=line_run(stores, hop),
    )


def bench_line(
    stores: Stores,
    campaign: Campaign,
    hop: CycleHop,
    config: CampaignConfig,
    *,
    run: LineRun,
) -> BenchLine:
    holder = stores.campaigns.line_holder(campaign.root_hop)
    drawn = stores.campaigns.last(hop, PartitionRecord)
    split = config.dataset_split
    return BenchLine(
        hop=hop,
        on_line=holder == hop,
        inner=stores.inner,
        refused=_refused(stores, campaign),
        held_by=None if holder == hop else holder.cycle_id,
        trigger=config.bench_trigger,
        held_out=len(drawn.bench_ids) if drawn is not None else 0 if split is None else split.bench,
        instrument_id=cycle_instrument(
            drawn, stores.campaigns.standing_rounds(hop, moment=None).rounds
        ),
        run=run,
        # Every cycle read WHOLE: what a superseded cycle paid past its cut is the line's cost too.
        spend=scan_ledger_spend(
            LedgerSpan(stores.campaigns.ledger(h)) for h in stores.campaigns.line(campaign.root_hop)
        ).spend,
    )


def _refused(stores: Stores, campaign: Campaign) -> CampaignRefusal | None:
    """The refusal a pass on *campaign* is admitted under, as its bench line serves it."""
    refused = campaign_refusal(stores, campaign)
    return None if refused is None else refused.refusal


def bench_under(
    stores: Stores,
    campaign: Campaign,
    hop: CycleHop,
    passes: BenchPasses | None,
    config: CampaignConfig,
    scorer: Scorer,
    *,
    run: LineRun,
) -> BenchScore:
    line = bench_line(stores, campaign, hop, config, run=run)
    return read_bench(stores, passes if line.on_line else None, scorer, line=line)


def declare_line_bench(
    session: Session,
    config: CampaignConfig,
    ledger: CycleEventLog,
    *,
    passes: BenchPasses | None,
    selecting: bool,
    ending: StopReason | None,
) -> BenchScore:
    stores, hop = session.store, session.hop
    scorer = scorer_of(config, verifier_graded=False)
    # The index says `selecting` / `ending` only once the run closes; the launch's own word wins.
    run = line_run(stores, hop)._replace(selecting=selecting, ending=ending)
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None or not hop.cycle_id:
        split = config.dataset_split
        line = BenchLine(
            hop=hop,
            on_line=False,
            inner=stores.inner,
            refused=None if campaign is None else _refused(stores, campaign),
            held_by=None,
            trigger=config.bench_trigger,
            held_out=0 if split is None else split.bench,
            instrument_id=session.instrument_id,
            run=run,
            spend=None,
        )
        score = read_bench(stores, None, scorer, line=line)
    else:
        held = passes if passes is not None else line_passes(stores, hop.campaign_id)
        score = bench_under(stores, campaign, hop, held, config, scorer, run=run)
    ledger.append(
        PhaseRecord(phase=CampaignPhase.BENCH, event="scored", view=BenchScoredView(bench=score))
    )
    return score
