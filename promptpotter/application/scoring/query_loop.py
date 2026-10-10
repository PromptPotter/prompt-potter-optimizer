from __future__ import annotations

import asyncio
import contextvars
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

from promptpotter.application.scoring.sample_measurement import (
    cell_bound,
    emit_replayed_step_tokens,
    execute_stale_data_protocol,
    measure_sample,
)
from promptpotter.domain.backend import BackpressureReading
from promptpotter.domain.phases import (
    REFUSAL_STOPS,
    STOP_REASON_INFO,
    StopCategory,
    StopLoop,
    StopReason,
    WalkEnd,
)
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.scoring import GradedCell, MeasuredCell, Scorer
from promptpotter.domain.validators import StopRule, StopSignal
from promptpotter.infrastructure.backend import CELL
from promptpotter.infrastructure.llm.send_pacing import HELD_POLL_S
from promptpotter.infrastructure.llm.spend_book import (
    SendBound,
    bound_spend_book,
    filed,
)
from promptpotter.infrastructure.llm.telemetry import (
    emit_priced_key,
    emit_sample_scored,
    emit_sample_started,
)
from promptpotter.infrastructure.runtime_flags import effective_lookahead
from promptpotter.shared.errors import ErrorCategory, SendRefusedError, graceful
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.domain.results import ScoreSummary
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import CatchUps
    from promptpotter.infrastructure.store.measurement_archive import CellClaim

logger = logging.getLogger(__name__)

__all__ = [
    "ArmSlot",
    "BlockRace",
    "Flight",
    "FlightGauge",
    "QueryLoopResult",
    "Walk",
    "run_walks",
]


_BUDGET_STOPS = frozenset(
    reason for reason, info in STOP_REASON_INFO.items() if info.category is StopCategory.BUDGET
)


def _budget_refusal(category: ErrorCategory | None) -> StopReason | None:
    stop = REFUSAL_STOPS.get(category) if category is not None else None
    return stop if stop in _BUDGET_STOPS else None


def _budget_stop(stop: BaseException) -> StopReason | None:
    if isinstance(stop, StopLoop) and stop.reason in _BUDGET_STOPS:
        return stop.reason
    if isinstance(stop, SendRefusedError):
        return _budget_refusal(stop.category)
    return None


def _dearest(bounds: Sequence[SendBound | None]) -> SendBound | None:
    """``None`` where no cell is held whole (``Connector.holds_own_sends``)."""
    held = [b for b in bounds if b is not None]
    if not held:
        return None
    outputs = [b.output_tokens for b in held]
    costs = [b.usd for b in held]
    return SendBound(
        input_tokens=max(b.input_tokens for b in held),
        output_tokens=None if None in outputs else max(o for o in outputs if o is not None),
        usd=None if None in costs else max(c for c in costs if c is not None),
        unpriced=tuple(dict.fromkeys(name for b in held for name in b.unpriced)),
    )


NEXT_CELL = "the next cell"

MAX_CONSECUTIVE_ERRORS: int = 3


class BlockRace(Protocol):
    @property
    def block_size(self) -> int: ...

    def close(self, rows: Mapping[int, Sequence[GradedCell]]) -> Mapping[int, StopSignal]: ...


@dataclass(frozen=True)
class Flight:
    """``waiting`` is the ``(sample_id, launched_at)`` call a DECISION is held on under in-order absorption."""

    out: int = 0
    allowed: int = 0
    most: int = 0
    waiting: tuple[int, float] | None = None
    backpressure: BackpressureReading | None = None
    # ``None`` where no book bounds them; against a reserve it is the WORST case.
    affordable: int | None = None
    cell_usd: float | None = None


class FlightGauge:
    """Read at most once per ``every`` seconds: a full horizon is a bounds sweep, too dear per launch."""

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


_ABORTS_AT_ONCE: dict[ErrorCategory, WalkEnd] = {
    ErrorCategory.CLIENT: WalkEnd.CLIENT_ERROR,
    ErrorCategory.PIPELINE: WalkEnd.PIPELINE_ERROR,
}


