"""The scoring walk — one search point's pass over its panel — and :func:`run_walks`, the one loop
that drives every walk of a scoring phase: prior-cache reuse, stale-data recovery, error
classification into an abort reason, look-ahead within the armed depth, and decisions in walk
order. The gateway turns a decided walk into the archived run."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast

from promptpotter.application.run_phase_control import declare_run_phase, pause_requested
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.sample_measurement import (
    STALE_DATA_LOAD_PROTOCOL,
    cell_bound,
    emit_replayed_step_tokens,
    measure_sample,
)
from promptpotter.application.scoring.sample_measurement import (
    execute_stale_data_protocol as _execute_stale_data_protocol,
)
from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.escalation_signals import EscalationSignal
from promptpotter.domain.phases import (
    REFUSAL_STOPS,
    STOP_REASON_INFO,
    RunPhase,
    StopLoop,
    StopReason,
)
from promptpotter.domain.scoring import CellScorer, QueryMeasurement, is_hit
from promptpotter.domain.spend import StepTokenUsage
from promptpotter.domain.validators import StopRule
from promptpotter.infrastructure.llm.spend_book import SendBound, bound_spend_book
from promptpotter.infrastructure.runtime_flags import effective_lookahead
from promptpotter.shared.errors import (
    ErrorCategory,
    SendRefusedError,
    error_category,
    graceful,
    is_error_result,
)
from promptpotter.shared.errors import (
    has_pipeline_warnings as _has_pipeline_warnings,
)

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.axis import AxisIndex
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint

logger = logging.getLogger(__name__)

__all__ = ["CatchUps", "Flight", "FlightGauge", "QueryLoopResult", "Walk", "run_walks"]


# The stops that still wait out the calls already sent — see :func:`run_walks`.
_BUDGET_STOPS = frozenset({StopReason.SPEND_BUDGET, StopReason.TOKEN_BUDGET})
_CEILINGS = frozenset(category for category, stop in REFUSAL_STOPS.items() if stop in _BUDGET_STOPS)


def _dearest(bounds: Sequence[SendBound | None]) -> SendBound | None:
    """The bound every cell of a phase is counted at: the largest any of its walks may send, and
    unbounded where any is. ``None`` where no cell is held whole (``Connector.holds_own_sends``)."""
    held = [b for b in bounds if b is not None]
    if not held:
        return None
    outputs = [b.output_tokens for b in held]
    costs = [b.usd for b in held]
    return SendBound(
        input_tokens=max(b.input_tokens for b in held),
        output_tokens=None if None in outputs else max(o for o in outputs if o is not None),
        usd=None if None in costs else max(c for c in costs if c is not None),
    )


MAX_CONSECUTIVE_ERRORS: int = 3
"""Abort a walk after this many consecutive client/pipeline errors — a
runaway backend shouldn't burn the round's compute budget."""

# The cell categories whose cause every later cell shares, so one halts the walk.
_WALK_STOPS: dict[ErrorCategory, StopReason] = {
    ErrorCategory.CONNECTION: StopReason.BACKEND_UNREACHABLE,
    **REFUSAL_STOPS,
}

# How many calls a round may hold in flight is the BACKEND's to declare
# (`Connector.max_cells_in_flight`), not a constant here. It was a fixed 2 while the depth was
# pinned off `execution` — a transport fact answering a cost question, wrongly.


class CatchUps(Protocol):
    """The PoBB catch-up calls a round pairs its cells with — `pobb/checks.py::PoBBCheck`, seen from
    a layer that may not import it. Started in free slots, committed as each cell is taken."""

    def start_backfill(self, sample: Sample, room: int) -> list[asyncio.Future[Any]]: ...

    def owed_backfills(self, sample: Sample) -> int: ...

    def backfills_in_flight(self) -> list[asyncio.Future[Any]]: ...

    def backfills_for(self, sample: Sample) -> list[asyncio.Future[Any]]: ...

    def commit_backfills(self, sample: Sample) -> None: ...

    def bank_backfills(self, samples: Sequence[Sample]) -> None: ...

    def discard_backfills(self) -> None: ...


@dataclass(frozen=True)
class Flight:
    """Calls out, calls the stop rules allow out now, and the most that could ever be out.
    ``waiting`` is the call a DECISION is held on — ``(sample_id, launched_at)`` — because in-order
    absorption lets one slow call stall a round whose other calls are all back. ``backpressure`` is
    the provider holding calls that are out but not yet sent."""

    out: int = 0
    allowed: int = 0
    most: int = 0
    waiting: tuple[int, float] | None = None
    backpressure: BackpressureReading | None = None
    # How many MORE cells the spend ceiling admits beside those out, and what one cell reserves.
    # ``None`` where no book bounds the cells. The depth can be armed, the stop rules can allow a
    # dozen, and this can still be 0: a cell reserves its WORST case, so a ceiling only a few of
    # those wide holds the walk at one call with every other reading saying otherwise — which is
    # what it did, unreported, for a whole campaign.
    affordable: int | None = None
    cell_usd: float | None = None


class FlightGauge:
    """What the scoring phase has out, what its stop rules allow out right now, and the most it could
    ever hold, published at most once per ``every`` seconds: the reading is asked for only then,
    because a full horizon is a bounds sweep, too dear to take at every launch. One phase publishes
    at a time — a second opening is a scheduler inside a scheduler, which nothing may build."""

    def __init__(self, emit: Callable[[Flight], None], *, every: float = 0.5) -> None:
        self._emit = emit
        self._every = every
        self._reader: Callable[[], Flight] | None = None
        self._published = Flight()
        self._due: asyncio.TimerHandle | None = None

    def open(self, reader: Callable[[], Flight]) -> None:
        if self._reader is not None:
            raise RuntimeError("a scoring phase is already publishing on this gauge")
        self._reader = reader
        self.touch()

    def close(self) -> None:
        self._reader = None
        self._publish()

    def touch(self) -> None:
        if self._due is None and self._reader is not None:
            self._due = asyncio.get_running_loop().call_later(self._every, self._publish)

    def _publish(self) -> None:
        if self._due is not None:
            self._due.cancel()
            self._due = None
        reading = self._reader() if self._reader is not None else Flight()
        if reading != self._published:
            self._published = reading
            self._emit(reading)


@dataclass
class QueryLoopResult:
    results: list[QueryMeasurement]
    completed: bool = True
    # "skip" (operator early-abort: accept partial, cycle continues) | "escalation" | abort reason.
    # A pause or a budget stop is raised, never returned.
    stop_reason: str | None = None
    escalation_signal: EscalationSignal | None = None


_BOLD_MARKER_RE = re.compile(r"\*\*[^*]+\*\*")


def _with_running(
    result: QueryMeasurement, running: dict[str, Any], run_id: str
) -> QueryMeasurement:
    """A shallow copy carrying the candidate's running fitness and the archive run the row lands in
    — transient projection hints for the live surfaces. The persisted results keep the clean result,
    not this copy: an archived row is already filed under its run."""
    out = dict(result)
    out["_running"] = running
    out["run_id"] = run_id
    return cast(QueryMeasurement, out)


def _materialize_cached(item: QueryMeasurement, scorer: CellScorer) -> QueryMeasurement:
    """Mark prior as cached + rescored; warn on hit/no-hit drift unless explained by bold-strip."""
    # Deliberately BINARY, on the archived vs rescored verdict rather than the graded fitness:
    # a float comparison fires per sample on every formula tweak, and this warning is calibrated
    # for one known-benign cause. ``fitness is None`` is never-scored, distinct from a 0.0.
    archived_fitness = item.get("fitness")
    r: dict[str, Any] = {**item, "cached": True}
    pd = r.get("pipeline_data")
    if isinstance(pd, dict):
        r["pipeline_data"] = {**pd, "total_time": 0.0}
    rescore_results([r], scorer)
    archived_hit = is_hit(archived_fitness)
    rescored_hit = is_hit(r.get("fitness"))
    if archived_fitness is not None and archived_hit != rescored_hit:
        predicted = r.get("predicted") or ""
        if not _BOLD_MARKER_RE.search(predicted):
            logger.warning(
                "Cache rescore drift on %r: archived hit=%s → rescored hit=%s. "
                "Policy divergence — not explained by bold-wrapper strip.",
                (r.get("query") or "")[:60],
                archived_hit,
                rescored_hit,
            )
    return cast(QueryMeasurement, r)


def _emit_cached_step_tokens(row: QueryMeasurement) -> None:
    """Meter a measurement-cache hit off the tokens the archived row already carries. Replaying them costs nothing, but the
    search still made the call, so the ledger has to say so."""

    pd = row.get("pipeline_data")
    if not isinstance(pd, dict):
        return
    step_tokens = pd.get("step_tokens")
    if not isinstance(step_tokens, dict) or not step_tokens:
        return
    step_timings = pd.get("step_timings")
    emit_replayed_step_tokens(
        cast("Mapping[str, StepTokenUsage]", step_tokens),
        step_timings if isinstance(step_timings, dict) else {},
    )


@dataclass
class QueryLoopState:
    """Read-only context threaded through per-sample processing."""

    search_point: JobSearchPoint
    session: Session
    # The archive run this walk's rows land in — half of every cell's address ``(run_id,
    # sample_id)``, stamped onto the live copy so a surface can open the cell before the round closes.
    run_id: str
    cached_sample_results: dict[int, QueryMeasurement]
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None
    axes: AxisIndex | None
    scorer: CellScorer  # narrowed from session.scoring.scorer (asserted non-None on construction)
    # The cached entry itself, so display can show the original DEPR row before the retry row.
    deprecated_samples: dict[int, QueryMeasurement]
    # Persists results-so-far after each fresh measurement and returns the running fitness over
    # them. The promise: a sample is on disk iff it was TAKEN — a discarded look-ahead acquisition
    # is paid for and deliberately not here.
    persist_fresh: Callable[[list[QueryMeasurement]], dict[str, Any]]
    # Ridden out on the sample snapshot so the live surfaces show a candidate fitness that moves
    # in real time instead of sitting at 0. A fresh sample reads it off ``persist_fresh``'s
    # return rather than folding twice.
    running_scores: Callable[[list[QueryMeasurement]], dict[str, Any]]
    # The run's closing write, given the walk's rows and its scores once it is decided.
    record_run: Callable[[list[QueryMeasurement], dict[str, Any]], None]
    # Keeps rows the walk has back and has not taken, beside the rows it has, for a resumption.
    bank: Callable[[list[QueryMeasurement], list[QueryMeasurement]], None]


def _armed_cells(session: Session) -> int:
    """What the operator ASKED for, clamped to what the backend declares it can hold — the request
    alone would let the browser outrun the box, the ceiling alone would widen every walk unbidden.

    **Sample look-ahead is LIVE, and every part of it looks removable.** It defaults off and no
    committed campaign enables it, so a reader concludes the branch never fires; it fires on every
    dataset the moment the operator presses the control — ``promptpotter-self`` included, where one
    press releases a GROUP of inner campaigns. Four pieces move together or not at all: the
    ``.runtime/sample_lookahead.json`` write/poll/consume triple, the launch/take split below,
    ``dashboard.json::sample_lookahead`` + ``sample_lookahead_discards`` with the two connector
    declarations beside them, and the ``campaign.lookahead`` cap. **Never "recover" an acquisition
    past a cut** — recording it makes the run's rows depend on in-flight depth, forcing a
    ``human_intervened`` stamp and devaluing the campaign; that discard is the design. A STOP is
    not a cut: it banks what each walk was sure to take (:meth:`Walk.bank`). Why it is
    browser-only with no CLI verb: ``docs/operations/access-model.md`` § host-admin ↔ user."""
    check = session.sample_lookahead_check
    requested = check() if check is not None else 1
    return effective_lookahead(requested, session.backend_client.max_cells_in_flight)


async def _maybe_recover_degraded(
    result: QueryMeasurement,
    sample: Sample,
    ctx: QueryLoopState,
) -> QueryMeasurement:

    if not _has_pipeline_warnings(result):
        return result
    recovered, _step = await _execute_stale_data_protocol(
        list(STALE_DATA_LOAD_PROTOCOL),
        sample,
        cast(dict[str, Any], result),
        ctx.session,
        pipeline_params=ctx.search_point.pipeline_params,
        axes=ctx.axes,
    )
    return cast(QueryMeasurement, recovered)


@dataclass
class _Acquired:
    """Carries no side effect a reader could order against: every append, persist, callback and
    ledger write belongs to :meth:`Walk.take`."""

    sample: Sample
    idx: int
    result: QueryMeasurement
    fresh: bool  # False ⇒ replayed from the prior cache (no ``persist_fresh``)
    deprecated_display: QueryMeasurement | None = None


def _returned_row(cell: asyncio.Task[_Acquired]) -> QueryMeasurement | None:
    if not cell.done() or cell.cancelled() or cell.exception() is not None:
        return None
    return cell.result().result


def _quiet(call: asyncio.Future[Any]) -> None:
    # Retrieved, so a discarded call's own failure is not reported as unhandled.
    if not call.cancelled():
        call.exception()


async def _acquire(sample: Sample, idx: int, ctx: QueryLoopState) -> _Acquired:
    cached = ctx.cached_sample_results.get(sample.id)
    if cached is not None:
        cached_r = _materialize_cached(cached, ctx.scorer)
        # Can re-measure for real, so a hit gets a slot like anything else.
        cached_r = await _maybe_recover_degraded(cached_r, sample, ctx)
        return _Acquired(sample=sample, idx=idx, result=cached_r, fresh=False)

    deprecated_display: QueryMeasurement | None = None
    if (cached_deprecated := ctx.deprecated_samples.get(sample.id)) is not None:
        # Rescored here, rendered at the take: a display call from a cell prints out of walk order.
        deprecated_display = _materialize_cached(cached_deprecated, ctx.scorer)

    result = await measure_sample(
        sample,
        ctx.session,
        pipeline_params=ctx.search_point.pipeline_params,
    )
    result = await _maybe_recover_degraded(result, sample, ctx)
    if sample.id in ctx.deprecated_samples:
        cast(dict[str, Any], result)["retry_of_deprecated_cache"] = True
    return _Acquired(
        sample=sample,
        idx=idx,
        result=result,
        fresh=True,
        deprecated_display=deprecated_display,
    )


LaunchEvent = tuple[str, int, int, int, int, int | None]


@dataclass
class Walk:
    """One search point's pass over its panel, in the order GIVEN — ``score_population`` reorders
    the round's panel once, so walking it as-given IS the round order. Passive: :func:`run_walks`
    launches its cells, takes them in walk order and decides it, so every walk of a phase answers
    to one loop and no walk reads another's state across an await.

    ``submitted`` is how far LAUNCHING reached and ``len(results)`` how far TAKING did; look-ahead
    is the gap between them. A cell frees its slot the moment it RETURNS, not when its turn to be
    taken comes, so a slow cell at the head holds one slot rather than the whole window."""

    dataset: list[Sample]
    ctx: QueryLoopState
    checks: Sequence[StopRule]
    on_sample_starting: Callable[[str, int, int, int, int, int | None], None] | None
    # The walk's own context — the candidate it measures — copied once per cell, because a cell's
    # throttle give-back and envelope deadline bind to the task it runs in.
    context: contextvars.Context
    results: list[QueryMeasurement] = field(default_factory=list)
    consecutive_errors: int = 0
    submitted: int = 0
    running: dict[int, asyncio.Task[_Acquired]] = field(default_factory=dict)
    finished: dict[int, asyncio.Task[_Acquired]] = field(default_factory=dict)
    launched_at: dict[int, float] = field(default_factory=dict)
    # Launch events of a walk measuring ahead of its turn, released at it, so each candidate's
    # events still open with its announcement. ``None`` once released.
    held: list[LaunchEvent] | None = field(default_factory=list)
    # The taken cell whose catch-ups the walk still waits on before it can be judged.
    settling: Sample | None = None
    outcome: QueryLoopResult | None = None
    # How many rows an operator's skip already let this walk take before a stop interrupted the
    # phase — replayed on resumption, so the skip outlives the pause that followed it.
    skip_at: int | None = None

    @property
    def n(self) -> int:
        return len(self.dataset)

    def launch(self, armed: int, horizon: int | None) -> tuple[Sample, asyncio.Task[_Acquired]]:
        idx = self.submitted
        sample = self.dataset[idx]
        # The depth the round is RUNNING at, not how many happen to be in flight this instant: a
        # window is empty at the start of every candidate, so reporting the latter unlights the
        # operator's armed indicator once per candidate.
        event: LaunchEvent = (sample.query, idx, self.n, sample.id, armed, horizon)
        if self.held is not None:
            self.held.append(event)
        elif self.on_sample_starting is not None:
            self.on_sample_starting(*event)
        cell = asyncio.create_task(
            _acquire(sample, idx, self.ctx),
            name=f"scoring:{sample.id}",
            context=self.context.copy(),
        )
        self.running[idx] = cell
        self.launched_at[idx] = time.time()
        self.submitted += 1
        return sample, cell

    def release(self) -> None:
        held, self.held = self.held or [], None
        if self.on_sample_starting is not None:
            for event in held:
                self.on_sample_starting(*event)

    def collect(self) -> None:
        for idx in [idx for idx, cell in self.running.items() if cell.done()]:
            self.finished[idx] = self.running.pop(idx)

    def head(self) -> asyncio.Task[_Acquired] | None:
        """The returned cell next in walk order — only it may be taken."""
        return None if self.settling is not None else self.finished.get(len(self.results))

    def take(self, cell: asyncio.Task[_Acquired]) -> QueryLoopResult | None:
        """The only writer of the rows, persister of one, and the fault verdict: an abort, ``None``
        to go on. Raises the cell's own error in its turn, never in its race."""
        acq = cell.result()
        del self.finished[acq.idx]
        ctx, n = self.ctx, self.n
        if not acq.fresh:
            # Only if it STAYED a replay: the stale-data protocol may have re-measured for real,
            # and that path already emitted its own fresh records.
            if acq.result.get("cached"):
                _emit_cached_step_tokens(acq.result)
        elif acq.deprecated_display is not None and ctx.on_sample_scored is not None:
            ctx.on_sample_scored(acq.deprecated_display, acq.idx, n)

        self.results.append(acq.result)
        running = ctx.persist_fresh(self.results) if acq.fresh else ctx.running_scores(self.results)

        # Asked of every row, not only fresh ones: a replay never carries an error, but the
        # stale-data protocol re-measures a replayed cell. The backend already spent its own
        # bounded retries, so the next cell meets the same outage — halt, and the unreached cell
        # stays a hole for `resume`.
        category = error_category(acq.result)
        if category is not None and (stop := _WALK_STOPS.get(category)) is not None:
            logger.warning("%s: %s", STOP_REASON_INFO[stop].label, acq.result.get("error"))
            raise StopLoop(stop, unmeasured=n - len(self.results) + 1)

        if acq.fresh:
            if is_error_result(acq.result):
                if reason := self._abort_reason(acq.result):
                    # The failed sample is already in the rows, so the remainder is the untouched
                    # tail — COUNTED, never written as rows. Stamping a cell nothing ever sent with
                    # `predicted: "ERROR"` gave absence the shape of failure, and every reader
                    # downstream then drew conclusions about the pipeline from cells that never
                    # ran, while the honest verdicts (`origin_unmeasured` / `origin_incomplete`)
                    # went unreachable because the padding kept the denominator full.
                    logger.warning(
                        "Aborting scoring: %s after query %d/%d. %d cells not attempted.",
                        reason,
                        len(self.results),
                        n,
                        n - len(self.results),
                    )
                    return QueryLoopResult(self.results, completed=False, stop_reason=reason)
            else:
                self.consecutive_errors = 0

        if ctx.on_sample_scored is not None:
            ctx.on_sample_scored(_with_running(acq.result, running, ctx.run_id), acq.idx, n)
        return None

    def _abort_reason(self, result: QueryMeasurement) -> str:
        cat = error_category(result)
        if cat in {ErrorCategory.CLIENT, ErrorCategory.PIPELINE}:
            return f"skipped_after_{cat}_error"
        self.consecutive_errors += 1
        if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            return "skipped_after_consecutive_errors"
        return ""

    def judge(self) -> QueryLoopResult | None:
        """The stop rules over the rows taken — cached rows too, or a candidate whose priors already
        dominate it runs one extra real query — else complete once every cell is taken."""
        for check in self.checks:
            if (signal := check.check(self.results)) is not None:
                return QueryLoopResult(
                    self.results,
                    completed=False,
                    stop_reason="escalation",
                    escalation_signal=signal,
                )
        return QueryLoopResult(self.results) if len(self.results) == self.n else None

    def horizon(self, last: int) -> int | None:
        """The fewest taken rows at which a stop rule could cut, looking no further than walk index
        ``last``. Launching index ``i`` needs no possible cut before ``i`` rows, so a cut wastes at
        most the one cell past it — the fault aborts aside, which no rule can foresee."""
        head = len(self.results)
        if last < head:
            return None
        upcoming = [
            (self.dataset[i], _returned_row(cell) if (cell := self.finished.get(i)) else None)
            for i in range(head, last + 1)
        ]
        stops = (check.earliest_stop(self.results, upcoming) for check in self.checks)
        return min((m for m in stops if m is not None), default=None)

    def rows(self) -> list[QueryMeasurement]:
        """Every row the walk has, taken or only returned."""
        returned = (_returned_row(cell) for cell in self.finished.values())
        return [*self.results, *(row for row in returned if row is not None)]

    def flight(self) -> Flight:
        """Out now; what the stop rules let this walk hold, counted over the whole remaining panel;
        and what it could hold were nothing ever cut."""
        settled = len(self.results) + len(self.finished)
        horizon = self.horizon(self.n - 1)
        limit = self.n if horizon is None else min(self.n, horizon + 1)
        return Flight(len(self.running), limit - settled, self.n - settled)

    def bank(self) -> list[Sample]:
        """Keep the cells back and not taken that this walk was sure to take — those below its
        horizon, up to the first error, which may abort it — and name them. A walk resumed after a
        stop replays each when it reaches it, so the rows it takes are still the serial walk's. A
        decided walk banks nothing: it will never take what lies past its cut."""
        self.collect()
        horizon = self.horizon(self.n - 1)
        rows: list[QueryMeasurement] = []
        samples: list[Sample] = []
        for idx in sorted(self.finished):
            if horizon is not None and idx >= horizon:
                break
            cell = self.finished[idx]
            if cell.cancelled() or cell.exception() is not None:
                continue
            acq = cell.result()
            if is_error_result(acq.result):
                break
            if acq.fresh:
                rows.append(acq.result)
            samples.append(acq.sample)
        if rows:
            self.ctx.bank(self.results, rows)
        return samples

    def end(
        self, outcome: QueryLoopResult | None, *, cancel: bool
    ) -> list[asyncio.Task[_Acquired]]:
        """Decide the walk and hand back its calls still running. A cell in flight or returned and
        not taken is DISCARDED, never written: recording it makes the run's rows depend on the
        in-flight depth, which forces a `human_intervened` stamp. ``cancel`` stops the running ones,
        which saves anything only where it stops their work — see :func:`run_walks`."""
        self.outcome = outcome
        if self.running or self.finished:
            cause = "the phase ended" if outcome is None else (outcome.stop_reason or "complete")
            if outcome is not None and outcome.escalation_signal is not None:
                cause = f"{cause}: {outcome.escalation_signal.check_name}"
            logger.info(
                "Discarding %d look-ahead acquisition(s) after query %d/%d (%s).",
                len(self.running) + len(self.finished),
                len(self.results),
                self.n,
                cause,
            )
        for cell in self.finished.values():
            _quiet(cell)
        draining = list(self.running.values())
        for cell in draining:
            if cancel:
                cell.cancel()
            cell.add_done_callback(_quiet)
        self.running, self.finished = {}, {}
        return draining


