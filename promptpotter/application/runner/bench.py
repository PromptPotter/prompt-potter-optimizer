"""Only the cycle holding the campaign's LINE banks or grades; one beside it does neither."""

from __future__ import annotations

from collections.abc import Collection, Sequence
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
    walked_rows,
)
from promptpotter.application.scoring.formula.compiler import cell_channels_of
from promptpotter.application.scoring.paired import (
    MemberRows,
    absent_pair,
    grade_measurands,
    mean_lift,
    read_pair,
)
from promptpotter.application.scoring.search_point_scorer import (
    SCORING_ERROR_ABORT,
    score_search_point,
)
from promptpotter.application.scoring.selection import level_band
from promptpotter.domain.arms import (
    BankedSearchPointError,
    Concluded,
    PointContext,
    candidate_label,
)
from promptpotter.domain.bench import (
    BENCH_HEADLINE,
    COLUMN_GRADE,
    BenchColumn,
    BenchCurve,
    BenchReading,
    BenchScope,
    BenchScore,
    BenchSubject,
    BenchTrigger,
    CampaignRefusal,
    LiftCost,
    LiveCycleRun,
    OwnLevel,
    PassAdmission,
    PassOutcome,
    PassStop,
    bench_split,
    bench_status,
    verify_admission,
)
from promptpotter.domain.campaign import Campaign, CampaignCycle, worked_seconds
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
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
from promptpotter.domain.phase_acts import PassOpened
from promptpotter.domain.phases import STOP_REASON_INFO, StopOutcome, StopReason
from promptpotter.domain.records.held_out import BenchScoredRecord, PassGradedRecord
from promptpotter.domain.records.partition import PartitionRecord
from promptpotter.domain.records.run_records import LaunchClockRecord, PhaseRecord
from promptpotter.domain.results import RoundOutcome, RoundResult
from promptpotter.domain.run_standing import LineOutlay, declared_selection
from promptpotter.domain.scoring import ROW_GRADES, CellSheet, Scorer, WalkedCell
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.domain.spend import Budget, SpendRollup
from promptpotter.domain.verification import BenchPass, BenchPasses
from promptpotter.infrastructure.llm.spend_book import require_spend_book
from promptpotter.infrastructure.llm.telemetry import append_record
from promptpotter.infrastructure.producer_lock import pass_held
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
    on_live_cycle: bool
    inner: bool
    # The campaign's own refusal (`admission.py::campaign_refusal`): its pass is refused with it.
    refused: CampaignRefusal | None
    held_by: str | None
    trigger: BenchTrigger
    held_out: int
    # The pair refuses two passes whose instruments differ; a cycle that drew no bank names none.
    instrument_id: str | None
    run: LiveCycleRun
    spend: SpendRollup | None


class _Graded(NamedTuple):
    origin: _PassReading
    selected: _PassReading | None
    members: tuple[MemberRows | None, MemberRows | None]
    vs_origin: PairedReading | None


def _read_pass(
    stores: Stores,
    bench_pass: BenchPass,
    scorer: Scorer,
    *,
    tolerance: int,
    refused: Collection[str],
) -> _PassReading:
    """A pass refused, stopped short, or past *tolerance* unscored rows, reads NOTHING, never a
    level over fewer rows."""
    expected = len(bench_pass.sample_keys)
    held_out = individual_cells([bench_pass.walk()], bench_pass.arm.key, RoleScope.BENCH)
    sheet = walked_rows(stores, held_out, scorer).standing()
    scored = len(sheet.scoreable)

    def unread(state: ReadingState) -> _PassReading:
        outcome = PassOutcome(state, bench_pass.stop, scored, expected, bench_pass.reads_before)
        return _PassReading(bench_pass, None, outcome, sheet)

    if bench_pass.arm.key in refused:
        return unread(ReadingState.MEMBER_UNSCOREABLE)
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


def spent_on_line(rnd: int, cut_round: int | None) -> bool:
    """A round its cycle ran itself (from *cut_round* on, a fork's), never the origin it is differenced against."""
    return rnd > 0 and (cut_round is None or rnd >= cut_round)


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


def line_round_budget(spans: Sequence[LineSpan], cap: int | None) -> int:
    """The held-out area's width: uncapped, the rounds the line spent."""
    spent = sum(len(span.own) for span in spans)
    if cap is None:
        return spent
    # The cycle's cap holds what its line had left: the line's adds every round a rebase left.
    return max(spent, cap + spent - sum(1 for rr in spans[-1].rounds if rr.round > 0))


