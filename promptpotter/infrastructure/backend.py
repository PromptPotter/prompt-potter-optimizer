from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from promptpotter.config.paths import MACHINE_DIR
from promptpotter.connectors.protocol import Connector, InProcessRun, InProcessWorkload, PricedPair
from promptpotter.domain.connector import BackendUnreachableError, ReplyReader, data_envelope
from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
from promptpotter.domain.pipeline_schema import NodeSpendBound, PipelineNode
from promptpotter.domain.records.provider import BackendWarningRecord
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import KeySource
from promptpotter.infrastructure.docker_host import claim_machine, machine_step
from promptpotter.infrastructure.llm import send_pacing
from promptpotter.infrastructure.llm.base import KEEPALIVE_OPTIONS
from promptpotter.infrastructure.llm.send_failure import FailedSendError, failed_send
from promptpotter.infrastructure.llm.send_pacing import (
    HELD_POLL_S,
    Backpressure,
    SendBudget,
    drawn_budget,
    loop_now,
    report_throttle_stall,
)
from promptpotter.infrastructure.llm.spend_book import (
    Admission,
    CallLabel,
    ReportedSpend,
    SendBound,
    SendOutcome,
    admitted,
    bound_account,
    replied,
    reserved,
)
from promptpotter.infrastructure.llm.telemetry import append_record
from promptpotter.infrastructure.llm.wallet import cells_paid_by
from promptpotter.infrastructure.run_scope import run_scope
from promptpotter.infrastructure.store.io import HeldLock, take_lock
from promptpotter.shared.errors import (
    CellThrottledError,
    CellUnscoreableError,
    ErrorCategory,
    PayloadInvalidError,
    cell_failure,
)
from promptpotter.shared.tls import tls_context

_OUTAGE_POLL_S = 5.0
# How long a cell waits for a backend that stopped answering; the cell's own clock may end it sooner.
_OUTAGE_WAIT_S = 600.0
_HTTP_TIMEOUT_S = 30.0
# Under uvicorn's 5 s `timeout_keep_alive`: at httpx's own 5 s a pooled socket the server is closing
# is reused, and the send breaks with `ReadError` 0-1 s after it went out.
_KEEPALIVE_EXPIRY_S = 2.0

# reply data -> (what it billed, `None` = reports nothing; caller's read).
type CellBilling[S] = Callable[[dict[str, Any]], tuple[ReportedSpend | None, S]]
# The 4xx a backend answers a model that generated nothing gradeable with: answered, so billed.
_ANSWERED_STATUSES = frozenset({422})
CELL = CallLabel("backend_cell", "backend")


logger = logging.getLogger(__name__)


def _reply_data(resp: httpx.Response, read: ReplyReader) -> dict[str, Any] | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    return read(body)


def _clock(data: dict[str, Any], spent_s: float) -> None:
    data["total_time"] = spent_s
    data["step_timings"] = {data["terminal_node"]: spent_s}


@dataclass
class CellSends:
    """One cell's resends, each warned on its ledger: a run the API server hosts has no console."""

    query: str
    budget: SendBudget
    recovered: bool = False
    down_since: float | None = None

    def warn(self, kind: str, *, wait_s: float, resending: bool = False, **extra: Any) -> None:
        append_record(
            BackendWarningRecord(
                kind=kind,
                attempt=self.budget.attempt - resending,
                max_attempts=self.budget.attempts,
                wait_s=float(wait_s),
                query=self.query,
                **extra,
            )
        )

    def wait_after(self, outcome: SendOutcome, cause: str) -> float | None:
        if not outcome.resendable:
            return None
        return self.budget.resend_wait(f"backend cell {self.query!r}", cause)


class MachineSlots:
    def __init__(self, jobs_dir: Path, name: str, capacity: int) -> None:
        self._jobs_dir = jobs_dir
        self._root = jobs_dir / MACHINE_DIR / name
        self._capacity = capacity

    def _lock(self, name: str) -> HeldLock | None:
        return take_lock(self._root / f"{name}.lock", wait_s=0)

    def _take(self) -> HeldLock | None:
        for i in range(self._capacity):
            if (slot := self._lock(str(i))) is not None:
                return slot
        return None

    async def _wait(self, take: Callable[[], HeldLock | None]) -> HeldLock:
        abort = run_scope().abort_check
        while (held := take()) is None:
            if abort is not None and abort():
                raise asyncio.CancelledError("machine-slot wait aborted")
            started = loop_now()
            await asyncio.sleep(HELD_POLL_S)
            report_throttle_stall(loop_now() - started)
        return held

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        with machine_step():
            claim_machine(self._jobs_dir)
        # One turn at a time: without it a run armed to the pool's depth holds every slot all round.
        turn = await self._wait(lambda: self._lock("turn"))
        try:
            slot = await self._wait(self._take)
        finally:
            turn.release()
        try:
            yield
        finally:
            slot.release()