@dataclass
class QueryLoopResult:
    """A decided walk: ``ended_on`` is ``None`` once it took every cell."""

    results: list[GradedCell]
    ended_on: WalkEnd | None = None
    stop_signal: StopSignal | None = None


def _emit_cached_step_tokens(facts: MeasuredCell) -> None:
    emit_replayed_step_tokens(facts.pipeline.step_tokens, facts.pipeline.step_timings)


class WalkRecorder(Protocol):
    def scores(self, results: Sequence[GradedCell]) -> ScoreSummary: ...

    def take(self, cell: GradedCell) -> GradedCell:
        """A measured sample is on disk iff it was TAKEN; a discarded look-ahead acquisition is not."""
        ...

    def bank(self, cells: Sequence[GradedCell]) -> None: ...

    def provenance(self, facts: MeasuredCell) -> str: ...


@dataclass(frozen=True)
class ArmSlot:
    """``idx`` is ``NO_ROUND_SLOT`` for a pass that is no arm of the round."""

    idx: int
    total: int
    individual_id: str


@dataclass
class QueryLoopState:
    search_point: JobSearchPoint
    session: Session
    cached_sample_results: dict[int, MeasuredCell]
    # ``None``: the walk writes no sample record — it is no pass a live surface draws.
    slot: ArmSlot | None
    role: str
    sample_index: SampleIndex | None
    deprecated_samples: dict[int, MeasuredCell]
    recorder: WalkRecorder
    # ``None`` where nothing is archived or the walk re-measures on purpose.
    claim_cell: Callable[[Sample], Awaitable[tuple[MeasuredCell | None, CellClaim | None]]] | None
    cell_keys: Mapping[int, str]
    # Shared by every walk, so a replay is metered the first time the campaign reads that cell only.
    counted: set[str]
    # Replays already priced when the walk opened: none waits on the ceiling or takes a spend stop.
    rereads: frozenset[int]

    @property
    def scorer(self) -> Scorer:
        return self.session.scoring.require_scorer()

    def sample_scored(
        self, cell: GradedCell, idx: int, total: int, *, running: ScoreSummary | None
    ) -> None:
        if (slot := self.slot) is not None:
            emit_sample_scored(
                candidate_idx=slot.idx,
                candidate_total=slot.total,
                individual_id=slot.individual_id,
                role=MeasurementRole(self.role),
                sample_idx=idx,
                sample_total=total,
                cell=cell,
                running=running,
            )

    def priced(self, sample: Sample) -> None:
        cell = self.cell_keys.get(sample.id)
        if cell is not None and cell not in self.counted:
            self.counted.add(cell)
            emit_priced_key(cell)


def _armed_cells(session: Session) -> int:
    """Sample look-ahead is LIVE though it defaults off: any dataset, the moment the operator arms it."""
    return effective_lookahead(
        session.control.sample_lookahead(), session.backend_client.max_cells_in_flight
    )


async def _maybe_recover_degraded(
    result: MeasuredCell,
    sample: Sample,
    ctx: QueryLoopState,
) -> MeasuredCell:

    if not is_deprecated(result):
        return result
    recovered, _step = await execute_stale_data_protocol(
        sample,
        result,
        ctx.session,
        pipeline_params=ctx.search_point.pipeline_params,
        sample_index=ctx.sample_index,
    )
    return recovered


@dataclass
class _Acquired:
    """Carries no side effect: every append, persist and ledger write belongs to :meth:`Walk.take`."""

    sample: Sample
    idx: int
    result: GradedCell
    fresh: bool
    deprecated_display: GradedCell | None = None
    # Held until the row is on disk or discarded, so no other walk measures the cell meanwhile.
    claim: CellClaim | None = None


