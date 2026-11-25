from __future__ import annotations

import asyncio
import contextvars
import sys
import time
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

from promptpotter.application.initialization.session import Session
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.run_phase_control import Flight, budget_stop
from promptpotter.application.scoring.sample_measurement import (
    cell_bound,
    emit_replayed_step_tokens,
    execute_stale_data_protocol,
    measure_sample,
)
from promptpotter.domain.account_words import refusal_words
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.command_kinds import MAX_SPEED
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pass_kinds import PASSES
from promptpotter.domain.phases import (
    ERROR_CATEGORY_INFO,
    STOP_REASON_INFO,
    StopCategory,
    StopReason,
    WalkEnd,
)
from promptpotter.domain.records.run_records import (
    ArmAct,
    FlightWaiting,
    RaceCatchUpRecord,
    RoundWarningRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    StopLoop,
)
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.sample import FiledAnswer, Sample, Stretch
from promptpotter.domain.scoring import (
    MAX_PASS_ROWS,
    CellSheet,
    GradedCell,
    MeasuredCell,
    Scorer,
    WalkedCell,
)
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.domain.validators import StopRule, StopSignal
from promptpotter.infrastructure.backend import CELL
from promptpotter.infrastructure.llm.heartbeat import CellBeat, heartbeat
from promptpotter.infrastructure.llm.send_pacing import HELD_POLL_S
from promptpotter.infrastructure.llm.spend_book import (
    SendBound,
    filed,
)
from promptpotter.infrastructure.llm.telemetry import (
    append_record,
    bound_round,
    claim_campaign_price,
    current_round,
)
from promptpotter.infrastructure.run_scope import run_scope
from promptpotter.infrastructure.store.command_log import LookaheadWindow, effective_lookahead
from promptpotter.infrastructure.store.io import HeldLock
from promptpotter.infrastructure.store.measurement_archive import ReplayFeed
from promptpotter.shared.errors import (
    ErrorCategory,
    PayloadInvalidError,
    SendRefusedError,
)
from promptpotter.shared.measurement_context import MeasuredPass, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.scoring.catch_up import CatchUpPool, CaughtUp


_BUDGET_STOPS = frozenset(
    reason for reason, info in STOP_REASON_INFO.items() if info.category is StopCategory.BUDGET
)


def _budget_refusal(category: ErrorCategory | None) -> StopReason | None:
    stop = ERROR_CATEGORY_INFO[category].stops if category is not None else None
    return stop if stop in _BUDGET_STOPS else None


def _budget_stop(stop: BaseException) -> StopLoop | None:
    """A budget stop as it travels on: a walk's own, or a refused send's, in its sentence."""
    if isinstance(stop, StopLoop) and stop.reason in _BUDGET_STOPS:
        return stop
    if isinstance(stop, SendRefusedError) and (reason := _budget_refusal(stop.category)):
        return StopLoop(reason, detail=refusal_words(stop))
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


class BlockRace[K: Hashable](Protocol):
    @property
    def block_size(self) -> int: ...

    def close(self, rows: Mapping[K, Sequence[GradedCell]]) -> Mapping[K, StopSignal]: ...

    def choose_rows(
        self, taken: Mapping[K, Sequence[GradedCell]], ahead: Mapping[K, Sequence[Sample]]
    ) -> Mapping[K, Stretch]:
        """At each block's open, every live walk's stretch: a block closes once each has taken its
        calls, its budget capping them, and hands out at least one call while a walk has a row
        left. Read off the rows taken alone: no cell in flight enters, so a press never moves the
        choice."""
        ...


