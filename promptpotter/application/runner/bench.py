"""The bench's pass: one individual sent over the held-out bench set, banked as facts, and
``read_bench``, the one reading of those facts under a named scorer. Run-level archive views skip
``MeasurementRole.BENCH``; row-level ones admit only ``admitted_ids``."""

from __future__ import annotations

from functools import partial
from statistics import fmean
from typing import TYPE_CHECKING, NamedTuple, cast

from promptpotter.application.runner.termination import RUN_STOPS, run_stop_reason
from promptpotter.application.scoring.classification import scoreable_rows
from promptpotter.application.scoring.formula import cell_channels_of, rescore_results
from promptpotter.application.scoring.metrics import fold_cells
from promptpotter.application.scoring.search_point_scorer import (
    SCORING_ERROR_ABORT,
    score_search_point,
)
from promptpotter.application.scoring.selection import matched_parent_lift, mean_fitness_ci
from promptpotter.domain.bench import (
    BENCH_HEADLINE,
    COLUMN_GRADE,
    BandedValue,
    BenchColumn,
    BenchColumns,
    BenchPass,
    BenchPasses,
    BenchReading,
    BenchScore,
    BenchSubject,
)
from promptpotter.domain.phases import STOP_REASON_INFO, CampaignPhase, StopOutcome, emit_phase
from promptpotter.domain.results import resolved_fitness
from promptpotter.infrastructure.store.archive_queries import load_run
from promptpotter.infrastructure.store.read_model import derived
from promptpotter.shared.instrument import NO_ROUND_SLOT, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.scoring import CellScorer, QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.llm.spend_book import SpendBook
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "PassReading",
    "bench_rows",
    "bench_selection",
    "grade_round_selection",
    "graded",
    "headline",
    "level_columns",
    "nothing_held_out",
    "paired_lift",
    "read_bench",
    "read_pass",
    "reserve_selection_pass",
    "score_on_bench",
    "unheld_bench",
]


class PassReading(NamedTuple):
    # `None` where the pass read nothing, and `missing` then says why.
    reading: BenchReading | None
    missing: str | None
    # The graded population the reading is over; empty where it read nothing.
    rows: list[QueryMeasurement]


def read_pass(
    stores: Stores, bench_pass: BenchPass, scorer: CellScorer, *, tolerance: int
) -> PassReading:
    """The pass's archived rows graded under *scorer*: a pass that stopped short, or ended past
    *tolerance* rows with no verdict, reads nothing rather than a number over fewer rows."""
    if bench_pass.stopped is not None:
        return PassReading(None, bench_pass.stopped, [])
    run = None if bench_pass.run_id is None else load_run(stores, bench_pass.run_id)
    if run is None:
        return PassReading(None, f"its run {bench_pass.run_id} is not in the archive", [])
    sent = set(bench_pass.sample_ids)
    rows = cast(
        "list[QueryMeasurement]",
        rescore_results([{**r} for r in run["measurements"] if r.get("sample_id") in sent], scorer),
    )
    graded_rows = scoreable_rows(rows)
    if (short := len(sent) - len(graded_rows)) > tolerance:
        missing = (
            f"{short} of {len(sent)} rows carry no verdict — a provider fault, or a cell the "
            f"formula cannot grade — past the split's tolerance of {tolerance}"
        )
        return PassReading(None, missing, [])
    level = level_columns(rows)
    reading = BenchReading(
        round=bench_pass.round,
        sp_hash=bench_pass.sp_hash,
        headline=BENCH_HEADLINE,
        n_scored=len(graded_rows),
        accuracy=level.accuracy,
        composite=level.composite,
    )
    return PassReading(reading, None, graded_rows)


def level_columns(rows: list[QueryMeasurement]) -> BenchColumns:
    """One population's level in both columns, each with its band — for the bench's pass and a
    verify's alike, so the two cannot bracket a level differently."""
    folded = fold_cells(rows)
    accuracy = folded["accuracy"]
    # The composite floors at 0.0 over no scoreable row; a column over none has no value.
    levels: dict[BenchColumn, float | None] = {
        "accuracy": accuracy,
        "composite": None
        if accuracy is None
        else resolved_fitness(folded["composite_fitness"], accuracy),
    }

    def column(name: BenchColumn) -> BandedValue | None:
        level = levels[name]
        if level is None:
            return None
        return _banded(level, *mean_fitness_ci(rows, grade=COLUMN_GRADE[name]))

    return BenchColumns(accuracy=column("accuracy"), composite=column("composite"))


def paired_lift(rows: list[QueryMeasurement], reference: list[QueryMeasurement]) -> BenchColumns:
    """*rows* over *reference* in both columns, paired per cell both scored."""

    def column(name: BenchColumn) -> BandedValue | None:
        paired = matched_parent_lift(rows, reference, grade=COLUMN_GRADE[name])
        return None if paired is None else _banded(*paired)

    return BenchColumns(accuracy=column("accuracy"), composite=column("composite"))