def _returned_row(cell: asyncio.Task[_Acquired]) -> GradedCell | None:
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
        cached_r = await _maybe_recover_degraded(cached.replayed(), sample, ctx)
        return _Acquired(sample=sample, idx=idx, result=ctx.scorer.grade(cached_r), fresh=False)

    deprecated_display: GradedCell | None = None
    if (cached_deprecated := ctx.deprecated_samples.get(sample.id)) is not None:
        # Graded here, rendered at the take: a display call from a cell prints out of walk order.
        deprecated_display = ctx.scorer.grade(cached_deprecated.replayed())

    try:
        result = await measure_sample(
            sample,
            ctx.session,
            pipeline_params=ctx.search_point.pipeline_params,
        )
        result = await _maybe_recover_degraded(result, sample, ctx)
        if sample.id in ctx.deprecated_samples:
            result = replace(result, retry_of_deprecated_cache=True)
        if claim is not None:
            claim.publish(result.wire(), grade=ctx.recorder.provenance(result))
        graded = ctx.scorer.grade(result)
    except BaseException:
        if claim is not None:
            claim.release()
        raise
    return _Acquired(
        sample=sample,
        idx=idx,
        result=graded,
        fresh=True,
        deprecated_display=deprecated_display,
        claim=claim,
    )


LaunchEvent = tuple["Sample", int, int, int | None]