def _adopted(rr: RoundOutcome) -> tuple[int, Concluded] | None:
    if rr.election is None or rr.election.selected is None:
        return None
    head = rr.head
    assert head is not None, "a round that selected ends on the arm it selected"
    return rr.round, head


def _stood_on(spans: Sequence[LineSpan]) -> list[tuple[int, Concluded] | None]:
    """What each round the line spent stood on, oldest first: its adopting round and arm, ``None`` the origin."""
    stood: list[tuple[int, Concluded] | None] = []
    for span in spans:
        held = None
        for rr in span.rounds:
            held = _adopted(rr) or held
            if spent_on_line(rr.round, span.cut_round):
                stood.append(held)
    return stood


def _standing(spans: Sequence[LineSpan], origin: str) -> list[str]:
    return [origin if held is None else held[1].individual.id for held in _stood_on(spans)]


def read_curve(
    stores: Stores,
    passes: BenchPasses | None,
    scorer: Scorer,
    spans: Sequence[LineSpan],
    *,
    budget: int,
) -> BenchCurve | None:
    """The last standing selection held forward to *budget*; ``None`` where no origin pass banked."""
    if passes is None:
        return None
    origin = passes.origin.arm.key
    spent = _standing(spans, origin)
    standing = [*spent, *[spent[-1] if spent else origin] * (budget - len(spent))]
    read = partial(
        _read_pass, stores, scorer=scorer, tolerance=passes.tolerance, refused=passes.refused
    )
    reads = {
        key: None if (taken := passes.pass_of(key)) is None else read(taken)
        for key in dict.fromkeys([origin, *standing])
    }
    state = next(
        (
            ReadingState.NOT_ASKED if held is None else held.outcome.state
            for held in reads.values()
            if held is None or held.reading is None
        ),
        ReadingState.READ,
    )
    grade = ROW_GRADES[COLUMN_GRADE[BENCH_HEADLINE]]
    columns = {key: {} if held is None else held.sheet.column(grade) for key, held in reads.items()}
    rows = {
        row: [columns[key].get(row) for key in (origin, *standing)]
        for row in passes.origin.sample_keys
    }
    paired = [
        scored
        for values in rows.values()
        if len(scored := [value for value in values if value is not None]) == len(values)
    ]
    if state is ReadingState.READ and not standing:
        state = ReadingState.NO_SELECTION
    elif state is ReadingState.READ and len(paired) < 2:
        state = ReadingState.UNDER_TWO_CELLS
    area = (
        mean_lift(
            grade_measurands(scorer)[0],
            [r[0] for r in paired],
            [fmean(r[1:]) for r in paired],
            pooled=len(standing) > 1,
        )
        if state is ReadingState.READ
        else None
    )

    def level(key: str) -> float | None:
        held = reads[key]
        reading = None if held is None else held.reading
        return None if reading is None or reading.level is None else reading.level.value

    return BenchCurve(
        state=state,
        origin=level(origin),
        levels=[level(key) for key in standing],
        area=area,
        rows=rows,
    )


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
        return level_band(sheet, COLUMN_GRADE[name], unit)

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
    graded = None if passes is None else _grade_line(stores, passes, scorer, line)
    selected = None if graded is None else graded.selected
    status = bench_status(
        trigger=line.trigger,
        bench_size=line.held_out if passes is None else len(passes.origin.sample_keys),
        on_live_cycle=line.on_live_cycle,
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
    return _grade_line(stores, passes, scorer, line).members


def _grade_line(stores: Stores, passes: BenchPasses, scorer: Scorer, line: BenchLine) -> _Graded:
    instrument_id = line.instrument_id
    assert instrument_id is not None, "a line that banked a pass drew its bank"
    read = partial(
        _read_pass, stores, scorer=scorer, tolerance=passes.tolerance, refused=passes.refused
    )
    member = partial(_bench_member, (line.hop,), instrument_id=instrument_id)
    origin = read(passes.origin)
    a = member(origin)
    standing = line.run.selection if line.run.rounds_closed else None
    selected_pass = None if standing is None else passes.pass_of(standing.key)
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
) -> BenchPass:
    """A run stop escapes once the pass it cut is graded on the cells it held."""
    taken, stop = await _send_on_bench(
        session,
        search_point,
        arm=ArmName(key=ArmKey(individual_id), round=round_num, label=label),
        subject=subject,
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
) -> tuple[BenchPass, BaseException | None]:
    """The pass, and the run stop that cut it: a stopped pass is graded here, a whole one by its caller."""
    # No `round=` on the bracket: it would move the dashboard's round back to the one being graded.
    append_record(
        PhaseRecord(
            phase=_BRACKET,
            step="enter",
            act=PassOpened(
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
        sp_hash = search_point.sp_hash(session.pipeline_schema)
        taken = _bench_pass(session, sp_hash, arm, scored.cells, stop, reads_before)
        if scored.halt is not None:
            graded(session, taken, subject=subject)
        return taken, scored.halt
    finally:
        append_record(PhaseRecord(phase=_BRACKET, step="exit"))


def _bench_pass(
    session: Session,
    sp_hash: str,
    arm: ArmName,
    cells: list[WalkedCell],
    stop: PassStop | None,
    reads_before: int,
) -> BenchPass:
    bench = session.state.scoring.partition.bench
    return BenchPass(
        arm=arm,
        sp_hash=sp_hash,
        cells=cells,
        sample_keys=[s.key for s in bench],
        stop=stop,
        scorer_id=session.state.scoring.scorer.id,
        reads_before=reads_before,
        measured_datasets={s.key: s.measured_dataset for s in bench if s.measured_dataset},
    )


def graded(
    session: Session,
    bench_pass: BenchPass,
    *,
    subject: BenchSubject,
    reserve: Budget | None = None,
    refusal: str | None = None,
) -> None:
    """The one write a line's passes are folded from: a pass no call here follows is lost with the
    process. A *refusal* banks a pass that sent nothing, which the fold reads refused."""
    tolerance = session.state.scoring.partition.split.tolerance
    read = _read_pass(
        session.store,
        bench_pass,
        session.state.scoring.scorer,
        tolerance=tolerance,
        refused=() if refusal is None else (bench_pass.arm.key,),
    )
    append_record(
        PassGradedRecord(
            verification=bench_pass,
            subject=subject,
            tolerance=tolerance,
            reserve_usd=None if reserve is None else reserve.usd_amount,
            reserve_tokens=None if reserve is None else reserve.token_amount,
            reading=read.reading,
            state=read.outcome.state,
            refusal=refusal,
        )
    )


def _adoptions(
    session: Session, rounds: Sequence[RoundOutcome], *, origin: str, trigger: BenchTrigger
) -> dict[str, tuple[ArmName, Concluded]]:
    """The final selection first, then under `every_adoption` each other one its line stood on, latest first; never the origin."""
    adopting = [_adopted(declared_selection(rounds))]
    if trigger == "every_adoption":
        spans = line_spans(session.store, session.state.scoring.scorer, session.hop)
        adopting += reversed(_stood_on(spans))
    roster: dict[str, tuple[ArmName, Concluded]] = {}
    for rnd, head in filter(None, adopting):
        arm = ArmName(key=ArmKey(head.individual.id), round=rnd, label=head.label)
        roster.setdefault(head.individual.id, (arm, head))
    roster.pop(origin, None)
    return roster


def reserve_selection_pass(cycle: Cycle, session: Session) -> None:
    """The set-aside opens at the ORIGIN's price; a selection that writes more would end its pass short."""
    book = require_spend_book("the selection's set-aside")
    aside = book.count().set_aside
    if not aside.usd_amount:
        return
    costs = [
        cost
        for cell in cycle.selection.results
        if (cost := cell_channels_of(cell.facts, cell.grade).get("cost")) is not None
    ]
    if not costs:
        return
    trigger = cycle.config.bench_trigger
    origin = cycle.origin_round.head
    assert origin is not None, "a closed origin round ends on the origin"
    passes = (
        len(_adoptions(session, cycle.rounds, origin=origin.individual.id, trigger=trigger))
        if trigger == "every_adoption"
        else 1
    )
    if not passes:
        return
    # One cell on top of the mean: the last row is admitted at the dearest bill, never the average.
    usd = passes * (len(session.state.scoring.partition.bench) * fmean(costs) + max(costs))
    if usd > aside.usd_amount:
        tokens = round(aside.token_amount * usd / aside.usd_amount)
        book.set_aside(usd, tokens, paid_by=session.backend_client.connector.cell_payer)


async def bench_selection(
    session: Session,
    rounds: Sequence[RoundOutcome],
    *,
    points: PointContext,
    banked: BenchPasses,
    trigger: BenchTrigger,
) -> BenchPasses:
    """Only a pause escapes a pass: any other stop ends it short, banked as a stopped pass, and sends no other."""
    origin = banked.origin
    adopted = _adoptions(session, rounds, origin=origin.arm.key, trigger=trigger)
    # Every member rebuilds before any sends: a moved searchpoint banks its member's pass refused,
    # unspent, and the rest of the roster is graded.
    roster: dict[str, tuple[ArmName, JobSearchPoint]] = {}
    for key, (arm, head) in adopted.items():
        try:
            roster[key] = (arm, points.measured(head))
        except BankedSearchPointError as exc:
            refused = _bench_pass(session, head.sp_hash, arm, [], None, _reads_before(session))
            graded(session, refused, subject="selected", refusal=str(exc))
            banked = banked.with_selection(refused, str(exc))
    for key, (arm, sp) in roster.items():
        held = banked.pass_of(key)
        if held is not None and held.answers(
            sp.sp_hash(session.pipeline_schema), origin.sample_keys, session.state.scoring.scorer.id
        ):
            continue
        taken, stop = await _send_on_bench(session, sp, arm=arm, subject="selected")
        if stop is None:
            graded(session, taken, subject="selected")
        elif STOP_REASON_INFO[run_stop_reason(stop)].outcome is StopOutcome.PAUSED:
            raise stop
        banked = banked.with_selection(taken, None)
        if stop is not None:
            return banked
    return banked


def _line(session: Session) -> list[CycleHop] | None:
    line = session.store.campaigns.line(session.bound_campaign.root_hop)
    return line if line[-1] == session.hop else None


def bank_campaign_result(
    session: Session,
    *,
    clock: LaunchClock,
    finished_at: str,
    optimizer_phases: frozenset[str],
) -> None:
    """Once per launch, on the cycle holding the line as it ends: a rebased parent banks none."""
    line = _line(session)
    if line is None:
        return
    append_record(launch_clock_record(session.store, line, clock, finished_at, optimizer_phases))


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
    return scan_launch_clocks(stores.campaigns.line_spans(campaign.root_hop))


def line_outlay(session: Session, phases: frozenset[str]) -> LineOutlay:
    """What the line has paid by now: every cycle's spend read whole, and the worked seconds of
    each ended launch and of this one to now."""
    stores, clock = session.store, session.state.clock
    assert clock is not None, "a closing round's launch is on a line"
    campaign = session.bound_campaign
    line = stores.campaigns.line(campaign.root_hop)
    closing = launch_clock_record(stores, line, clock, utcnow_iso(), phases)
    return LineOutlay(
        search_usd=scan_ledger_spend(
            stores.campaigns.line_spans(campaign.root_hop)
        ).spend.search_incurred_usd,
        worked_s=worked_seconds([*line_launches(stores, campaign), closing]),
    )


def _bench_counted(stores: Stores, hop: CycleHop) -> Budget:
    """The cycle's bench bucket as its own ledger folds it: the run's rollup is a snapshot each
    bank replaces, so two reads of one never move."""
    spend = scan_ledger_spend([LedgerSpan(stores.campaigns.ledger(hop))]).spend
    bench = spend.by_kind["bench"]
    return Budget.amount(bench.incurred_usd, bench.input_tokens + bench.output_tokens)


async def bench_origin(
    session: Session,
    origin_sp: JobSearchPoint,
    *,
    origin_id: str,
) -> BenchPasses | None:
    stores, hop = session.store, session.hop
    if _line(session) is None:
        return None
    held = line_passes(stores, session.bound_campaign)
    if held is not None and held.origin.answers(
        origin_sp.sp_hash(session.pipeline_schema),
        [s.key for s in session.state.scoring.partition.bench],
        session.state.scoring.scorer.id,
    ):
        passes = held
    else:
        # The bench bucket's own delta: a search call landing while this pass is out is not reserve.
        before = _bench_counted(stores, hop)
        origin_pass = await score_on_bench(
            session,
            origin_sp,
            individual_id=origin_id,
            subject="origin",
            label=candidate_label(0, 0),
            round_num=0,
        )
        after = _bench_counted(stores, hop)
        passes = BenchPasses(
            tolerance=session.state.scoring.partition.split.tolerance,
            origin=origin_pass,
            reserve_usd=after.usd_amount - before.usd_amount,
            reserve_tokens=after.token_amount - before.token_amount,
            # A pass sent beside another reference pairs with nothing on this one.
            selections={},
            refused={},
        )
    graded(
        session,
        passes.origin,
        subject="origin",
        reserve=Budget.amount(passes.reserve_usd, passes.reserve_tokens),
    )
    return passes


def live_cycle_run(stores: Stores, hop: CycleHop) -> LiveCycleRun:
    # A launch stopped before run init names no cycle.
    named = bool(hop.cycle_id)
    index = stores.campaigns.load(hop, moment=None) if named else None
    standing = None if index is None else index.standing
    # A pass holds the cycle too: only the loop's hold is a run still selecting. Its own lock says
    # so, as its phase cannot: a pass with a pause asked reads PAUSED.
    run = stores.run_state(hop) if named else None
    attached = run is not None and run.producer.attached
    passing = attached and pass_held(stores.campaigns.cycle_dir(hop))
    return LiveCycleRun(
        selecting=attached and not passing,
        passing=passing,
        # The bracket, not the lock alone: `held-out` reads this under its own pass's lock.
        pass_open=attached and scan_bench_open(stores.campaigns.ledger(hop)),
        ending=None if index is None else index.stop_reason,
        selection=None if standing is None else standing.selection,
        rounds_closed=0 if standing is None else standing.rounds_closed,
    )


def line_passes(stores: Stores, campaign: Campaign) -> BenchPasses | None:
    """The line's OWN ledgers: a root cut from another tree graded its prefix in another epoch."""
    return scan_bench_passes(stores.campaigns.line_spans(campaign.root_hop))


def cycle_curve(stores: Stores, at: CampaignCycle, config: CampaignConfig) -> BenchCurve | None:
    passes = line_passes(stores, at.campaign)
    if passes is None:
        return None
    scorer = scorer_of(config, verifier_graded=False)
    spans = line_spans(stores, scorer, at.hop)
    budget = line_round_budget(spans, config.optimization.budget.rounds)
    return read_curve(stores, passes, scorer, spans, budget=budget)


def read_cycle_bench(stores: Stores, at: CampaignCycle) -> BenchScore:
    return cycle_bench(stores, at, cycle_config(stores, at).config)


def cycle_bench(stores: Stores, at: CampaignCycle, config: CampaignConfig) -> BenchScore:
    return bench_under(
        stores,
        at,
        line_passes(stores, at.campaign),
        config,
        scorer_of(config, verifier_graded=False),
        run=live_cycle_run(stores, at.hop),
    )


def bench_line(
    stores: Stores, at: CampaignCycle, config: CampaignConfig, *, run: LiveCycleRun
) -> BenchLine:
    campaign, hop = at.campaign, at.hop
    holder = stores.campaigns.live_cycle(campaign.root_hop)
    drawn = stores.campaigns.last(hop, PartitionRecord)
    return BenchLine(
        hop=hop,
        on_live_cycle=holder == hop,
        inner=stores.inner,
        refused=_refused(stores, campaign),
        held_by=None if holder == hop else holder.cycle_id,
        trigger=config.bench_trigger,
        held_out=len(drawn.bench_ids) if drawn is not None else config.dataset_split.bench,
        instrument_id=None if drawn is None else drawn.instrument_id,
        run=run,
        spend=scan_ledger_spend(stores.campaigns.line_spans(campaign.root_hop)).spend,
    )


def cycle_verify(stores: Stores, at: CampaignCycle) -> PassAdmission:
    """What `verify-candidate` on *at* is admitted under, as every door serves it."""
    return verify_admission(
        inner=stores.inner,
        refused=_refused(stores, at.campaign),
        run=live_cycle_run(stores, at.hop),
    )


def _refused(stores: Stores, campaign: Campaign) -> CampaignRefusal | None:
    """The refusal a pass on *campaign* is admitted under, as its bench line serves it."""
    refused = campaign_refusal(stores, campaign)
    return None if refused is None else refused.refusal


def bench_under(
    stores: Stores,
    at: CampaignCycle,
    passes: BenchPasses | None,
    config: CampaignConfig,
    scorer: Scorer,
    *,
    run: LiveCycleRun,
) -> BenchScore:
    line = bench_line(stores, at, config, run=run)
    return read_bench(stores, passes if line.on_live_cycle else None, scorer, line=line)


def declare_line_bench(
    session: Session,
    config: CampaignConfig,
    *,
    passes: BenchPasses | None,
    selecting: bool,
    ending: StopReason | None,
) -> BenchScore:
    stores, at = session.store, session.at
    scorer = scorer_of(config, verifier_graded=False)
    # The index says `selecting` / `ending` only once the run closes; the launch's own word wins.
    run = live_cycle_run(stores, at.hop)._replace(selecting=selecting, ending=ending)
    held = passes if passes is not None else line_passes(stores, at.campaign)
    score = bench_under(stores, at, held, config, scorer, run=run)
    append_record(BenchScoredRecord(bench=score))
    return score