class CellsOut(Protocol):
    def out(self) -> int: ...


# Every scoring phase this process holds, by the machine it sends to: the ceiling is the machine's,
# so the inner runs an outer holds at once share one count of it.
_SENDING: dict[tuple[str, str], set[CellsOut]] = {}


class BackendClient:
    def __init__(
        self,
        connector: Connector,
        *,
        endpoint: str | None,
        workload: InProcessWorkload,
        jobs_dir: Path,
    ) -> None:
        if (unset := connector.unconfigured(endpoint)) is not None:
            raise BackendUnreachableError(connector.name, "", unset)
        self._connector = connector
        self._endpoint = endpoint
        self.base_url = connector.base_url(endpoint)
        self._machine = (connector.name, self.base_url)
        self.workload = workload
        self.backpressure = Backpressure("cells")
        self._guard = connector.session_factory()
        self.http = httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT_S,
            headers=connector.credential_header() or None,
            transport=httpx.AsyncHTTPTransport(
                verify=tls_context(),
                socket_options=KEEPALIVE_OPTIONS,
                limits=httpx.Limits(keepalive_expiry=_KEEPALIVE_EXPIRY_S),
            ),
        )
        self._machine_slots = (
            MachineSlots(jobs_dir, connector.name, connector.max_cells_in_flight)
            if connector.cells_hold_the_machine
            else None
        )
        # A machine-held cell is counted once, by the pool every process shares: arming counts this
        # client's phases alone and each cell waits on its slot (`MachineSlots.hold`).
        self._phases: set[CellsOut] = (
            set() if self._machine_slots is not None else _SENDING.setdefault(self._machine, set())
        )

    @property
    def connector(self) -> Connector:
        return self._connector

    @contextlib.contextmanager
    def sending(self, phase: CellsOut) -> Iterator[None]:
        """*phase*'s calls out count against ``max_cells_in_flight`` beside every other phase on
        this machine while it sends; a machine-held connector's, beside this client's alone."""
        self._phases.add(phase)
        try:
            yield
        finally:
            self._phases.discard(phase)

    def cells_out(self) -> int:
        return sum(phase.out() for phase in self._phases)

    def room(self, phase: CellsOut) -> int:
        """The cells *phase* may still send: the ceiling past every phase's calls out, less one for
        each other phase holding none while *phase* holds some, or the first to send starves it."""
        waiting = sum(1 for other in self._phases if other is not phase and not other.out())
        return (
            self._connector.max_cells_in_flight - self.cells_out() - (waiting if phase.out() else 0)
        )

    def node_spend_bound(self, node: PipelineNode, cfg: NodeCall) -> NodeSpendBound | None:
        if (sent := self._connector.sent_spend_bound) is not None:
            return sent(node.name, cfg, self._connector.priced_as(cfg), self.workload)
        return node.spend_bound

    def cells_paid_by(self, *params: PipelineParams) -> KeySource:
        """Who pays the cells *params* send under the bound account (`wallet.py::cells_paid_by`)."""
        return cells_paid_by(
            bound_account("a backend's cells"),
            self._connector,
            (self._connector.priced_as(cfg)[1] for p in params for cfg in p.nodes.values()),
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _reachable_within(self, down_since: float) -> bool:
        abort = run_scope().abort_check
        while loop_now() - down_since < _OUTAGE_WAIT_S:
            if abort is not None and abort():
                raise asyncio.CancelledError("backend outage wait aborted")
            started = loop_now()
            await asyncio.sleep(_OUTAGE_POLL_S)
            answered = await self._connector.unreachable(self._endpoint) is None
            report_throttle_stall(loop_now() - started)
            if answered:
                return True
        return False

    async def fetch_pipeline(self) -> dict[str, Any]:
        """The declaration out of its ``data`` envelope. No answer or a 5xx (a tunnel up, its backend
        booting) is an outage; any other answer naming no ``nodes`` is the backend's refusal."""
        url = f"{self.base_url}/pipeline"
        try:
            resp = await self.http.get(url)
        except httpx.TransportError as exc:
            raise BackendUnreachableError(self._connector.name, self.base_url) from exc
        if resp.is_server_error:
            raise BackendUnreachableError(
                self._connector.name, self.base_url, f"GET {url} answered {resp.status_code}."
            )
        declared = _reply_data(resp, data_envelope) if resp.is_success else None
        if not declared or not isinstance(declared.get("nodes"), dict) or not declared["nodes"]:
            raise PayloadInvalidError(
                f"GET {url} answered {resp.status_code} with no pipeline declaration: the "
                f"backend's schema is its reply's `data` object, naming its `nodes`.",
                code="pipeline_config_invalid",
                details={"url": url, "status": resp.status_code},
            )
        return declared

    async def init_session(self, samples: Sequence[Sample], library: Sequence[str]) -> None:
        await self._guard.open(self.http, self.base_url, samples, library)

    async def run_query[S](
        self,
        sample: Sample,
        pipeline_params: PipelineParams,
        *,
        bound: SendBound | None,
        pairs: Mapping[str, PricedPair],
        billed: CellBilling[S],
    ) -> tuple[dict[str, Any], S]:
        """Never resends a request the backend may still be working: a read timeout is terminal."""
        query = sample.query
        payload = self._connector.wire_adapter(sample, pipeline_params)
        paid_by = self.cells_paid_by(pipeline_params)

        if self._connector.execution != "remote_http":
            return await self._in_process_until_admitted(
                sample, payload, bound=bound, pairs=pairs, billed=billed, paid_by=paid_by
            )
        if bound is None:
            raise RuntimeError("a remote cell is held whole, so it needs the bound its nodes serve")
        outcome, data, spent = await self._post_until_answered(
            query, payload, bound=bound, billed=billed, paid_by=paid_by
        )
        if outcome.failure is not None:
            raise FailedSendError(outcome)
        if data is None:
            raise ValueError(f"backend answered HTTP {outcome.status} with no answer to read")
        return data, spent

    async def _in_process_until_admitted[S](
        self,
        sample: Sample,
        payload: dict[str, Any],
        *,
        bound: SendBound | None,
        pairs: Mapping[str, PricedPair],
        billed: CellBilling[S],
        paid_by: KeySource,
    ) -> tuple[dict[str, Any], S]:
        run = self._connector.in_process_run
        if run is None:
            raise RuntimeError(
                f"execution={self._connector.execution!r} but no in_process_run wired"
            )
        while True:
            # Backpressure before the slot: a cell held by a cooldown must hold no machine slot.
            machine = (
                self._machine_slots.hold()
                if self._machine_slots is not None
                else contextlib.nullcontext()
            )
            async with self.backpressure.send() as ticket, machine:
                try:
                    result = await self._in_process_cell(
                        run,
                        sample,
                        payload,
                        bound=bound,
                        pairs=pairs,
                        billed=billed,
                        paid_by=paid_by,
                    )
                except CellThrottledError as exc:
                    self.backpressure.throttled(ticket, headers=None, body=str(exc))
                    continue
                self.backpressure.eased(ticket)
                return result

    async def _post_until_answered[S](
        self,
        query: str,
        payload: dict[str, Any],
        *,
        bound: SendBound,
        billed: CellBilling[S],
        paid_by: KeySource,
    ) -> tuple[SendOutcome, dict[str, Any] | None, S]:
        client = self.http
        sends = CellSends(query, drawn_budget())
        while True:
            wait: float | None = None
            unreachable: tuple[httpx.TransportError, SendOutcome] | None = None
            async with self.backpressure.send() as ticket:
                with admitted(CELL, bound, model=None, provider=None, paid_by=paid_by) as admission:
                    started = time.monotonic()
                    try:
                        # The cell's envelope and the run's wall clock end the wait, as a HALTED cut.
                        resp = await client.post(
                            f"{self.base_url}{self._connector.cell_path}",
                            json=payload,
                            timeout=None,
                        )
                    except httpx.TransportError as exc:
                        outcome = failed_send(exc)
                        admission.close(outcome)
                        if not outcome.sent:
                            unreachable = exc, outcome
                        else:
                            error_class = exc.__class__.__name__
                            wait = sends.wait_after(outcome, error_class)
                            sends.warn(
                                "transport_error",
                                wait_s=wait or 0.0,
                                resending=wait is not None,
                                error_class=error_class,
                                final=wait is None,
                            )
                            if wait is None:
                                raise FailedSendError(outcome) from exc
                    else:
                        sends.down_since = None
                        outcome = replied(
                            resp.status_code,
                            headers=resp.headers,
                            said=resp.text,
                            answered_on=_ANSWERED_STATUSES,
                        )
                        data = _reply_data(resp, self._connector.read_reply)
                        if self._connector.round_trip_is_latency and data and resp.is_success:
                            _clock(data, time.monotonic() - started)
                        reported, spent = billed(data or {})
                        outcome = outcome._replace(reported=reported)
                        admission.close(outcome)
                        if outcome.failure is ErrorCategory.PROVIDER_THROTTLED:
                            self.backpressure.throttled(
                                ticket, headers=outcome.headers, body=outcome.detail
                            )
                            continue
                        if outcome.failure is None:
                            self.backpressure.eased(ticket)
                        wait = await self._resend_wait(client, resp, outcome, sends)
            if unreachable is not None:
                await self._wait_out_outage(*unreachable, sends)
                continue
            if wait is None:
                return outcome, data, spent
            if wait:
                await send_pacing.held_wait(wait, "backend")

    async def _resend_wait(
        self,
        client: httpx.AsyncClient,
        resp: httpx.Response,
        outcome: SendOutcome,
        sends: CellSends,
    ) -> float | None:
        code = resp.status_code
        if outcome.resendable and (refused := self._guard.resend_refused(resp)) is not None:
            # The backend's own hole, never a bound of ours: a resume re-measures it.
            raise cell_failure(f"HTTP {code} {refused}", ErrorCategory.SERVER, spent={})
        if (wait := sends.wait_after(outcome, f"HTTP {code}")) is not None:
            sends.warn("server_error", wait_s=wait, resending=True, status_code=code)
        elif (
            code == 400
            and not sends.recovered
            and await self._guard.recover(client, self.base_url, resp)
        ):
            sends.recovered = True
            wait = 0.0
        return wait

    async def _wait_out_outage(
        self, unreachable: httpx.TransportError, outcome: SendOutcome, sends: CellSends
    ) -> None:
        error_class = unreachable.__class__.__name__
        if sends.down_since is None:
            sends.down_since = loop_now()
            logger.warning(
                "Backend unreachable (%s); waiting up to %.0fs for it to answer",
                error_class,
                _OUTAGE_WAIT_S,
            )
            sends.warn("transport_error", wait_s=_OUTAGE_WAIT_S, error_class=error_class)
        if not await self._reachable_within(sends.down_since):
            sends.warn("transport_error", wait_s=0.0, error_class=error_class, final=True)
            raise FailedSendError(outcome) from unreachable

    async def _in_process_cell[S](
        self,
        run: InProcessRun,
        sample: Sample,
        payload: dict[str, Any],
        *,
        bound: SendBound | None,
        pairs: Mapping[str, PricedPair],
        billed: CellBilling[S],
        paid_by: KeySource,
    ) -> tuple[dict[str, Any], S]:
        with contextlib.ExitStack() as hold:
            admission: Admission | None = None
            if bound is not None and self._connector.holds_own_sends:
                hold.enter_context(reserved(CELL, bound, paid_by=paid_by))
            elif bound is not None:
                admission = hold.enter_context(
                    admitted(CELL, bound, model=None, provider=None, paid_by=paid_by)
                )
            started = time.monotonic()
            try:
                result = await run(self.workload, sample, payload, pairs)
            except CellUnscoreableError as exc:
                # `spent is None`: nobody learned what it paid, so the whole bound stays held.
                if admission is not None:
                    reported = None
                    if exc.spent is not None:
                        timings = dict.fromkeys(exc.spent, time.monotonic() - started)
                        paid = {"step_tokens": dict(exc.spent), "step_timings": timings}
                        reported, _ = billed(paid)
                    admission.close(
                        SendOutcome(
                            sent=True, may_have_billed=True, reported=reported, failure=exc.category
                        )
                    )
                raise
            data: dict[str, Any] = result["data"]
            _clock(data, time.monotonic() - started)
            reported, spent = billed(data)
            if admission is not None:
                admission.close(SendOutcome(sent=True, may_have_billed=True, reported=reported))
            return data, spent
