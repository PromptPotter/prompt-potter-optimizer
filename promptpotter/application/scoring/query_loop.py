"""The scoring walk — one search point's pass over its panel — and :func:`run_walks`, the one loop
that drives every walk of a scoring phase: prior-cache reuse, stale-data recovery, error
classification into an abort reason, look-ahead within the armed depth, and decisions in walk
order or at a block race's closes. The gateway turns a decided walk into the archived run."""

from __future__ import annotations

import asyncio
import contextvars
import enum
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast

from promptpotter.application.run_phase_control import pause_requested
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.sample_measurement import (
    STALE_DATA_LOAD_PROTOCOL,
    cell_bound,
    emit_replayed_step_tokens,
    measure_sample,
    needs_rerun,
)
from promptpotter.application.scoring.sample_measurement import (
    execute_stale_data_protocol as _execute_stale_data_protocol,
)
from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.phases import (
    REFUSAL_STOPS,
    STOP_REASON_INFO,
    StopCategory,
    StopLoop,
    StopReason,
)
from promptpotter.domain.scoring import CellScorer, QueryMeasurement
from promptpotter.domain.spend import StepTokenUsage
from promptpotter.domain.validators import StopRule, StopSignal
from promptpotter.infrastructure.backend import CELL
from promptpotter.infrastructure.llm.spend_book import (
    SendBound,
    bound_spend_book,
    filed,
)
from promptpotter.infrastructure.llm.telemetry import emit_priced_key
from promptpotter.infrastructure.runtime_flags import effective_lookahead
from promptpotter.shared.errors import (
    ErrorCategory,
    SendRefusedError,
    error_category,
    graceful,
    is_error_result,
)

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store.measurement_archive import CellClaim

logger = logging.getLogger(__name__)

__all__ = [
    "BlockRace",
    "CatchUps",
    "Flight",
    "FlightGauge",
    "QueryLoopResult",
    "Walk",
    "WalkEnd",
    "run_walks",
]


# The stops that still wait out the calls already sent — see :func:`run_walks`.
_BUDGET_STOPS = frozenset(
    reason for reason, info in STOP_REASON_INFO.items() if info.category is StopCategory.BUDGET
)


def _budget_refusal(category: ErrorCategory | None) -> StopReason | None:
    """The budget stop a refusal of this category is, ``None`` for any other."""
    stop = REFUSAL_STOPS.get(category) if category is not None else None
    return stop if stop in _BUDGET_STOPS else None


def _budget_stop(stop: BaseException) -> StopReason | None:
    if isinstance(stop, StopLoop) and stop.reason in _BUDGET_STOPS:
        return stop.reason
    if isinstance(stop, SendRefusedError):
        return _budget_refusal(stop.category)
    return None


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


class BlockRace(Protocol):
    """A race that decides its walks together: every live walk takes a block of ``block_size``
    cells, then ``close`` reads all their rows and names the walks it stops, by walk index."""

    @property
    def block_size(self) -> int: ...

    def close(self, rows: Mapping[int, list[QueryMeasurement]]) -> Mapping[int, StopSignal]: ...


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
    # How many MORE cells the spend book admits beside those out, and what one holds against the
    # limit that binds; ``None`` where no book bounds them. Against a reserve it is the WORST case.
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


class WalkEnd(enum.StrEnum):
    """Why ONE walk ended before its last cell, the round going on. A run's ending is a
    ``StopReason`` and is raised: a pause always, a budget stop unless the caller keeps the cut."""

    # The operator's early-abort: the partial is accepted.
    SKIP = "skip"
    STOP_RULE = "stop_rule"
    BUDGET = "budget"
    CLIENT_ERROR = "client_error"
    PIPELINE_ERROR = "pipeline_error"
    CONSECUTIVE_ERRORS = "consecutive_errors"


_ABORTS_AT_ONCE: dict[ErrorCategory, WalkEnd] = {
    ErrorCategory.CLIENT: WalkEnd.CLIENT_ERROR,
    ErrorCategory.PIPELINE: WalkEnd.PIPELINE_ERROR,
}


@dataclass
class QueryLoopResult:
    """A decided walk: ``ended_on`` is ``None`` once it took every cell."""

    results: list[QueryMeasurement]
    ended_on: WalkEnd | None = None
    stop_signal: StopSignal | None = None