@dataclass
class Walk:
    """A cell frees its slot when it RETURNS, not when taken; no walk reads another's state across an await."""

    dataset: list[Sample]
    ctx: QueryLoopState
    checks: Sequence[StopRule]
    # Copied once per cell: a cell's throttle give-back and envelope deadline bind to its task.
    context: contextvars.Context
    # Rows an operator's skip let this walk take, replayed on resumption.
    skip_at: int | None = None
    results: list[GradedCell] = field(default_factory=list)
    consecutive_errors: int = 0
    submitted: int = 0
    running: dict[int, asyncio.Task[_Acquired]] = field(default_factory=dict)
    # Running cells still waiting on another process's claim: nothing sent, so every stop cancels them.
    claiming: set[int] = field(default_factory=set)
    finished: dict[int, asyncio.Task[_Acquired]] = field(default_factory=dict)
    launched_at: dict[int, float] = field(default_factory=dict)
    # Launch events of a walk measuring ahead of its turn; ``None`` once released.
    held: list[LaunchEvent] | None = field(default_factory=list)
    settling: Sample | None = None
    outcome: QueryLoopResult | None = None
    # The rows at which a block race next closes, where it may stop this walk. ``None`` outside one.
    boundary: int | None = None

    @property
    def n(self) -> int:
        return len(self.dataset)

    def enter_block(self, end: int) -> None:
        self.boundary = min(self.n, end)

    def launch(self, armed: int, horizon: int | None) -> tuple[Sample, asyncio.Task[_Acquired]]:
        idx = self.submitted
        sample = self.dataset[idx]
        # `armed` is the depth the round RUNS at, never the in-flight count, empty at each candidate's start.
        event: LaunchEvent = (sample, idx, armed, horizon)
        if self.held is not None:
            self.held.append(event)
        else:
            self._started(event)
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
        for event in held:
            self._started(event)

    def _started(self, event: LaunchEvent) -> None:
        if (slot := self.ctx.slot) is not None:
            sample, idx, armed, horizon = event
            emit_sample_started(
                candidate_idx=slot.idx,
                candidate_total=slot.total,
                sample_idx=idx,
                sample_total=self.n,
                sample_id=sample.id,
                query=sample.query,
                sample_lookahead=armed,
                stop_horizon=horizon,
            )

    def collect(self) -> None:
        for idx in [idx for idx, cell in self.running.items() if cell.done()]:
            self.finished[idx] = self.running.pop(idx)

    def head(self) -> asyncio.Task[_Acquired] | None:
        return None if self.settling is not None else self.finished.get(len(self.results))

    def take(self, cell: asyncio.Task[_Acquired]) -> QueryLoopResult | None:
        """The only writer of the rows; raises the cell's own error in its turn, never in its race."""
        acq = cell.result()
        del self.finished[acq.idx]
        ctx, n = self.ctx, self.n
        cell_key = ctx.cell_keys.get(acq.sample.id)
        if not acq.fresh:
            # Only if it STAYED a replay: the stale-data protocol may have re-measured for real.
            if acq.result.facts.cached and (cell_key is None or cell_key not in ctx.counted):
                _emit_cached_step_tokens(acq.result.facts)
        elif acq.deprecated_display is not None:
            ctx.sample_scored(acq.deprecated_display, acq.idx, n, running=None)
        ctx.priced(acq.sample)

        try:
            # Before the snapshot below, which names the cell by its answer's address.
            taken = ctx.recorder.take(acq.result)
            self.results.append(taken)
            running = ctx.recorder.scores(self.results)
        finally:
            if acq.claim is not None:
                acq.claim.release()

        # Asked of every row, replays included: the stale-data protocol re-measures a replayed cell.
        category = taken.facts.error_category
        if category is not None and (stop := REFUSAL_STOPS.get(category)) is not None:
            logger.warning("%s: %s", STOP_REASON_INFO[stop].label, taken.facts.error)
            raise StopLoop(stop, unmeasured=n - len(self.results) + 1)

        if acq.fresh:
            if taken.facts.errored:
                if reason := self._abort_reason(taken):
                    # The untouched tail is counted, never written as rows, which would keep the denominator full.
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

        ctx.sample_scored(taken, acq.idx, n, running=running)
        return None

    def _abort_reason(self, result: GradedCell) -> WalkEnd | None:
        cat = result.facts.error_category
        if cat is not None and (at_once := _ABORTS_AT_ONCE.get(cat)) is not None:
            return at_once
        self.consecutive_errors += 1
        if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            return WalkEnd.CONSECUTIVE_ERRORS
        return None

    def judge(self) -> QueryLoopResult | None:
        """Cached rows are judged too, or a candidate its priors already dominate runs one extra real query."""
        for check in self.checks:
            if (signal := check.check(self.results)) is not None:
                return QueryLoopResult(self.results, ended_on=WalkEnd.STOP_RULE, stop_signal=signal)
        if len(self.results) == self.n and self.boundary is None:
            return QueryLoopResult(self.results)
        return None

    def launch_horizon(self, last: int) -> int | None:
        """Index ``i`` may launch while no cut is possible before ``i`` rows, so a cut wastes one cell at most."""
        return self.horizon(last - 2)

    def horizon(self, last: int) -> int | None:
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

    def rows(self) -> list[GradedCell]:
        returned = (_returned_row(cell) for cell in self.finished.values())
        return [*self.results, *(row for row in returned if row is not None)]

    def flight(self) -> Flight:
        settled = len(self.results) + len(self.finished)
        horizon = self.horizon(self.n - 1)
        limit = self.n if horizon is None else min(self.n, horizon + 1)
        return Flight(len(self.running), limit - settled, self.n - settled)

    def bank(self) -> list[Sample]:
        """Only cells this walk was SURE to take: below its horizon and before the first error."""
        self.collect()
        horizon = self.horizon(self.n - 1)
        rows: list[GradedCell] = []
        paid: list[Sample] = []
        samples: list[Sample] = []
        for idx in sorted(self.finished):
            if horizon is not None and idx >= horizon:
                break
            cell = self.finished[idx]
            if cell.cancelled() or cell.exception() is not None:
                continue
            acq = cell.result()
            if acq.result.facts.errored:
                break
            if acq.fresh:
                rows.append(acq.result)
                paid.append(acq.sample)
            samples.append(acq.sample)
        if rows:
            self.ctx.recorder.bank(rows)
            for sample in paid:
                self.ctx.priced(sample)
        return samples

    def end(
        self, outcome: QueryLoopResult | None, *, cancel: bool
    ) -> list[asyncio.Task[_Acquired]]:
        """A cell in flight or returned-not-taken is DISCARDED: written, the rows depend on in-flight depth."""
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
    """Rows, cuts and events land where a SERIAL phase lands them; ``keep_cut`` returns a budget stop, not raises."""
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
        # A pause or a spent ceiling first waits out the calls already sent, which bill anyway.
        if not phase.cancels and (isinstance(stop, KeyboardInterrupt) or budget is not None):
            await phase.land()
        phase.bank()
        if budget is None or not keep_cut:
            raise
        phase.keep_cut()
        return budget
    finally:
        # Not awaited: an `await` here can swallow a CancelledError aimed at this coroutine.
        phase.discard()