def _banded(value: float, ci_lo: float | None, ci_hi: float | None) -> BandedValue:
    return BandedValue(value=value, ci_lo=ci_lo, ci_hi=ci_hi)


def read_bench(
    stores: Stores, passes: BenchPasses, scorer: CellScorer, *, scorer_id: str
) -> BenchScore:
    """The headline, derived from the passes' archived facts under *scorer* — for the run that
    graded them and every later reader alike, so a copy of it is only ever a cache."""
    return _graded_bench(stores, passes, scorer, scorer_id=scorer_id)[0]


def bench_rows(
    stores: Stores, passes: BenchPasses, scorer: CellScorer, *, scorer_id: str
) -> tuple[list[QueryMeasurement] | None, list[QueryMeasurement] | None]:
    """The graded rows behind :func:`read_bench`'s two readings, origin then selected; ``None``
    for a pass that read nothing. Shared with every other reader, so read-only."""
    _, origin, selected = _graded_bench(stores, passes, scorer, scorer_id=scorer_id)
    return (
        None if origin.reading is None else origin.rows,
        None if selected.reading is None else selected.rows,
    )


def _graded_bench(
    stores: Stores, passes: BenchPasses, scorer: CellScorer, *, scorer_id: str
) -> tuple[BenchScore, PassReading, PassReading]:
    """Regraded only when a pass's archived run moves: the campaign list asks on every poll."""
    archive = stores.archive
    runs = tuple(
        None if p is None or p.run_id is None else archive.signature(p.run_id)
        for p in (passes.origin, passes.selected)
    )
    graded = derived(
        ("bench", archive.base_dir, passes.model_dump_json(), scorer_id),
        sig=runs,
        compute=lambda: _grade_bench(stores, passes, scorer, scorer_id=scorer_id),
    )
    assert graded is not None
    return graded


def _lift(origin: PassReading, selected: PassReading, *, same_pass: bool) -> BenchColumns:
    if not same_pass:
        return paired_lift(selected.rows, origin.rows)
    # One pass read twice is no comparison: 0.0 by identity, and no interval to draw.
    zero = None if origin.reading is None else _banded(0.0, None, None)
    return BenchColumns(accuracy=zero, composite=zero)


def _grade_bench(
    stores: Stores, passes: BenchPasses, scorer: CellScorer, *, scorer_id: str
) -> tuple[BenchScore, PassReading, PassReading]:
    origin = read_pass(stores, passes.origin, scorer, tolerance=passes.tolerance)
    if passes.selected is None:
        selected = PassReading(None, "not graded until the line's run ends", [])
    elif passes.selected == passes.origin:
        selected = origin
    else:
        selected = read_pass(stores, passes.selected, scorer, tolerance=passes.tolerance)
    reads = (("origin", origin), ("selected", selected))
    missing = "; ".join(f"{name}: {r.missing}" for name, r in reads if r.missing is not None)
    score = BenchScore(
        bench_size=len(passes.origin.sample_ids),
        scorer_id=scorer_id,
        headline=BENCH_HEADLINE,
        origin=origin.reading,
        selected=selected.reading,
        missing_reason=missing or None,
        lift=_lift(origin, selected, same_pass=passes.selected == passes.origin),
    )
    return score, origin, selected


async def score_on_bench(
    session: Session,
    search_point: JobSearchPoint,
    *,
    subject: BenchSubject,
    label: str,
    round_num: int,
    cb: RunCallbacks,
) -> BenchPass:
    sp_hash = search_point.sp_hash(session.pipeline_schema)
    bench = list(session.scoring.require_partition().bench)
    # Bracketed without a round: the pass scores after the loop, and a round here would move the
    # dashboard's round back to the one being graded. That round rides the view instead.
    emit_phase(
        cb.on_phase,
        CampaignPhase.BENCH,
        "enter",
        subject=subject,
        label=label,
        sp_hash=sp_hash,
        graded_round=round_num,
        rows=len(bench),
    )
    try:
        scored = await score_search_point(
            search_point,
            bench,
            session,
            label=MeasurementRole.BENCH,
            measured=None,
            on_sample_scored=partial(cb.on_sample_scored, NO_ROUND_SLOT, 0),
            on_sample_starting=partial(cb.on_sample_started, NO_ROUND_SLOT, 0),
        )
    finally:
        emit_phase(cb.on_phase, CampaignPhase.BENCH, "exit")
    stopped: str | None = None
    if scored.stopped is not None:
        signal = scored.signal
        cause = (
            f": {signal.check_result['last_error']}"
            if signal is not None and signal.check_name == SCORING_ERROR_ABORT
            else ""
        )
        stopped = f"{scored.stopped} after {len(scored.results)} of {len(bench)} rows{cause}"
    return BenchPass(
        round=round_num,
        sp_hash=sp_hash,
        run_id=scored.run_id,
        sample_ids=[s.id for s in bench],
        stopped=stopped,
        scorer_id=session.scoring.scorer_id,
    )