def _with_running(
    result: QueryMeasurement, running: dict[str, Any], run_id: str
) -> QueryMeasurement:
    """A shallow copy carrying the candidate's running fitness and the archive run the row lands
    in — the ledger's copy. The persisted results keep the clean result, not this copy: an archived
    row is already filed under its run."""
    out = dict(result)
    out["_running"] = running
    out["run_id"] = run_id
    return cast(QueryMeasurement, out)


def _materialize_cached(item: QueryMeasurement) -> QueryMeasurement:
    """A banked row as a replay: flagged cached, and its elapsed clock zeroed — nothing was spent."""
    r: dict[str, Any] = {**item, "cached": True}
    pd = r.get("pipeline_data")
    if isinstance(pd, dict):
        r["pipeline_data"] = {**pd, "total_time": 0.0}
    return cast(QueryMeasurement, r)


def _graded(row: QueryMeasurement, scorer: CellScorer) -> QueryMeasurement:
    """Every row the walk holds, fresh or replayed, is graded HERE under the run's own scorer. A
    ``ScoringFormulaError`` is a formula contract bug and halts the run; it never becomes a row."""
    rescore_results([cast("dict[str, Any]", row)], scorer)
    return row


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
    sample_index: SampleIndex | None
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
    # A cell no prior covers: the row another walk banked or is measuring, else this walk's hold on
    # it. ``None`` where nothing is archived or the walk re-measures on purpose.
    claim_cell: (
        Callable[[Sample], Awaitable[tuple[QueryMeasurement | None, CellClaim | None]]] | None
    )
    # Each sample's cell (`ReplayFeed.cell_key`); empty where nothing is archived to replay.
    cell_keys: Mapping[int, str]
    # The campaign's priced set (`SessionState.priced_keys`), shared by every walk and grown
    # as each takes one, so a replay is metered the first time the campaign reads that cell only.
    counted: set[str]
    # The replays already priced when the walk opened: none waits on the ceiling and no spend stop
    # lands on one.
    rereads: frozenset[int]

    def priced(self, sample: Sample) -> None:
        """Mark this sample's cell priced — billed fresh or metered as a replay. Said on the ledger
        the first time, which is where a later launch learns it."""
        cell = self.cell_keys.get(sample.id)
        if cell is not None and cell not in self.counted:
            self.counted.add(cell)
            emit_priced_key(cell)


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

    if not needs_rerun(result):
        return result
    recovered, _step = await _execute_stale_data_protocol(
        list(STALE_DATA_LOAD_PROTOCOL),
        sample,
        cast(dict[str, Any], result),
        ctx.session,
        pipeline_params=ctx.search_point.pipeline_params,
        sample_index=ctx.sample_index,
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
    # Held until the row is on disk or discarded, so no other walk measures the cell meanwhile.
    claim: CellClaim | None = None


def _returned_row(cell: asyncio.Task[_Acquired]) -> QueryMeasurement | None:
    if not cell.done() or cell.cancelled() or cell.exception() is not None:
        return None
    return cell.result().result


def _release_claim(cell: asyncio.Task[_Acquired]) -> None:
    returned = cell.done() and not cell.cancelled() and cell.exception() is None
    if returned and (claim := cell.result().claim) is not None:
        claim.release()


def _quiet(call: asyncio.Future[Any]) -> None:
    # Retrieved, so a discarded call's own failure is not reported as unhandled.
    if not call.cancelled():
        call.exception()


async def _acquire(sample: Sample, idx: int, ctx: QueryLoopState, claiming: set[int]) -> _Acquired:
    cached = ctx.cached_sample_results.get(sample.id)
    claim: CellClaim | None = None
    if cached is None and ctx.claim_cell is not None:
        claiming.add(idx)
        try:
            cached, claim = await ctx.claim_cell(sample)
        finally:
            claiming.discard(idx)
    if cached is not None:
        # Can re-measure for real, so a hit gets a slot like anything else.
        cached_r = await _maybe_recover_degraded(_materialize_cached(cached), sample, ctx)
        return _Acquired(sample=sample, idx=idx, result=_graded(cached_r, ctx.scorer), fresh=False)

    deprecated_display: QueryMeasurement | None = None
    if (cached_deprecated := ctx.deprecated_samples.get(sample.id)) is not None:
        # Graded here, rendered at the take: a display call from a cell prints out of walk order.
        deprecated_display = _graded(_materialize_cached(cached_deprecated), ctx.scorer)

    try:
        result = await measure_sample(
            sample,
            ctx.session,
            pipeline_params=ctx.search_point.pipeline_params,
        )
        result = await _maybe_recover_degraded(result, sample, ctx)
        if sample.id in ctx.deprecated_samples:
            cast(dict[str, Any], result)["retry_of_deprecated_cache"] = True
        if claim is not None:
            claim.publish(cast(dict[str, Any], result))
        result = _graded(result, ctx.scorer)
    except BaseException:
        if claim is not None:
            claim.release()
        raise
    return _Acquired(
        sample=sample,
        idx=idx,
        result=result,
        fresh=True,
        deprecated_display=deprecated_display,
        claim=claim,
    )


LaunchEvent = tuple[str, int, int, int, int, int | None]


@dataclass
class Walk:
    """One search point's pass over its panel, in the order GIVEN — the sampler orders the
    round's panel once, so walking it as-given IS the round order. Passive: :func:`run_walks`
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
    # The running cells still waiting on another process's claim: nothing sent, so every stop and
    # end cancels them. Each keeps its slot, which its send takes if the holder drops the cell.
    claiming: set[int] = field(default_factory=set)
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
    # The rows at which a block race next closes, where it may stop this walk. ``None`` outside one.
    boundary: int | None = None

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
            _acquire(sample, idx, self.ctx, self.claiming),
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
        cell_key = ctx.cell_keys.get(acq.sample.id)
        if not acq.fresh:
            # Only if it STAYED a replay: the stale-data protocol may have re-measured for real,
            # and that path already emitted its own fresh records.
            if acq.result.get("cached") and (cell_key is None or cell_key not in ctx.counted):
                _emit_cached_step_tokens(acq.result)
        elif acq.deprecated_display is not None and ctx.on_sample_scored is not None:
            ctx.on_sample_scored(acq.deprecated_display, acq.idx, n)
        ctx.priced(acq.sample)

        self.results.append(acq.result)
        try:
            running = (
                ctx.persist_fresh(self.results) if acq.fresh else ctx.running_scores(self.results)
            )
        finally:
            if acq.claim is not None:
                acq.claim.release()

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
                    return QueryLoopResult(self.results, ended_on=reason)
            else:
                self.consecutive_errors = 0

        if ctx.on_sample_scored is not None:
            ctx.on_sample_scored(_with_running(acq.result, running, ctx.run_id), acq.idx, n)
        return None

    def _abort_reason(self, result: QueryMeasurement) -> WalkEnd | None:
        cat = error_category(result)
        if cat is not None and (at_once := _ABORTS_AT_ONCE.get(cat)) is not None:
            return at_once
        self.consecutive_errors += 1
        if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            return WalkEnd.CONSECUTIVE_ERRORS
        return None

    def judge(self) -> QueryLoopResult | None:
        """The stop rules over the rows taken — cached rows too, or a candidate whose priors already
        dominate it runs one extra real query — else complete once every cell is taken, unless a
        block race's last close still decides it."""
        for check in self.checks:
            if (signal := check.check(self.results)) is not None:
                return QueryLoopResult(self.results, ended_on=WalkEnd.STOP_RULE, stop_signal=signal)
        if len(self.results) == self.n and self.boundary is None:
            return QueryLoopResult(self.results)
        return None

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
        stops = [check.earliest_stop(self.results, upcoming) for check in self.checks]
        if self.boundary is not None and self.boundary <= last + 1:
            stops.append(self.boundary)
        return min((m for m in stops if m is not None), default=None)

    def rereads_ahead(self, start: int) -> int:
        end = start
        while end < self.n and self.dataset[end].id in self.ctx.rereads:
            end += 1
        return end - start

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
        paid: list[Sample] = []
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
                paid.append(acq.sample)
            samples.append(acq.sample)
        if rows:
            self.ctx.bank(self.results, rows)
            # Billed already, and the resumed walk meets each as a replay.
            for sample in paid:
                self.ctx.priced(sample)
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
            cause = "the phase ended" if outcome is None else (outcome.ended_on or "complete")
            if outcome is not None and outcome.stop_signal is not None:
                cause = f"{cause}: {outcome.stop_signal.check_name}"
            logger.info(
                "Discarding %d look-ahead acquisition(s) after query %d/%d (%s).",
                len(self.running) + len(self.finished),
                len(self.results),
                self.n,
                cause,
            )
        for cell in self.finished.values():
            _quiet(cell)
            _release_claim(cell)
        self.drop_claim_waits()
        draining = list(self.running.values())
        for cell in draining:
            if cancel:
                cell.cancel()
            cell.add_done_callback(_quiet)
            cell.add_done_callback(_release_claim)
        self.running, self.finished = {}, {}
        return draining

    def drop_claim_waits(self) -> None:
        for idx, cell in self.running.items():
            if idx in self.claiming:
                cell.cancel()


async def run_walks(
    walks: Sequence[Walk | None],
    session: Session,
    *,
    keep_cut: bool,
    backfills: CatchUps | None = None,
    blocks: BlockRace | None = None,
    on_turn: Callable[[int, int | None], None] | None = None,
    on_decided: Callable[[int], None] | None = None,
) -> StopReason | None:
    """Drive a scoring phase: every walk measures at once, and they are taken and decided one at a
    time, in order, so every row, cut, prior and event lands where a serial phase lands it.

    Only the walk whose TURN it is takes cells, answers a skip and is decided. ``on_turn(i, block)``
    opens its turn before its held launches are released — ``block`` the 0-based block it walks under
    a block race, else ``None``; ``on_decided(i)`` runs once it is decided, before the next turn
    opens. A ``None`` walk has nothing to measure and is decided on its turn.

    **Under a block race the turns cycle per block.** Each live walk in index order takes the block
    and hands the turn on; once all have, ``blocks.close`` decides them together on the rows they
    share, and the survivors start the next block — so a cut never waits on an arm's place in line.

    **The depth bounds every call the phase has out** — its cells, the PoBB catch-ups that pair
    them, and discarded calls still winding down — which a whole inner campaign per call makes a
    memory bound. Slots go in one order: the catch-ups the walk on turn waits on, then its own
    cells, then the other live walks in index order, which leave one slot free; and no call starts
    that the spend book cannot hold beside every call out (:func:`_dearest`). A pause is raised; a
    pause that already cancelled a call is the same pause.

    **A budget stop is raised too, unless ``keep_cut``.** Then every walk still undecided is decided
    on the rows it took, less the cell the ceiling refused, and the stop is returned — ``None``
    where the phase ran to its end.

    **A sent call is cancelled only where that stops what it bills**
    (``Connector.cancel_stops_billing``). Elsewhere the backend finishes it and the provider bills
    it whether or not anyone waits, so it is left to land, counted against the depth, before the
    phase ends — or stops, where what it returns is banked."""
    phase = _ScoringPhase(walks, session, backfills, blocks, on_turn, on_decided)
    phase.cell = _dearest(
        [
            await cell_bound(session, walk.ctx.search_point.pipeline_params or {})
            for walk in walks
            if walk is not None
        ]
    )
    if phase.gauge is not None:
        phase.gauge.open(phase.reading)
    try:
        walking_on = phase.first_turn()
        while walking_on:
            walking_on = await phase.step()
        if not phase.cancels:
            await phase.land()
        return None
    except BaseException as stop:
        budget = _budget_stop(stop)
        # A pause or a spent ceiling first waits out the calls already sent, which bill anyway; an
        # unreachable backend or a provider's refusal lands nothing more, and a cancellation aimed
        # at this phase is answered at once.
        if not phase.cancels and (isinstance(stop, KeyboardInterrupt) or budget is not None):
            await phase.land()
        phase.bank()
        if budget is None or not keep_cut:
            raise
        phase.keep_cut()
        return budget
    finally:
        # Not awaited — an `await` here can swallow a CancelledError aimed at this coroutine, and
        # answering a cancellation with a normal return tells the canceller it succeeded while the
        # work runs on: the L4 cell wall clock is enforced only if the CancelledError comes back.
        phase.discard()


@dataclass(frozen=True)
class _TurnState:
    """Where the walk on turn stands at one step, and which stop is asked of it."""

    head: asyncio.Task[_Acquired] | None
    settle: Sample | None
    settled: bool
    cut: bool
    skip: bool
    pause: bool
    tripped: StopReason | None
    keeping: bool

    @property
    def stopping(self) -> bool:
        return self.skip or self.pause or self.tripped is not None


@dataclass
class _ScoringPhase:
    """What the steps of one :func:`run_walks` call share: whose turn it is and every call out."""

    walks: Sequence[Walk | None]
    session: Session
    backfills: CatchUps | None
    blocks: BlockRace | None
    on_turn: Callable[[int, int | None], None] | None
    on_decided: Callable[[int], None] | None
    cell: SendBound | None = None
    turn: int = -1
    block: int = 0
    draining: set[asyncio.Future[Any]] = field(default_factory=set)
    announced: set[int] = field(default_factory=set)
    decided: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.gauge = self.session.flight
        self.cancels = self.session.backend_client.cancel_stops_billing
        self.book = bound_spend_book()
        self.label = filed(CELL)
        # A pass the book's ceilings do not meter — a bench pass on a controlled arm — is neither
        # admitted against them nor stopped by them.
        self.unbounded = self.book is not None and not self.book.binds(self.label.kind)

    def affordable(self) -> int:
        # At the book's own price for a cell, and every call out counted at it here rather than
        # read back off the book: a cell launched this step has not placed its own hold yet.
        book, cell, label = self.book, self.cell, self.label
        if book is None or cell is None or self.unbounded:
            return sys.maxsize
        return book.fits(book.held_at(label, cell), cell, beside=label.kind) - self.out()

    def live(self) -> list[Walk]:
        return [walk for walk in self.walks if walk is not None and walk.outcome is None]

    def out(self) -> int:
        cells = sum(len(walk.running) for walk in self.live())
        catching = len(self.backfills.backfills_in_flight()) if self.backfills is not None else 0
        return cells + len(self.draining) + catching

    def outstanding(self) -> set[asyncio.Future[Any]]:
        calls = {call for call in self.draining if not call.done()}
        for walking in self.live():
            calls.update(walking.running.values())
        if self.backfills is not None:
            calls.update(self.backfills.backfills_in_flight())
        return calls

    def reach(self, walk: Walk) -> None:
        if self.blocks is not None:
            walk.boundary = min(walk.n, (self.block + 1) * self.blocks.block_size)

    def announce(self, i: int) -> None:
        self.announced.add(i)
        if self.on_turn is not None:
            self.on_turn(i, None if self.blocks is None else self.block)

    def conclude(self, i: int) -> None:
        self.decided.add(i)
        if self.on_decided is not None:
            self.on_decided(i)

    def first_turn(self) -> bool:
        for opened in self.live():
            self.reach(opened)
        return self.advance()

    def advance(self) -> bool:
        while True:
            self.turn += 1
            if self.turn >= len(self.walks):
                if self.blocks is None or not self.close_block():
                    return False
                self.turn = 0
            walk = self.walks[self.turn]
            # A block race turns every live walk once per block; a decided one had its last turn.
            if (walk is None and self.block > 0) or (walk is not None and walk.outcome is not None):
                continue
            self.announce(self.turn)
            if walk is not None:
                walk.release()
                if walk.dataset:
                    return True
                walk.end(QueryLoopResult([]), cancel=self.cancels)
            self.conclude(self.turn)

    def close_block(self) -> bool:
        """Every live walk took the block: the race decides them together, and a walk through its
        whole panel completes. False once no walk is left."""
        assert self.blocks is not None
        racing = {
            i: walk
            for i, walk in enumerate(self.walks)
            if walk is not None and walk.outcome is None
        }
        stops = self.blocks.close({i: walk.results for i, walk in racing.items()}) if racing else {}
        for i, walk in racing.items():
            if (signal := stops.get(i)) is not None:
                verdict = QueryLoopResult(
                    walk.results, ended_on=WalkEnd.STOP_RULE, stop_signal=signal
                )
            elif len(walk.results) == walk.n:
                verdict = QueryLoopResult(walk.results)
            else:
                continue
            self.draining.update(walk.end(verdict, cancel=self.cancels))
            self.conclude(i)
        self.block += 1
        for walk in self.live():
            self.reach(walk)
        return bool(self.live())

    async def step(self) -> bool:
        """One step of the walk on turn: honour a stop, else take what is back, else launch and
        wait. False once no walk is left."""
        walk = self.walks[self.turn]
        assert walk is not None
        if self.gauge is not None:
            self.gauge.touch()
        armed = _armed_cells(self.session)
        if walk.skip_at is not None and len(walk.results) >= walk.skip_at:
            logger.info("Replaying an operator skip after query %d/%d.", walk.skip_at, walk.n)
            return self.decide(walk, QueryLoopResult(walk.results, ended_on=WalkEnd.SKIP))
        now = self.turn_state(walk)
        if now.stopping and (now.cut or not now.keeping):
            return self.honour_stop(walk, now)
        if now.settled and now.settle is not None and self.backfills is not None:
            walk.settling = None
            self.backfills.commit_backfills(now.settle)
            return self.judged(walk)
        if now.head is not None:
            return self.take_head(walk, now.head)
        # Re-read every step, so a press landing mid-walk TOPS THE WINDOW UP rather than waiting
        # for it to drain. Nothing is spent here: the round that scored under the depth spends
        # it (`runner/measurement.py`).
        if not now.stopping:
            self.launch(walk, armed)
        await self.wait(walk)
        return True

    def turn_state(self, walk: Walk) -> _TurnState:
        session, backfills = self.session, self.backfills
        head = walk.head()
        settle = walk.settling
        started: list[asyncio.Future[Any]] = []
        unstarted = 0
        if settle is not None and backfills is not None:
            started = backfills.backfills_for(settle)
            unstarted = backfills.owed_backfills(settle)
        cut = (head is not None and head.cancelled()) or any(c.cancelled() for c in started)
        # Answered only by the walk whose turn it is — the skip names the candidate on screen —
        # so nothing measuring for it can spend the press. A cancelled call is the pause's own
        # doing: the throttle wait polls the same flag.
        skip = bool(session.skip_check and session.skip_check())
        pause = cut or pause_requested(session)
        # Same cadence as the pause, because the round-boundary gate cannot fire until the round
        # closes — and for an L4 outer round every sample is an entire inner CAMPAIGN.
        tripped = session.budget_tripped() if session.budget_tripped is not None else None
        if self.unbounded or walk.rereads_ahead(len(walk.results)):
            tripped = None
        return _TurnState(
            head=head,
            settle=settle,
            settled=settle is not None and unstarted == 0 and all(c.done() for c in started),
            cut=cut,
            skip=skip,
            pause=pause,
            tripped=tripped,
            # A returned cell, or a catch-up already started, is paid for — kept before a stop is
            # honoured, so the stop lands a row later, as if pressed a moment later. Keeping starts
            # nothing: while a stop waits, only what is already out is waited on.
            keeping=(settle is not None and unstarted == 0) or head is not None,
        )

    def honour_stop(self, walk: Walk, now: _TurnState) -> bool:
        if now.skip:
            if self.session.skip_consume:
                self.session.skip_consume()
            logger.info(
                "Operator skip after query %d/%d; accepting partial searchpoint.",
                len(walk.results),
                walk.n,
            )
            return self.decide(walk, QueryLoopResult(walk.results, ended_on=WalkEnd.SKIP))
        if now.pause or now.tripped is None:
            # Between samples, where every TAKEN result is already on disk, so this exits
            # cleanly and `resume` continues into the remaining samples.
            logger.debug("Pause after query %d/%d.", len(walk.results), walk.n)
            raise KeyboardInterrupt("graceful")
        logger.warning(
            "Budget ceiling reached after query %d/%d (%s); halting mid-round.",
            len(walk.results),
            walk.n,
            now.tripped.value,
        )
        raise StopLoop(now.tripped)

    def take_head(self, walk: Walk, head: asyncio.Task[_Acquired]) -> bool:
        backfills = self.backfills
        sample = walk.dataset[len(walk.results)]
        if (verdict := walk.take(head)) is not None:
            return self.decide(walk, verdict)
        if backfills is not None and (
            backfills.owed_backfills(sample) or backfills.backfills_for(sample)
        ):
            walk.settling = sample
            return True
        return self.judged(walk)

    def decide(self, walk: Walk, verdict: QueryLoopResult) -> bool:
        self.draining.update(walk.end(verdict, cancel=self.cancels))
        self.conclude(self.turn)
        return self.advance()

    def judged(self, walk: Walk) -> bool:
        """Decide the walk on turn where its rules say so, or hand the turn on at a block's end.
        False once no walk is left."""
        if (verdict := walk.judge()) is not None:
            return self.decide(walk, verdict)
        if walk.boundary is not None and len(walk.results) == walk.boundary:
            return self.advance()
        return True

    def launch(self, walk: Walk, armed: int) -> None:
        if self.backfills is not None:
            room = min(armed - self.out(), self.affordable())
            owing = [walk.settling] if walk.settling is not None else []
            owing += [walk.dataset[idx] for idx in sorted(walk.finished)]
            for sample in owing:
                room -= len(self.backfills.start_backfill(sample, room))
        self.fill(walk, armed, armed)
        for ahead in self.live():
            if ahead is not walk:
                self.fill(ahead, armed - 1, armed)

    def fill(self, walk: Walk, cap: int, armed: int) -> None:
        backfills = self.backfills
        room = min(cap - self.out(), max(self.affordable(), walk.rereads_ahead(walk.submitted)))
        last = min(walk.n if walk.skip_at is None else walk.skip_at, walk.submitted + room) - 1
        if walk.boundary is not None:
            # One cell past a pending close at most, whatever the rules allow.
            last = min(last, walk.boundary)
        if last < walk.submitted:
            return
        horizon = walk.horizon(last - 2)
        while (
            room > 0 and walk.submitted <= last and (horizon is None or walk.submitted <= horizon)
        ):
            sample, _cell = walk.launch(armed, horizon)
            room -= 1
            if backfills is not None:
                room -= len(backfills.start_backfill(sample, min(room, self.affordable())))

    async def wait(self, walk: Walk) -> None:
        book, cell, label = self.book, self.cell, self.label
        calls = self.outstanding()
        if not calls:
            if book is not None and cell is not None and self.affordable() == 0:
                logger.warning(
                    "The spend book holds no further cell after query %d/%d; halting mid-round.",
                    len(walk.results),
                    walk.n,
                )
                # Refused with the ceiling that binds, and the sums that say why.
                book.hold(book.held_at(label, cell), cell, label.kind, what="the next cell")
            raise RuntimeError(
                f"scoring phase stalled: nothing out and nothing to take at query "
                f"{len(walk.results)}/{walk.n}"
            )
        await asyncio.wait(calls, return_when=asyncio.FIRST_COMPLETED)
        for walking in self.live():
            walking.collect()
        self.draining.difference_update([call for call in self.draining if call.done()])

    async def land(self) -> None:
        """Wait out every call already sent, starting none, so each one's cost is on the record."""
        for walking in self.live():
            walking.drop_claim_waits()
        if calls := self.outstanding():
            if self.gauge is not None:
                self.gauge.touch()
            await asyncio.wait(calls)
        for walking in self.live():
            walking.collect()
        self.draining.clear()

    def bank(self) -> None:
        # The phase stops rather than decides, so its walks resume later: keep what came back that
        # each was sure to take, and the catch-ups paired with those cells or with one it took.
        for walk in self.live():
            with graceful("Could not bank a stopped walk's returned cells"):
                sure = walk.bank()
                if self.backfills is not None:
                    self.backfills.bank_backfills(
                        [*sure, walk.settling] if walk.settling is not None else sure
                    )

    def keep_cut(self) -> None:
        for i, walk in enumerate(self.walks):
            if i in self.decided:
                continue
            if i not in self.announced:
                self.announce(i)
            if walk is not None:
                # The cell the ceiling refused was taken as a row before it stopped the phase.
                kept = [row for row in walk.results if _budget_refusal(error_category(row)) is None]
                walk.end(QueryLoopResult(kept, ended_on=WalkEnd.BUDGET), cancel=True)
            self.conclude(i)

    def discard(self) -> None:
        for walk in self.live():
            walk.end(None, cancel=True)
        if self.backfills is not None:
            self.backfills.discard_backfills()
        if self.gauge is not None:
            self.gauge.close()

    def reading(self) -> Flight:
        backfills, book, cell, label = self.backfills, self.book, self.cell, self.label
        walking = self.live()
        parts = [walk.flight() for walk in walking]
        winding = sum(1 for call in self.draining if not call.done())
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
        if 0 <= self.turn < len(self.walks) and (holder := self.walks[self.turn]) is not None:
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
            self.session.backend_client.backpressure.reading(),
            affordable=(
                None
                if book is None or cell is None or self.unbounded
                else max(0, self.affordable())
            ),
            cell_usd=(
                None
                if book is None or cell is None
                else book.binding(book.held_at(label, cell), cell, beside=label.kind).usd
            ),
        )