_ABORTS_AT_ONCE: dict[ErrorCategory, WalkEnd] = {
    ErrorCategory.CLIENT: WalkEnd.CLIENT_ERROR,
    ErrorCategory.PARAMETER_REFUSED: WalkEnd.PARAMETER_REFUSED,
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


@dataclass
class QueryLoopState:
    search_point: JobSearchPoint
    session: Session
    kind: MeasurementRole
    # ``None`` where nothing is archived or the walk re-measures on purpose.
    feed: ReplayFeed | None
    # ``None``: the walk writes no sample record — it is no pass a live surface draws.
    recorded: MeasuredPass | None
    sample_index: SampleIndex | None
    recorder: WalkRecorder
    # The rows of the walk this one continues: its running fold covers them, then its own they lack.
    held: CellSheet
    # The archive's answers by sample key, first read when a row is first sent or taken, never at
    # open: a row the walk never reaches costs no read.
    filed: dict[str, FiledAnswer] | None = None
    # By sample id, whether its replay is priced nothing (`rereads`), as first asked.
    free: dict[int, bool] = field(default_factory=dict)

    def advance(self) -> dict[str, FiledAnswer]:
        """The archive's answers, what landed since the walk last read it folded in."""
        filed = self.filed = {} if self.filed is None else self.filed
        if self.feed is not None:
            filed.update(self.feed.advance())
        return filed

    def _answer(self, sample: Sample) -> MeasuredCell | None:
        held = (self.advance() if self.filed is None else self.filed).get(sample.key)
        return None if held is None else held.cell

    def replay(self, sample: Sample) -> MeasuredCell | None:
        cell = self._answer(sample)
        return None if cell is None or is_deprecated(cell) else cell

    def deprecated(self, sample: Sample) -> MeasuredCell | None:
        """An archived answer the stale-data protocol re-measures."""
        cell = self._answer(sample)
        return cell if cell is not None and is_deprecated(cell) else None

    def rereads(self, sample: Sample) -> bool:
        """A replay the bound meter prices nothing for: it waits on no ceiling, takes no spend stop."""
        if (free := self.free.get(sample.id)) is None:
            book, feed = run_scope().book, self.feed
            free = self.free[sample.id] = (
                feed is not None
                and self.replay(sample) is not None
                and (book is None or book.replays_free(feed.cell_key(sample.key)))
            )
        return free

    @property
    def scorer(self) -> Scorer:
        return self.session.state.scoring.scorer

    def running(self, results: Sequence[GradedCell]) -> ScoreSummary:
        held = frozenset(cell.key for cell in self.held)
        return self.recorder.scores(
            [*self.held, *(cell for cell in results if cell.key not in held)]
        )

    def sample_scored(
        self,
        cell: GradedCell,
        sample: Sample,
        idx: int,
        total: int,
        *,
        running: ScoreSummary | None,
    ) -> None:
        if (recorded := self.recorded) is not None:
            append_record(
                SampleScoredRecord(
                    round=bound_round(),
                    individual_id=recorded.individual_id,
                    role=recorded.role,
                    sample_idx=idx,
                    sample_total=total,
                    position=sample.id,
                    result=cell,
                    running=running,
                )
            )

    def claim_price(self, sample: Sample) -> bool:
        """True on its campaign's first reading of the cell, which this call claims: the one a
        replay is metered on."""
        return self.feed is None or claim_campaign_price(self.feed.cell_key(sample.key))


def _armed(session: Session, kind: MeasurementRole) -> LookaheadWindow:
    """Sample look-ahead is LIVE though it defaults off: any dataset, the moment the operator arms it."""
    ceiling = session.backend_client.connector.max_cells_in_flight
    asked = MAX_SPEED if PASSES[kind].max_speed else session.state.control.sample_lookahead()
    return effective_lookahead(asked, ceiling)


async def _maybe_recover_degraded(
    result: MeasuredCell,
    sample: Sample,
    ctx: QueryLoopState,
) -> MeasuredCell:

    if not is_deprecated(result):
        return result
    return await execute_stale_data_protocol(
        sample,
        result,
        ctx.session,
        pipeline_params=ctx.search_point.pipeline_params,
        sample_index=ctx.sample_index,
    )


@dataclass
class _Acquired:
    """Carries no side effect but a claimed cell's archive row, filed before its claim retires so a
    waiter replays it: every other append, persist and ledger write belongs to :meth:`Walk.take`.
    A claimed cell a cut discards stays filed, already paid."""

    sample: Sample
    result: GradedCell
    fresh: bool
    deprecated_display: GradedCell | None = None


def _returned_row(cell: asyncio.Task[_Acquired]) -> GradedCell | None:
    if not cell.done() or cell.cancelled() or cell.exception() is not None:
        return None
    return cell.result().result


def _quiet(call: asyncio.Future[Any]) -> None:
    # Retrieved, so a discarded call's own failure is not reported as unhandled.
    if not call.cancelled():
        call.exception()


_CLAIM_POLL_S = 0.5


async def _claim_cell(
    sample: Sample, feed: ReplayFeed, ctx: QueryLoopState
) -> tuple[MeasuredCell | None, HeldLock | None]:
    waiting: asyncio.Task[None] | None = None
    try:
        while True:
            claim = feed.claim(sample.key)
            if claim is not None:
                try:
                    ctx.advance()
                except BaseException:
                    claim.retire()
                    raise
                if (row := ctx.replay(sample)) is None:
                    return None, claim
                claim.retire()
                return row, None
            if waiting is None:
                waiting = asyncio.create_task(
                    heartbeat(
                        CellBeat(sample.id),
                        round_num=current_round(),
                        start_monotonic=time.monotonic(),
                        detail_fn=lambda: "another run is measuring this cell",
                    )
                )
            if ctx.session.state.control.pause_requested():
                raise asyncio.CancelledError("claim wait aborted by a pause")
            await asyncio.sleep(_CLAIM_POLL_S)
    finally:
        if waiting is not None:
            waiting.cancel()


async def _acquire(sample: Sample, ctx: QueryLoopState, claiming: set[int]) -> _Acquired:
    cached = ctx.replay(sample)
    claim: HeldLock | None = None
    if cached is None and (feed := ctx.feed) is not None:
        claiming.add(sample.id)
        try:
            cached, claim = await _claim_cell(sample, feed, ctx)
        finally:
            claiming.discard(sample.id)
    if cached is not None:
        cached_r = await _maybe_recover_degraded(cached.replayed(), sample, ctx)
        return _Acquired(sample=sample, result=ctx.scorer.grade(cached_r), fresh=False)

    deprecated_display: GradedCell | None = None
    if (cached_deprecated := ctx.deprecated(sample)) is not None:
        # Graded here, rendered at the take: a display call from a cell prints out of walk order.
        deprecated_display = ctx.scorer.grade(cached_deprecated.replayed())

    try:
        result = await measure_sample(sample, ctx.session, ctx.search_point.pipeline_params)
        result = await _maybe_recover_degraded(result, sample, ctx)
        if cached_deprecated is not None:
            result = replace(result, retry_of_deprecated_cache=True)
        graded = ctx.scorer.grade(result)
        if claim is not None:
            # The holder files the answer under its claim: the waiter it released replays it.
            graded = ctx.recorder.take(graded)
    finally:
        if claim is not None:
            claim.retire()
    return _Acquired(
        sample=sample,
        result=graded,
        fresh=True,
        deprecated_display=deprecated_display,
    )


LaunchEvent = tuple["Sample", LookaheadWindow]


@dataclass
class Walk:
    """A cell frees its slot when it RETURNS, not when taken; no walk reads another's state across an await."""

    # Every row the walk may take, in the order it takes them; its replay state covers them all.
    dataset: list[Sample]
    # How many of them it takes.
    budget: int
    ctx: QueryLoopState
    checks: Sequence[StopRule]
    # Copied once per cell: a cell's throttle give-back and envelope deadline bind to its task.
    context: contextvars.Context
    # Rows an operator's skip let this walk take, replayed on resumption.
    skip_at: int | None = None
    results: list[GradedCell] = field(default_factory=list)
    consecutive_errors: int = 0
    # Each cell out, then back, by the row it takes: a block's open re-seats it where its row moved.
    running: dict[int, asyncio.Task[_Acquired]] = field(default_factory=dict)
    # Sample ids of running cells still on another process's claim: every stop cancels them.
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
        return self.budget

    def enter_block(self, end: int) -> None:
        self.boundary = min(self.n, end)

    @property
    def waits(self) -> bool:
        """Handed no call this block, a row still left to it: it waits out the block's close."""
        return (close := self.boundary) is not None and len(self.results) == close < self.n

    def reorder(self, rows: Sequence[Sample]) -> None:
        """Its untaken pool in the order its race chose. A cell out or back moves with its row, by
        sample id: the choice drops no row from the pool, so a press re-buys and replays none."""
        head = len(self.results)
        # By slot, never by key: two rows of one content share a key.
        assert sorted(s.id for s in rows) == sorted(s.id for s in self.dataset[head:]), (
            "a race chose rows outside the walk's untaken pool"
        )
        seat = {sample.id: head + k for k, sample in enumerate(rows)}
        row = self.dataset
        self.running = {seat[row[i].id]: cell for i, cell in self.running.items()}
        self.finished = {seat[row[i].id]: cell for i, cell in self.finished.items()}
        # A taken row keeps its own: a settling cell reads it.
        self.launched_at = {
            seat[row[i].id] if i >= head else i: at for i, at in self.launched_at.items()
        }
        # A copy: walks share the list they were opened on.
        self.dataset = [*row[:head], *rows]

    def unsent(self, cells: int) -> list[int]:
        """The rows it may still send, in the order it takes them: none taken, out or back, and
        at most *cells* out or back past its pending close, so a cut there wastes them at most."""
        end, out = self.n if self.skip_at is None else self.skip_at, {*self.running, *self.finished}
        rows = [i for i in range(len(self.results), end) if i not in out]
        if (close := self.boundary) is None:
            return rows
        past = max(cells - sum(i >= close for i in out), 0)
        return [i for i in rows if i < close] + [i for i in rows if i >= close][:past]

    def sent(self) -> list[Sample]:
        """The rows it took, then each one a cell is out or back on, in the order it takes them."""
        out = sorted({*self.running, *self.finished})
        return [*self.dataset[: len(self.results)], *(self.dataset[i] for i in out)]

    def launch(self, armed: LookaheadWindow) -> tuple[Sample, asyncio.Task[_Acquired]]:
        idx = self.unsent(armed.cells)[0]
        sample = self.dataset[idx]
        # `armed` is the window the round RUNS at, never the in-flight count, empty at each candidate's start.
        event: LaunchEvent = (sample, armed)
        if self.held is not None:
            self.held.append(event)
        else:
            self._started(event)
        cell = asyncio.create_task(
            _acquire(sample, self.ctx, self.claiming),
            name=f"scoring:{sample.id}",
            context=self.context.copy(),
        )
        self.running[idx] = cell
        self.launched_at[idx] = time.time()
        return sample, cell

    def release(self) -> None:
        held, self.held = self.held or [], None
        for event in held:
            self._started(event)

    def _started(self, event: LaunchEvent) -> None:
        if (recorded := self.ctx.recorded) is not None:
            sample, armed = event
            append_record(
                SampleStartedRecord(
                    round=bound_round(),
                    individual_id=recorded.individual_id,
                    role=recorded.role,
                    sample_total=self.n,
                    position=sample.id,
                    query_preview=sample.query,
                    sample_lookahead=armed.cells,
                    sample_abreast=armed.abreast,
                )
            )

    def collect(self) -> None:
        for idx in [idx for idx, cell in self.running.items() if cell.done()]:
            self.finished[idx] = self.running.pop(idx)

    def head(self) -> asyncio.Task[_Acquired] | None:
        return None if self.settling is not None else self.finished.get(len(self.results))

    def take(self, cell: asyncio.Task[_Acquired]) -> QueryLoopResult | None:
        """The only writer of the rows; raises the cell's own error in its turn, never in its race."""
        acq = cell.result()
        idx = len(self.results)
        del self.finished[idx]
        ctx, n = self.ctx, self.n
        first = ctx.claim_price(acq.sample)
        if not acq.fresh:
            # Only if it STAYED a replay: the stale-data protocol may have re-measured for real.
            if acq.result.facts.cached and first:
                _emit_cached_step_tokens(acq.result.facts)
        elif acq.deprecated_display is not None:
            ctx.sample_scored(acq.deprecated_display, acq.sample, idx, n, running=None)

        # Before the snapshot below, which names the cell by its answer's address.
        taken = ctx.recorder.take(acq.result)
        self.results.append(taken)
        running = ctx.running(self.results)

        # Asked of every row, replays included: the stale-data protocol re-measures a replayed cell.
        category = taken.facts.error_category
        if category is not None and (stop := ERROR_CATEGORY_INFO[category].stops) is not None:
            raise StopLoop(
                stop, unmeasured=n - len(self.results) + 1, detail=taken.facts.error or ""
            )

        if acq.fresh:
            if taken.facts.errored:
                if reason := self._abort_reason(taken):
                    # The untouched tail is counted, never written as rows, which would keep the denominator full.
                    return QueryLoopResult(self.results, ended_on=reason)
            else:
                self.consecutive_errors = 0

        ctx.sample_scored(taken, acq.sample, idx, n, running=running)
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
        """Index ``i`` may launch while no rule can cut before ``i`` rows, so a rule's cut wastes one
        cell at most. A block's close bounds no launch: a cut there wastes the press at most."""
        return self.horizon(last - 2)

    def horizon(self, last: int) -> int | None:
        """The fewest rows at which a stop rule may cut, read off the rows up to index *last*."""
        head = len(self.results)
        if last < head:
            return None
        upcoming = [
            (self.dataset[i], _returned_row(cell) if (cell := self.finished.get(i)) else None)
            for i in range(head, last + 1)
        ]
        stops = [check.earliest_stop(self.results, upcoming) for check in self.checks]
        return min((m for m in stops if m is not None), default=None)

    def rereads_ahead(self, start: int) -> int:
        end = start
        while end < self.n and self.ctx.rereads(self.dataset[end]):
            end += 1
        return end - start

    def sendable(self, cells: int, room: int = sys.maxsize) -> list[int]:
        """The rows it may send now, at most *room*: unsent at a press of *cells*, and none where a
        rule's cut would waste more than one."""
        if not (sending := self.unsent(cells)[: max(room, 0)]):
            return []
        horizon = self.launch_horizon(sending[-1])
        return [i for i in sending if horizon is None or i <= horizon]

    def flight(self, cells: int) -> Flight:
        """``allowed`` is what ``fill`` may have out at a press of *cells*: one bound for both."""
        out, settled = len(self.running), len(self.results) + len(self.finished)
        return Flight(out, out + len(self.sendable(cells, cells - out)), self.n - settled)

    def bank(self) -> list[Sample]:
        """Only cells this walk was SURE to take: below its horizon, its block race's pending close
        and the first error."""
        self.collect()
        sure = min(m for m in (self.n, self.horizon(self.n - 1), self.boundary) if m is not None)
        rows: list[GradedCell] = []
        paid: list[Sample] = []
        samples: list[Sample] = []
        for idx in sorted(self.finished):
            if idx >= sure:
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
                self.ctx.claim_price(sample)
        return samples

    def end(
        self, outcome: QueryLoopResult | None, *, cancel: bool
    ) -> list[asyncio.Task[_Acquired]]:
        """A cell in flight or returned-not-taken is DISCARDED: written, the rows depend on in-flight depth."""
        self.outcome = outcome
        for cell in self.finished.values():
            _quiet(cell)
        self.drop_claim_waits()
        draining = list(self.running.values())
        for cell in draining:
            if cancel:
                cell.cancel()
            cell.add_done_callback(_quiet)
        self.running, self.finished = {}, {}
        return draining

    def drop_claim_waits(self) -> None:
        for idx, cell in self.running.items():
            if self.dataset[idx].id in self.claiming:
                cell.cancel()


async def run_walks[K: Hashable](
    walks: Mapping[K, Walk | None],
    session: Session,
    *,
    keep_cut: bool,
    backfills: CatchUpPool | None = None,
    blocks: BlockRace[K] | None = None,
    on_turn: Callable[[K, int | None], None] | None = None,
    on_decided: Callable[[K], None] | None = None,
) -> StopLoop | None:
    """Rows, cuts and events land where a SERIAL phase lands them, in *walks*' order; ``keep_cut``
    returns a budget stop, its words whole, not raises."""
    if over := [w.n for w in walks.values() if w is not None and w.n > MAX_PASS_ROWS]:
        raise PayloadInvalidError(
            f"A pass scores at most {MAX_PASS_ROWS} rows, and this one asks for {max(over)}. "
            "Cut the set where it is defined: the dataset's split or the pass's sample size.",
            code="pass_too_large",
        )
    phase = _ScoringPhase(
        list(walks), list(walks.values()), session, backfills, blocks, on_turn, on_decided
    )
    phase.cell = _dearest(
        [
            await cell_bound(session, walk.ctx.search_point.pipeline_params)
            for walk in walks.values()
            if walk is not None
        ]
    )
    if phase.gauge is not None:
        phase.gauge.open(phase.reading)
    with session.backend_client.sending(phase):
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
    tripped: StopLoop | None
    keeping: bool

    @property
    def stopping(self) -> bool:
        return self.skip or self.pause or self.tripped is not None


# By identity: the backend counts each phase sending on its machine once.
@dataclass(eq=False)
class _ScoringPhase[K: Hashable]:
    # By turn: the walk at index ``i`` is the one keyed ``keys[i]``.
    keys: list[K]
    walks: list[Walk | None]
    session: Session
    backfills: CatchUpPool | None
    blocks: BlockRace[K] | None
    on_turn: Callable[[K, int | None], None] | None
    on_decided: Callable[[K], None] | None
    cell: SendBound | None = None
    turn: int = -1
    block: int = 0
    draining: set[asyncio.Future[Any]] = field(default_factory=set)
    announced: set[int] = field(default_factory=set)
    decided: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.gauge = self.session.state.flight
        self.client = self.session.backend_client
        self.cancels = self.client.connector.cancel_stops_billing
        self.payer = self.client.connector.cell_payer
        self.ceiling = self.client.connector.max_cells_in_flight
        self.book = run_scope().book
        self.label = filed(CELL)
        # A pass the book's ceilings do not meter is neither admitted against them nor stopped by them.
        self.unbounded = self.book is not None and not self.book.binds(self.label.kind)

    def affordable(self) -> int:
        # Calls out are counted here, not read off the book: a cell launched this step holds nothing yet.
        book, cell = self.book, self.cell
        if book is None or cell is None or self.unbounded:
            return sys.maxsize
        return book.fits(cell, paid_by=self.payer, kind=self.label.kind) - self.out()

    def racing(self) -> dict[int, Walk]:
        return {
            i: walk
            for i, walk in enumerate(self.walks)
            if walk is not None and walk.outcome is None
        }

    def live(self) -> list[Walk]:
        return list(self.racing().values())

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

    def open_block(self) -> None:
        """Each live walk's next stretch: the order its race takes its untaken pool in, and the
        row its block closes at."""
        live = {self.keys[i]: walk for i, walk in self.racing().items()}
        if self.blocks is None or not live:
            return
        chosen = self.blocks.choose_rows(
            {key: walk.results for key, walk in live.items()},
            {key: walk.dataset[len(walk.results) :] for key, walk in live.items()},
        )
        for key, walk in live.items():
            walk.reorder(chosen[key].rows)
            walk.enter_block(len(walk.results) + chosen[key].calls)
        assert any(not walk.waits for walk in live.values()), "a block handed out no call"

    def announce(self, i: int) -> None:
        self.announced.add(i)
        if self.on_turn is not None:
            self.on_turn(self.keys[i], None if self.blocks is None else self.block)

    def conclude(self, i: int) -> None:
        self.decided.add(i)
        if self.on_decided is not None:
            self.on_decided(self.keys[i])

    def first_turn(self) -> bool:
        self.open_block()
        return self.advance()

    def advance(self) -> bool:
        while True:
            self.turn += 1
            if self.turn >= len(self.walks):
                if self.blocks is None or not self.close_block():
                    return False
                self.turn = 0
            walk = self.walks[self.turn]
            # A block race turns every live walk handed a call once per block; a decided one had
            # its last turn.
            if (walk is None and self.block > 0) or (
                walk is not None and (walk.outcome is not None or walk.waits)
            ):
                continue
            self.announce(self.turn)
            if walk is not None:
                walk.release()
                if walk.n:
                    return True
                walk.end(QueryLoopResult([]), cancel=self.cancels)
            self.conclude(self.turn)

    def close_block(self) -> bool:
        assert self.blocks is not None
        racing = self.racing()
        stops = (
            self.blocks.close({self.keys[i]: walk.results for i, walk in racing.items()})
            if racing
            else {}
        )
        for i, walk in racing.items():
            if (signal := stops.get(self.keys[i])) is not None:
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
        self.open_block()
        return bool(self.live())

    async def step(self) -> bool:
        walk = self.walks[self.turn]
        assert walk is not None
        if self.gauge is not None:
            self.gauge.touch()
        armed = _armed(self.session, walk.ctx.kind)
        if walk.skip_at is not None and len(walk.results) >= walk.skip_at:
            return self.decide(walk, QueryLoopResult(walk.results, ended_on=WalkEnd.SKIP))
        now = self.turn_state(walk)
        if now.stopping and (now.cut or not now.keeping):
            return self.honour_stop(walk, now)
        if now.settled and now.settle is not None and self.backfills is not None:
            walk.settling = None
            self.caught_up(walk, self.backfills.commit_backfills(now.settle))
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
        skip = session.state.control.skip_requested()
        pause = cut or session.state.control.pause_requested()
        tripped = (
            None
            if self.book is None or self.unbounded or walk.rereads_ahead(len(walk.results))
            else budget_stop(self.book)
        )
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
            if session.state.control.spend_skip():
                # Stamped where a searchpoint is actually cut, never at the press.
                session.act(ArmAct.SKIP)
            return self.decide(walk, QueryLoopResult(walk.results, ended_on=WalkEnd.SKIP))
        if now.pause or now.tripped is None:
            raise KeyboardInterrupt("graceful")
        raise now.tripped

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

    def held(self, walk: Walk) -> int:
        """A walk's own calls out: its cells, and the catch-ups started on its cells not yet settled."""
        out = len(walk.running)
        if self.backfills is not None:
            for sample in walk.sent()[max(0, len(walk.results) - 1) :]:
                out += sum(not call.done() for call in self.backfills.backfills_for(sample))
        return out

    def others(self) -> int:
        """Calls out on this machine that every other run's phase holds."""
        return self.client.cells_out() - self.out()

    def room(self, walk: Walk, armed: LookaheadWindow, ceiling: int) -> int:
        return min(armed.cells - self.held(walk), self.client.room(self) - (self.ceiling - ceiling))

    def abreast(self, armed: LookaheadWindow) -> list[Walk]:
        """The live walks that send ahead, counted from the turn's in walk order."""
        n = len(self.walks)
        turning = [
            walk
            for k in range(n)
            if (walk := self.walks[(self.turn + k) % n]) is not None and walk.outcome is None
        ]
        return turning if armed.abreast == "all" else turning[: armed.abreast]

    def launch(self, walk: Walk, armed: LookaheadWindow) -> None:
        if self.backfills is not None:
            room = min(self.room(walk, armed, self.ceiling), self.affordable())
            owing = [walk.settling] if walk.settling is not None else []
            owing += [walk.dataset[idx] for idx in sorted(walk.finished)]
            for sample in owing:
                room -= len(self.backfills.start_backfill(sample, room))
        for ahead in self.abreast(armed):
            # A walk behind the turn leaves it one slot, so its next cell never waits on another's.
            self.fill(ahead, armed, self.ceiling if ahead is walk else self.ceiling - 1)

    def fill(self, walk: Walk, armed: LookaheadWindow, ceiling: int) -> None:
        """Up to the press, past a pending block close too, on the rows its last open chose."""
        backfills = self.backfills
        if not (unsent := walk.unsent(armed.cells)):
            return
        room = min(
            self.room(walk, armed, ceiling),
            max(self.affordable(), walk.rereads_ahead(unsent[0])),
        )
        for _ in walk.sendable(armed.cells, room):
            if room <= 0:
                break
            sample, _cell = walk.launch(armed)
            room -= 1
            if backfills is not None:
                room -= len(backfills.start_backfill(sample, min(room, self.affordable())))

    async def pressed(self, armed: LookaheadWindow, kind: MeasurementRole) -> None:
        """Polled: the press is a command another process records on the ledger, which no signal carries."""
        while _armed(self.session, kind) == armed:  # noqa: ASYNC110 — a cross-process ledger wait
            await asyncio.sleep(HELD_POLL_S)

    async def freed(self) -> None:
        """Polled: another run's cell lands on its own task, which this phase does not wait on."""
        while self.client.room(self) <= 0:  # noqa: ASYNC110 — another run's calls
            await asyncio.sleep(HELD_POLL_S)

    async def wait(self, walk: Walk, armed: LookaheadWindow) -> None:
        book, cell = self.book, self.cell
        calls = self.outstanding()
        full = self.client.room(self) <= 0
        if not calls and not full:
            if book is not None and cell is not None and self.affordable() == 0:
                # Raises the refusal `hold` would, naming the ceiling that binds, and holds nothing.
                book.refuse_unless_room((cell, cell), self.label.kind, NEXT_CELL, self.payer)
            raise RuntimeError(
                f"scoring phase stalled: nothing out and nothing to take at query "
                f"{len(walk.results)}/{walk.n}"
            )
        watched = set[asyncio.Future[Any]](calls)
        polls: list[asyncio.Future[None]] = []
        if armed != effective_lookahead(MAX_SPEED, self.ceiling):
            polls.append(asyncio.ensure_future(self.pressed(armed, walk.ctx.kind)))
        if full and self.others():
            polls.append(asyncio.ensure_future(self.freed()))
        watched.update(polls)
        try:
            await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for poll in polls:
                poll.cancel()
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

    def caught_up(self, walk: Walk, caught: CaughtUp | None) -> None:
        """A catch-up walk records no pass, so its cells reach the ledger here or nowhere."""
        if caught is None:
            return
        arm = walk.ctx.recorded
        assert arm is not None, "a race settles arms, and every arm's pass is recorded"
        by_cell: dict[str, dict[ArmKey, WalkedCell]] = {}
        for prior, cells in caught.cells.items():
            for cell in cells:
                if (address := cell.address) is not None:
                    by_cell.setdefault(cell.key, {})[prior] = address
        if not by_cell:
            return
        position_of = walk.ctx.session.bank.position_of
        append_record(
            *(
                RaceCatchUpRecord(
                    round=bound_round(),
                    arm=ArmKey(arm.individual_id),
                    member=caught.member,
                    sample_key=key,
                    position=position_of[key],
                    cells=taken,
                )
                for key, taken in by_cell.items()
            )
        )

    def bank(self) -> None:
        for walk in self.live():
            try:
                sure = walk.bank()
                if self.backfills is not None:
                    self.caught_up(
                        walk,
                        self.backfills.bank_backfills(
                            [*sure, walk.settling] if walk.settling is not None else sure
                        ),
                    )
            except (KeyboardInterrupt, asyncio.CancelledError, SendRefusedError):
                raise
            except Exception as fault:  # noqa: BLE001 -- the stop that ended the walk stands
                said = "a stopped walk's returned cells were not banked"
                append_record(
                    RoundWarningRecord.handled(
                        "cells_unbanked", fault, said=said, round=bound_round()
                    )
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
        backfills, book, cell = self.backfills, self.book, self.cell
        walking = self.live()
        parts = [walk.flight(_armed(self.session, walk.ctx.kind).cells) for walk in walking]
        winding = sum(1 for call in self.draining if not call.done())
        out_now = sum(p.out for p in parts) + winding
        allowed = sum(p.allowed for p in parts) + winding
        most = sum(p.most for p in parts) + winding
        if backfills is not None:
            catching = len(backfills.backfills_in_flight())
            samples = {s.id: s for walk in walking for s in walk.dataset[: walk.n]}
            launched = {s.id for walk in walking for s in walk.sent()}
            owed = {sid: backfills.owed_backfills(s) for sid, s in samples.items()}
            out_now += catching
            allowed += catching + sum(k for sid, k in owed.items() if sid in launched)
            most += catching + sum(owed.values())
        waiting = None
        if 0 <= self.turn < len(self.walks) and (holder := self.walks[self.turn]) is not None:
            head, settling = len(holder.results), holder.settling
            held = settling or (holder.dataset[head] if head in holder.running else None)
            # A walk writing no sample record holds no cell a reader can name.
            if held is not None and (recorded := holder.ctx.recorded) is not None:
                since = holder.launched_at[head if settling is None else head - 1]
                who = recorded.individual_id
                waiting = FlightWaiting(individual_id=who, position=held.id, since=since)
        # The machine's calls out, every run's on it: what its ceiling bounds.
        others = self.others()
        return Flight(
            out_now + others,
            allowed + others,
            most,
            waiting,
            affordable=(
                None
                if book is None or cell is None or self.unbounded
                else max(0, self.affordable())
            ),
            cell_usd=None if book is None or cell is None else cell.usd,
        )