def unheld_bench(scorer_id: str) -> BenchScore:
    """The headline of a split holding no bench row: it never grades, so this is final at start."""
    return BenchScore(
        bench_size=0,
        scorer_id=scorer_id,
        headline=BENCH_HEADLINE,
        origin=None,
        selected=None,
        missing_reason="nothing held out: the campaign's dataset_split declares no bench rows",
        lift=BenchColumns(accuracy=None, composite=None),
    )


def nothing_held_out(cb: RunCallbacks, *, scorer_id: str) -> BenchScore:
    score = unheld_bench(scorer_id)
    emit_phase(cb.on_phase, CampaignPhase.BENCH, "scored", bench=score)
    return score


def _tolerance(session: Session) -> int:
    split = session.scoring.require_partition().split
    return split.tolerance if split is not None else 0


def graded(cb: RunCallbacks, session: Session, bench_pass: BenchPass, *, label: str) -> None:
    """One pass on the ledger, as a reading of the individual *label* names."""
    read = read_pass(
        session.store,
        bench_pass,
        session.scoring.require_scorer(),
        tolerance=_tolerance(session),
    )
    emit_phase(
        cb.on_phase,
        CampaignPhase.BENCH,
        "graded",
        reading=read.reading,
        missing=read.missing,
        label=label,
    )


async def grade_round_selection(
    cycle: Cycle, session: Session, round_result: RoundResult, *, cb: RunCallbacks
) -> None:
    """Under ``bench_each_round``, the round's declared selection graded on the bench set. A held
    round declares nothing new, and a campaign holding no bench row has nothing to grade on."""
    if not (
        cycle.config.bench_each_round
        and round_result.selected_labels
        and session.scoring.require_partition().bench
    ):
        return
    label = round_result.selected_labels[0]
    graded(
        cb,
        session,
        await score_on_bench(
            session,
            cycle.selected_sp,
            subject="selected",
            label=label,
            round_num=round_result.round,
            cb=cb,
        ),
        label=label,
    )


def reserve_selection_pass(cycle: Cycle, session: Session, book: SpendBook) -> None:
    """Restate what *book* sets aside for the bench at the SELECTION's price. It opens at the
    origin's, and an individual that makes the solver write more ends its pass short under that.
    Nothing set aside means nothing to restate: no line, or a ceiling the bench is metered beside."""
    if not book.set_aside_usd:
        return
    costs = [
        cost
        for row in cycle.selection.results
        if (cost := cell_channels_of(row).get("cost")) is not None
    ]
    if not costs:
        return
    # One cell on top of the mean: the last row is admitted at the dearest bill, never the average.
    usd = len(session.scoring.require_partition().bench) * fmean(costs) + max(costs)
    if usd > book.set_aside_usd:
        book.set_aside(usd, round(book.set_aside_tokens * usd / book.set_aside_usd))


async def bench_selection(
    cycle: Cycle, session: Session, *, banked: BenchPasses, cb: RunCallbacks
) -> BenchPasses:
    """The selection is the pick the optimizer declared (``Cycle.selection``), sent here over rows
    it never read, beside the origin's pass *banked* holds. Sent once where it is the origin
    itself. Only a pause escapes the pass: any other stop ends it short, and the headline says so."""
    origin = banked.origin
    picked, selected_sp = cycle.selection, cycle.selected_sp
    selected_hash = selected_sp.sp_hash(session.pipeline_schema)
    if not any(rr.round > 0 for rr in cycle.rounds):
        selected = BenchPass(
            round=picked.round,
            sp_hash=selected_hash,
            run_id=None,
            sample_ids=origin.sample_ids,
            stopped="no round closed, so the optimizer selected nothing",
            scorer_id=session.scoring.scorer_id,
        )
    elif selected_hash == origin.sp_hash:
        selected = origin
    else:
        try:
            selected = await score_on_bench(
                session,
                selected_sp,
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
                sp_hash=selected_hash,
                run_id=None,
                sample_ids=origin.sample_ids,
                stopped=reason.value,
                scorer_id=session.scoring.scorer_id,
            )
    return banked.model_copy(update={"selected": selected})


def headline(cb: RunCallbacks, session: Session, passes: BenchPasses) -> BenchScore:
    """The passes read under the run's own scorer, on the ledger as the dashboard folds it."""
    score = read_bench(
        session.store,
        passes,
        session.scoring.require_scorer(),
        scorer_id=session.scoring.scorer_id,
    )
    emit_phase(cb.on_phase, CampaignPhase.BENCH, "scored", bench=score)
    return score