async def run_walks(
    walks: Sequence[Walk | None],
    session: Session,
    *,
    backfills: CatchUps | None = None,
    on_turn: Callable[[int], None] | None = None,
    on_decided: Callable[[int], bool] | None = None,
) -> None:
    """Drive a scoring phase: every walk measures at once, and they are taken and decided one at a
    time, in order, so every row, cut, prior and event lands where a serial phase lands it.

    Only the walk whose TURN it is takes cells, answers a skip and is decided. ``on_turn(i)`` opens
    its turn before its held launches are released; ``on_decided(i)`` runs once it is decided, before
    the next turn opens, and returning ``True`` ends the phase. A ``None`` walk has nothing to measure
    and is decided on its turn.

    **The depth bounds every call the phase has out** — its cells, the PoBB catch-ups that pair
    them, and discarded calls still winding down — which a whole inner campaign per call makes a
    memory bound. Slots go in one order: the catch-ups the walk on turn waits on, then its own
    cells, then the walks ahead in index order, which leave one slot free; and no call starts that
    the spend book cannot hold beside every call out (:func:`_dearest`). A pause or a budget stop is
    raised; a pause that already cancelled a call is the same pause.

    **A sent call is cancelled only where that stops what it bills**
    (``Connector.cancel_stops_billing``). Elsewhere the backend finishes it and the provider bills
    it whether or not anyone waits, so it is left to land, counted against the depth, before the
    phase ends — or stops, where what it returns is banked."""
    gauge = session.flight
    draining: set[asyncio.Future[Any]] = set()
    turn = -1
    cancels = session.backend_client.cancel_stops_billing
    book = bound_spend_book()
    cell = _dearest(
        [
            await cell_bound(session, walk.ctx.search_point.pipeline_params or {})
            for walk in walks
            if walk is not None
        ]
    )

    def affordable() -> int:
        # Every call out is counted at the dearest cell, here rather than read back off the book: a
        # cell launched this step has not placed its own hold yet.
        if book is None or cell is None:
            return sys.maxsize
        return book.fits(cell, beside="backend") - out()

    def live() -> list[Walk]:
        return [walk for walk in walks[max(turn, 0) :] if walk is not None and walk.outcome is None]

    def out() -> int:
        cells = sum(len(walk.running) for walk in live())
        catching = len(backfills.backfills_in_flight()) if backfills is not None else 0
        return cells + len(draining) + catching

    def advance() -> bool:
        nonlocal turn
        while True:
            turn += 1
            if turn >= len(walks):
                return False
            if on_turn is not None:
                on_turn(turn)
            walk = walks[turn]
            if walk is not None:
                walk.release()
                if walk.dataset:
                    return True
                walk.end(QueryLoopResult([]), cancel=cancels)
            if on_decided is not None and on_decided(turn):
                return False

    def decide(walk: Walk, verdict: QueryLoopResult) -> bool:
        draining.update(walk.end(verdict, cancel=cancels))
        if on_decided is not None and on_decided(turn):
            return False
        return advance()

    def fill(walk: Walk, cap: int, armed: int) -> None:
        room = min(cap - out(), affordable())
        last = min(walk.n if walk.skip_at is None else walk.skip_at, walk.submitted + room) - 1
        if last < walk.submitted:
            return
        horizon = walk.horizon(last - 2)
        while (
            room > 0 and walk.submitted <= last and (horizon is None or walk.submitted <= horizon)
        ):
            sample, _cell = walk.launch(armed, horizon)
            room -= 1
            if backfills is not None:
                room -= len(backfills.start_backfill(sample, room))

    def reading() -> Flight:
        walking = live()
        parts = [walk.flight() for walk in walking]
        winding = sum(1 for call in draining if not call.done())
        out_now = sum(p.out for p in parts) + winding
        allowed = sum(p.allowed for p in parts) + winding
        most = sum(p.most for p in parts) + winding
        if backfills is not None:
            catching = len(backfills.backfills_in_flight())
            samples = {s.id: s for walk in walking for s in walk.dataset}
            launched = {s.id for walk in walking for s in walk.dataset[: walk.submitted]}
            owed = {sid: backfills.owed_backfills(s) for sid, s in samples.items()}
            out_now += catching
            allowed += catching + sum(k for sid, k in owed.items() if sid in launched)
            most += catching + sum(owed.values())
        waiting = None
        if 0 <= turn < len(walks) and (holder := walks[turn]) is not None:
            if holder.settling is not None:
                taken = len(holder.results) - 1
                waiting = (holder.settling.id, holder.launched_at[taken])
            elif (head := len(holder.results)) in holder.running:
                waiting = (holder.dataset[head].id, holder.launched_at[head])
        return Flight(
            out_now,
            allowed,
            most,
            waiting,
            session.backend_client.backpressure.reading(),
            affordable=None if book is None or cell is None else max(0, affordable()),
            cell_usd=None if cell is None else cell.usd,
        )

    def outstanding() -> set[asyncio.Future[Any]]:
        calls = {call for call in draining if not call.done()}
        for walking in live():
            calls.update(walking.running.values())
        if backfills is not None:
            calls.update(backfills.backfills_in_flight())
        return calls

    async def land() -> None:
        """Wait out every call already sent, starting none, so each one's cost is on the record."""
        if calls := outstanding():
            if gauge is not None:
                gauge.touch()
            await asyncio.wait(calls)
        for walking in live():
            walking.collect()
        draining.clear()

    if gauge is not None:
        gauge.open(reading)
    try:
        walking_on = advance()
        while walking_on:
            walk = walks[turn]
            assert walk is not None
            if gauge is not None:
                gauge.touch()
            armed = _armed_cells(session)
            if walk.skip_at is not None and len(walk.results) >= walk.skip_at:
                logger.info("Replaying an operator skip after query %d/%d.", walk.skip_at, walk.n)
                skipped = QueryLoopResult(walk.results, completed=False, stop_reason="skip")
                if not decide(walk, skipped):
                    break
                continue
            head = walk.head()
            settle = walk.settling
            started: list[asyncio.Future[Any]] = []
            unstarted = 0
            if settle is not None and backfills is not None:
                started = backfills.backfills_for(settle)
                unstarted = backfills.owed_backfills(settle)
            settled = settle is not None and unstarted == 0 and all(c.done() for c in started)
            cut = (head is not None and head.cancelled()) or any(c.cancelled() for c in started)
            # Answered only by the walk whose turn it is — the skip names the candidate on screen —
            # so nothing measuring for it can spend the press. A cancelled call is the pause's own
            # doing: the throttle wait polls the same flag.
            skip = bool(session.skip_check and session.skip_check())
            pause = cut or pause_requested(session)
            # Same cadence as the pause, because the round-boundary gate cannot fire until the round
            # closes — and for an L4 outer round every sample is an entire inner CAMPAIGN.
            tripped = session.budget_tripped() if session.budget_tripped is not None else None
            stopping = skip or pause or tripped is not None
            # A returned cell, or a catch-up already started, is paid for — kept before a stop is
            # honoured, so the stop lands a row later, as if pressed a moment later. Keeping starts
            # nothing: while a stop waits, only what is already out is waited on.
            keeping = (settle is not None and unstarted == 0) or head is not None
            if stopping and (cut or not keeping):
                if skip:
                    if session.skip_consume:
                        session.skip_consume()
                    logger.info(
                        "Operator skip after query %d/%d; accepting partial searchpoint.",
                        len(walk.results),
                        walk.n,
                    )
                    skipped = QueryLoopResult(walk.results, completed=False, stop_reason="skip")
                    if not decide(walk, skipped):
                        break
                    continue
                if pause or tripped is None:
                    # Between samples, where every TAKEN result is already on disk, so this exits
                    # cleanly and `resume` continues into the remaining samples.
                    logger.debug("Pause after query %d/%d.", len(walk.results), walk.n)
                    declare_run_phase(session, RunPhase.PAUSED)
                    raise KeyboardInterrupt("graceful")
                logger.warning(
                    "Budget ceiling reached after query %d/%d (%s); halting mid-round.",
                    len(walk.results),
                    walk.n,
                    tripped.value,
                )
                raise StopLoop(tripped)
            if settled and settle is not None and backfills is not None:
                walk.settling = None
                backfills.commit_backfills(settle)
                if (verdict := walk.judge()) is not None and not decide(walk, verdict):
                    break
                continue
            if head is not None:
                sample = walk.dataset[len(walk.results)]
                if (verdict := walk.take(head)) is not None:
                    if not decide(walk, verdict):
                        break
                    continue
                if backfills is not None and (
                    backfills.owed_backfills(sample) or backfills.backfills_for(sample)
                ):
                    walk.settling = sample
                    continue
                if (verdict := walk.judge()) is not None and not decide(walk, verdict):
                    break
                continue

            # Re-read every step, so a press landing mid-walk TOPS THE WINDOW UP rather than waiting
            # for it to drain. Nothing is spent here: the round that scored under the depth spends
            # it (`l1/score/winner.py`).
            if not stopping:
                if backfills is not None:
                    room = min(armed - out(), affordable())
                    owing = [walk.settling] if walk.settling is not None else []
                    owing += [walk.dataset[idx] for idx in sorted(walk.finished)]
                    for sample in owing:
                        room -= len(backfills.start_backfill(sample, room))
                fill(walk, armed, armed)
                for ahead in live():
                    if ahead is not walk:
                        fill(ahead, armed - 1, armed)

            calls: set[asyncio.Future[Any]] = {*draining}
            for walking in live():
                calls.update(walking.running.values())
            if backfills is not None:
                calls.update(backfills.backfills_in_flight())
            if not calls:
                if book is not None and cell is not None and affordable() == 0:
                    logger.warning(
                        "The spend book holds no further cell after query %d/%d; halting "
                        "mid-round.",
                        len(walk.results),
                        walk.n,
                    )
                    # Refused with the ceiling that binds, and the sums that say why.
                    book.hold(cell, "backend", what="the next cell")
                raise RuntimeError(
                    f"scoring phase stalled: nothing out and nothing to take at query "
                    f"{len(walk.results)}/{walk.n}"
                )
            await asyncio.wait(calls, return_when=asyncio.FIRST_COMPLETED)
            for walking in live():
                walking.collect()
            draining.difference_update([call for call in draining if call.done()])
        if not cancels:
            await land()
    except BaseException as stop:
        # A pause or a spent ceiling first waits out the calls already sent, which bill anyway; an
        # unreachable backend or a provider's refusal lands nothing more, and a cancellation aimed
        # at this phase is answered at once.
        if not cancels and (
            isinstance(stop, KeyboardInterrupt)
            or (isinstance(stop, StopLoop) and stop.reason in _BUDGET_STOPS)
            or (isinstance(stop, SendRefusedError) and stop.category in _CEILINGS)
        ):
            await land()
        # The phase stops rather than decides, so its walks resume later: keep what came back that
        # each was sure to take, and the catch-ups paired with those cells or with one it took.
        for walk in live():
            with graceful("Could not bank a stopped walk's returned cells"):
                sure = walk.bank()
                if backfills is not None:
                    backfills.bank_backfills(
                        [*sure, walk.settling] if walk.settling is not None else sure
                    )
        raise
    finally:
        # Not awaited — an `await` here can swallow a CancelledError aimed at this coroutine, and
        # answering a cancellation with a normal return tells the canceller it succeeded while the
        # work runs on: the L4 cell wall clock is enforced only if the CancelledError comes back.
        for walk in live():
            walk.end(None, cancel=True)
        if backfills is not None:
            backfills.discard_backfills()
        if gauge is not None:
            gauge.close()