@dataclass(frozen=True)
class _TurnState:
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
        # A pass the book's ceilings do not meter is neither admitted against them nor stopped by them.
        self.unbounded = self.book is not None and not self.book.binds(self.label.kind)

    def affordable(self) -> int:
        # Calls out are counted here, not read off the book: a cell launched this step holds nothing yet.
        book, cell, label = self.book, self.cell, self.label
        if book is None or cell is None or self.unbounded:
            return sys.maxsize
        return book.fits(book.held_at(label, cell), cell, beside=label.kind) - self.out()

    def live(self) -> list[Walk]:
        return [walk for walk in self.walks if walk is not None and walk.outcome is None]

    def out(self) -> int:
        # A catch-up and a cancelled call still draining count too, or `max_cells_in_flight` understates peak RSS.
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
            walk.enter_block((self.block + 1) * self.blocks.block_size)

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
        if not now.stopping:
            self.launch(walk, armed)
        await self.wait(walk, armed)
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
        # A cancelled call is the pause's own doing: the throttle wait polls the same flag.
        skip = session.control.skip_requested()
        pause = cut or session.control.pause_requested()
        tripped = session.control.budget_tripped()
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
            # A returned cell or a started catch-up is paid for, so it is kept before a stop is honoured.
            keeping=(settle is not None and unstarted == 0) or head is not None,
        )

    def honour_stop(self, walk: Walk, now: _TurnState) -> bool:
        if now.skip:
            session = self.session
            if session.control.spend_skip() and session.state.cycle_id:
                # Stamped where a searchpoint is actually cut, never at the press.
                session.store.campaigns.record_intervention(session.hop, kind="skip")
                session.human_intervened = True
            logger.info(
                "Operator skip after query %d/%d; accepting partial searchpoint.",
                len(walk.results),
                walk.n,
            )
            return self.decide(walk, QueryLoopResult(walk.results, ended_on=WalkEnd.SKIP))
        if now.pause or now.tripped is None:
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
        horizon = walk.launch_horizon(last)
        while (
            room > 0 and walk.submitted <= last and (horizon is None or walk.submitted <= horizon)
        ):
            sample, _cell = walk.launch(armed, horizon)
            room -= 1
            if backfills is not None:
                room -= len(backfills.start_backfill(sample, min(room, self.affordable())))

    async def depth_raised(self, armed: int) -> None:
        """Polled: the press is a command another process records on the ledger, which no signal carries."""
        while _armed_cells(self.session) <= armed:
            await asyncio.sleep(HELD_POLL_S)

    async def wait(self, walk: Walk, armed: int) -> None:
        book, cell, label = self.book, self.cell, self.label
        calls = self.outstanding()
        if not calls:
            if book is not None and cell is not None and self.affordable() == 0:
                logger.warning(
                    "The spend book holds no further cell after query %d/%d; halting mid-round.",
                    len(walk.results),
                    walk.n,
                )
                # Raises the refusal `hold` would, naming the ceiling that binds, and holds nothing.
                book.refuse_unless_room(book.held_at(label, cell), cell, NEXT_CELL)
            raise RuntimeError(
                f"scoring phase stalled: nothing out and nothing to take at query "
                f"{len(walk.results)}/{walk.n}"
            )
        watched = set[asyncio.Future[Any]](calls)
        raised: asyncio.Future[None] | None = None
        if armed < self.session.backend_client.max_cells_in_flight:
            raised = asyncio.ensure_future(self.depth_raised(armed))
            watched.add(raised)
        try:
            await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if raised is not None:
                raised.cancel()
        for walking in self.live():
            walking.collect()
        self.draining.difference_update([call for call in self.draining if call.done()])

    async def land(self) -> None:
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
                kept = [
                    cell
                    for cell in walk.results
                    if _budget_refusal(cell.facts.error_category) is None
                ]
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
