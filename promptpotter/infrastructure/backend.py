"""HTTP client for backend APIs — wire payloads + session lifecycle. Connector-agnostic;
per-connector adapters in `promptpotter.connectors`. API responses stored verbatim.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from filelock import BaseFileLock, FileLock, Timeout

from promptpotter.config.paths import default_jobs_dir
from promptpotter.infrastructure.docker_host import claim_machine, machine_step
from promptpotter.infrastructure.llm.rate_limit import (
    MAX_SEND_ATTEMPTS,
    Backpressure,
    get_abort_check,
    report_throttle_stall,
    wait_with_countdown,
)
from promptpotter.infrastructure.llm.spend_book import (
    Admission,
    Billed,
    CallLabel,
    SendBound,
    admitted,
    connection_broke,
    may_have_billed,
    never_sent,
    reserved,
)
from promptpotter.infrastructure.llm.telemetry import emit_backend_warning
from promptpotter.shared.errors import CellHaltedError, CellThrottledError, CellUnscoreableError

# HTTP timeout for /matches. Longer than the backend's own retries can run — a few provider
# attempts per LLM node, each on its own timeout — because a read timeout is terminal: the backend
# is still working and billing, so the cell is left unreported and never sent again.
QUERY_TIMEOUT: float = 600.0
# How long a cell waits out a backend it cannot connect to — a restart under a running campaign —
# before it is banked unreachable, which stops the walk.
BACKEND_OUTAGE_S: float = 600.0
_OUTAGE_POLL_S = 5.0

# The hold a whole cell is admitted on, and the settle that reads what it billed off the reply —
# ``None`` where the reply reports nothing, which leaves the cell unreported.
CellBilling = Callable[[dict[str, Any]], "list[Billed] | None"]
CELL = CallLabel("backend_cell", "backend")

if TYPE_CHECKING:
    from promptpotter.connectors.protocol import (
        Connector,
        InProcessRun,
        InProcessWorkload,
        PromptDelivery,
        SentSpendBound,
    )
    from promptpotter.domain.connector import (
        CellEnvelopeSeconds,
        ConnectorExecution,
        MeasuredUnit,
        SessionProtocol,
        WireAdapter,
    )
    from promptpotter.domain.pipeline_schema import NodeSpendBound, PipelineNode
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.value_tree import Delivery

logger = logging.getLogger(__name__)

__all__ = [
    "BackendClient",
    "build_backend_client",
]


def build_backend_client(
    connector: Connector, base_url: str, *, workload: InProcessWorkload
) -> BackendClient:
    """The ONE ``BackendClient`` construction — every wire fact comes off the connector. Transport, payload shape, session
    and credential are all per-backend, so they are read from the one place that declares them. *workload* is per-RUN."""
    return BackendClient(
        base_url,
        wire_adapter=connector.wire_adapter,
        session=connector.session_factory(),
        execution=connector.execution,
        in_process_run=connector.in_process_run,
        workload=workload,
        max_cells_in_flight=connector.max_cells_in_flight,
        holds_own_sends=connector.holds_own_sends,
        sent_spend_bound=connector.sent_spend_bound,
        cancel_stops_billing=connector.cancel_stops_billing,
        cell_envelope=connector.cell_envelope_s,
        measured_unit=connector.measured_unit,
        answer_key=connector.answer_key,
        prompt_delivery=connector.prompt_delivery,
        auth_token=connector.auth_token() if connector.auth_token else None,
        machine_slots=(
            MachineSlots(
                default_jobs_dir() / "machine" / connector.name,
                connector.max_cells_in_flight,
                compose_overlay=connector.compose_overlay,
            )
            if connector.cells_hold_the_machine
            else None
        ),
    )


def _reply_data(resp: httpx.Response) -> dict[str, Any]:
    """The ``data`` a reply carries — on success, and on an error envelope reporting what the
    failed request billed before it failed."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else {}


def _settle(admission: Admission, reported: list[Billed] | None) -> None:
    if reported is None:
        admission.unreported()
    else:
        admission.settle(*reported)


def _settle_reply(admission: Admission, resp: httpx.Response, billed: CellBilling) -> None:
    reported = billed(_reply_data(resp))
    if reported is not None:
        admission.settle(*reported)
    elif resp.is_success or may_have_billed(resp.status_code):
        admission.unreported()
    else:
        admission.release()


@dataclass
class _CellSends:
    query: str
    attempt: int = 0
    recovered: bool = False
    down_since: float | None = None

    def warn(self, kind: str, *, wait_s: float, **extra: Any) -> None:
        # Emits straight to the ledger: the in-process arm takes no callback to thread through.
        emit_backend_warning(
            kind=kind,
            attempt=self.attempt + 1,
            max_attempts=MAX_SEND_ATTEMPTS,
            wait_s=float(wait_s),
            query=self.query,
            **extra,
        )

    def wait_after_break(self, exc: httpx.TransportError) -> float | None:
        resend = connection_broke(exc) and self.attempt + 1 < MAX_SEND_ATTEMPTS
        wait = float(2**self.attempt) if resend else None
        self.warn(
            "transport_error",
            wait_s=wait or 0.0,
            error_class=exc.__class__.__name__,
            final=not resend,
        )
        return wait


# How often a cell waiting on the machine looks for a free slot.
_MACHINE_POLL_S = 0.5


class MachineSlots:
    """The machine's slots for cells that hold it (``Connector.cells_hold_the_machine``) — ONE pool
    for every run on the box, one OS lock file per slot in the machine-global jobs dir. The kernel
    drops a lock with its holder, so a crashed run frees its slots without a heartbeat. A cell
    waits for one like it waits out the provider's pushback: tick by tick, breaking on a pause,
    reported as stall so its wall-clock envelope gives the wait back.

    **A slot is taken only by the cell holding the TURN**, one more lock every cell passes through
    and a waiting cell keeps until a slot is its own. Without it the pool belongs to whichever run
    filled it: that run's next cell asks the instant one lands, a waiting run asks on its next tick,
    so a run armed to the pool's depth holds every slot until its round ends.

    **Holding a slot is what claims the machine** (``docker_host.claim_machine``), so no connector
    can run a container cell this process has not put its producer lock and sweep behind."""

    def __init__(self, root: Path, capacity: int, *, compose_overlay: Path | None) -> None:
        self._root = root
        self._capacity = capacity
        self._compose_overlay = compose_overlay

    def _lock(self, name: str) -> BaseFileLock | None:
        held = FileLock(str(self._root / f"{name}.lock"), timeout=0)
        try:
            held.acquire()
        except Timeout:
            return None
        return held

    def _take(self) -> BaseFileLock | None:
        for i in range(self._capacity):
            if (slot := self._lock(str(i))) is not None:
                return slot
        return None

    async def _wait(self, take: Callable[[], BaseFileLock | None]) -> BaseFileLock:
        abort = get_abort_check()
        while (held := take()) is None:
            if abort is not None and abort():
                raise asyncio.CancelledError("machine-slot wait aborted")
            started = time.monotonic()
            await asyncio.sleep(_MACHINE_POLL_S)
            report_throttle_stall(time.monotonic() - started)
        return held

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        self._root.mkdir(parents=True, exist_ok=True)
        with machine_step():
            await claim_machine(compose_overlay=self._compose_overlay)
        turn = await self._wait(lambda: self._lock("turn"))
        try:
            slot = await self._wait(self._take)
        finally:
            turn.release()
        try:
            yield
        finally:
            slot.release()


class BackendClient:
    """Async HTTP client. `wire_adapter` + `session` are connector-specific and required at construction."""

    def __init__(
        self,
        base_url: str,
        *,
        wire_adapter: WireAdapter,
        session: SessionProtocol,
        execution: ConnectorExecution = "remote_http",
        in_process_run: InProcessRun | None = None,
        workload: InProcessWorkload,
        max_cells_in_flight: int = 2,
        holds_own_sends: bool = False,
        sent_spend_bound: SentSpendBound | None = None,
        cancel_stops_billing: bool = False,
        cell_envelope: CellEnvelopeSeconds | None = None,
        measured_unit: MeasuredUnit = "sample",
        answer_key: str | None = None,
        prompt_delivery: PromptDelivery,
        timeout: float = 30.0,
        auth_token: str | None = None,
        machine_slots: MachineSlots | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._wire_adapter: WireAdapter = wire_adapter
        self._guard: SessionProtocol = session
        # The connector's declared execution mode. ``run_query`` dispatches on
        # this — not the connector name — so a new backend's transport is a
        # declared capability, not a core-loop branch.
        self._execution: ConnectorExecution = execution
        # The non-HTTP execution arm, supplied by an ``in_process`` connector, and what this run's
        # backend runs against.
        self._in_process_run: InProcessRun | None = in_process_run
        self.workload: InProcessWorkload = workload
        # What one sample COSTS, which the transport above does not answer — two `in_process`
        # connectors want opposite depths.
        self._max_cells_in_flight = max_cells_in_flight
        self.holds_own_sends = holds_own_sends
        self._sent_spend_bound = sent_spend_bound
        self.cancel_stops_billing = cancel_stops_billing
        self._cell_envelope: CellEnvelopeSeconds | None = cell_envelope
        self._measured_unit: MeasuredUnit = measured_unit
        self._prompt_delivery: PromptDelivery = prompt_delivery
        # Where this backend's answer TEXT lives, when it emits one outside a ranking.
        self._answer_key: str | None = answer_key
        self._auth_token = auth_token or ""
        self._http: httpx.AsyncClient | None = None
        # Every cell of the run answers one provider pushback together (`run_query`).
        self.backpressure = Backpressure("cells")
        # Every run on the machine shares these, where a cell holds the machine itself.
        self._machine_slots = machine_slots

    @property
    def derives_spend_bounds(self) -> bool:
        """Whether what a node run can bill is derived from the config sent to it
        (``Connector.sent_spend_bound``) rather than served by the backend."""
        return self._sent_spend_bound is not None

    def node_spend_bound(self, node: PipelineNode, cfg: Mapping[str, Any]) -> NodeSpendBound | None:
        """What one run of ``node`` can bill: derived from the config sent to it where the
        connector declares that, else what the backend served."""
        if self._sent_spend_bound is not None:
            return self._sent_spend_bound(node.name, cfg)
        return node.spend_bound

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            headers = {"Authorization": f"Bearer {self._auth_token}"} if self._auth_token else None
            self._http = httpx.AsyncClient(timeout=self.timeout, headers=headers)
        return self._http

    @property
    def http(self) -> httpx.AsyncClient:
        """Public accessor for the shared httpx client — for init-side helpers that need the same authenticated client without
        round-tripping through ``BackendClient``'s own methods."""
        return self._get_http()

    @property
    def execution(self) -> ConnectorExecution:
        """The connector's declared transport — asked by callers that must know whether a query is a
        network round trip or work this process does itself."""
        return self._execution

    @property
    def max_cells_in_flight(self) -> int:
        return self._max_cells_in_flight

    def cell_envelope_s(
        self, sample: Sample, pipeline_params: dict[str, Any] | None
    ) -> float | None:
        """Seconds this cell may spend, or ``None`` where the backend declares no bound — see
        :attr:`Connector.cell_envelope_s`. A method, not a property: it is resolved per cell."""
        return None if self._cell_envelope is None else self._cell_envelope(sample, pipeline_params)

    def prompt_delivery(self, pipeline_params: dict[str, Any] | None) -> Delivery:
        """The channel the candidate's prompt travels under these params, so
        ``PipelineSchema.value_tree`` can say whether a value being optimized can even arrive."""
        return self._prompt_delivery(pipeline_params)

    @property
    def measured_unit(self) -> MeasuredUnit:
        return self._measured_unit

    @property
    def answer_key(self) -> str | None:
        """The ``data`` key holding the cell's answer text, or ``None`` where the terminal ranking
        is the only source — see :attr:`Connector.answer_key`."""
        return self._answer_key

    async def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"params": params} if params else {}
        resp = await self._get_http().get(
            f"{self.base_url}{path}",
            **kwargs,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def aclose(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    # -- status check -------------------------------------------------------

    async def check_status(self) -> dict[str, Any]:
        """GET /status. Failure returns `{status: not_implemented|unreachable|error, error: ...}` dict."""
        try:
            resp = await self._get_http().get(f"{self.base_url}/status")
            resp.raise_for_status()
            status: dict[str, Any] = resp.json()
            return status
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.info("Backend /status endpoint not found (404)")
                return {
                    "status": "not_implemented",
                    "error": "GET /status not available on this backend",
                }
            logger.warning("Backend status check failed: %s", exc)
            return {"status": "error", "error": str(exc)}
        except httpx.ConnectError as exc:
            logger.warning("Backend unreachable: %s", exc)
            return {"status": "unreachable", "error": str(exc)}
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("Backend status check failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    async def _reachable_within(self, down_since: float) -> bool:
        """Probe ``GET /status`` until the backend answers; ``False`` once it has been unreachable
        for :data:`BACKEND_OUTAGE_S`. Reported as stall, so the cell's envelope gives it back."""
        abort = get_abort_check()
        while time.monotonic() - down_since < BACKEND_OUTAGE_S:
            if abort is not None and abort():
                raise asyncio.CancelledError("backend outage wait aborted")
            started = time.monotonic()
            await asyncio.sleep(_OUTAGE_POLL_S)
            answered = (await self.check_status()).get("status") != "unreachable"
            report_throttle_stall(time.monotonic() - started)
            if answered:
                return True
        return False

    # -- pipeline config ---------------------------------------------------

    async def fetch_pipeline(self) -> dict[str, Any]:
        return await self._get_json("/pipeline")

    # -- replay operations ------------------------------------------------

    async def init_session(self, terms: list[str]) -> dict[str, Any]:
        """POST /sessions. Idempotent; guard stashes terms so `run_query` auto-recovers on restart."""
        return await self._guard.set_terms(self._get_http(), self.base_url, terms)

    async def run_query(
        self,
        sample: Sample,
        pipeline_params: dict[str, Any] | None = None,
        *,
        bound: SendBound | None,
        billed: CellBilling,
    ) -> dict[str, Any]:
        """One cell — POST /matches, or the in-process arm — held whole at ``bound`` against the
        run's spend book and settled off what the reply says it billed (``billed`` reads a reply's
        ``data``). Where the backend's own sends are each admitted as they are made
        (``Connector.holds_own_sends``) the cell only RESERVES ``bound``, and ``bound`` is ``None``
        where nothing bounds it. A request the backend may still be working is never sent
        again: only a throttle, a 5xx, a connection never made or broken before any reply
        (``connection_broke``, the rule our own sends retry on) and a lost session are — a throttle
        whenever the run's :attr:`backpressure` lets it, a connection never made until the backend
        answers within :data:`BACKEND_OUTAGE_S`, the rest a bounded number of times, each landing
        on the ledger through :func:`emit_backend_warning`. A 5xx whose body says a resend ends the
        same way is never sent again either: the cell is HALTED (:class:`CellHaltedError`)."""
        query = sample.query
        payload = self._wire_adapter(query, pipeline_params)

        if self._execution != "remote_http":
            return await self._in_process_until_admitted(
                sample, payload, bound=bound, billed=billed
            )
        if bound is None:
            raise RuntimeError("a remote cell is held whole, so it needs the bound its nodes serve")
        resp = await self._post_until_answered(query, payload, bound=bound, billed=billed)
        resp.raise_for_status()
        match_result: dict[str, Any] = resp.json()
        return match_result

    async def _in_process_until_admitted(
        self,
        sample: Sample,
        payload: dict[str, Any],
        *,
        bound: SendBound | None,
        billed: CellBilling,
    ) -> dict[str, Any]:
        # Declared-mode dispatch: a non-HTTP connector runs in this process via its own arm.
        # The registry guarantees the arm whenever the mode is ``in_process``.
        if self._in_process_run is None:
            raise RuntimeError(f"execution={self._execution!r} but no in_process_run wired")
        while True:
            # The provider's admission first: a cell held by its cooldown holds no machine slot
            # another run could use.
            machine = (
                self._machine_slots.hold()
                if self._machine_slots is not None
                else contextlib.nullcontext()
            )
            async with self.backpressure.send() as ticket, machine:
                try:
                    result = await self._in_process_cell(
                        self._in_process_run, sample, payload, bound=bound, billed=billed
                    )
                except CellThrottledError as exc:
                    self.backpressure.throttled(ticket, headers=None, body=str(exc))
                    continue
                self.backpressure.eased(ticket)
                return result

    async def _post_until_answered(
        self,
        query: str,
        payload: dict[str, Any],
        *,
        bound: SendBound,
        billed: CellBilling,
    ) -> httpx.Response:
        client = self._get_http()
        sends = _CellSends(query)
        # 429 → the run's backpressure; a connection never made → wait out the outage; a 5xx or a
        # connection broken before any reply → exp backoff (1, 2, 4, 8s); a lost session → one
        # recovery; everything else, a read timeout included, exits.
        while True:
            wait: float | None = None
            unreachable: httpx.TransportError | None = None
            async with self.backpressure.send() as ticket:
                with admitted(CELL, bound, model=None, provider=None) as admission:
                    try:
                        resp = await client.post(
                            f"{self.base_url}/matches",
                            json=payload,
                            timeout=QUERY_TIMEOUT,
                        )
                    except httpx.TransportError as exc:
                        if never_sent(exc):
                            admission.release()
                            unreachable = exc
                        # Left open, so each broken send stays held at its bound.
                        elif (wait := sends.wait_after_break(exc)) is None:
                            raise
                    else:
                        sends.down_since = None
                        _settle_reply(admission, resp, billed)
                        if resp.status_code == 429:
                            self.backpressure.throttled(
                                ticket, headers=resp.headers, body=resp.text
                            )
                            continue
                        if resp.is_success:
                            self.backpressure.eased(ticket)
                        wait = await self._resend_wait(client, resp, sends)
            if unreachable is not None:
                await self._wait_out_outage(unreachable, sends)
                continue
            if wait is None:
                return resp
            sends.attempt += 1
            if wait:
                await wait_with_countdown(wait, "backend")

    async def _resend_wait(
        self, client: httpx.AsyncClient, resp: httpx.Response, sends: _CellSends
    ) -> float | None:
        wait: float | None = None
        code = resp.status_code
        if 500 <= code < 600 and (refused := self._guard.resend_refused(resp)) is not None:
            raise CellHaltedError(f"HTTP {code} {refused}", spent={})
        if 500 <= code < 600 and sends.attempt + 1 < MAX_SEND_ATTEMPTS:
            wait = float(2**sends.attempt)
            logger.warning(
                "Backend %d (attempt %d/%d); waiting %.1fs",
                code,
                sends.attempt + 1,
                MAX_SEND_ATTEMPTS,
                wait,
            )
            sends.warn("server_error", wait_s=wait, status_code=code)
        elif (
            code == 400
            and not sends.recovered
            and await self._guard.recover(client, self.base_url, resp)
        ):
            sends.recovered = True
            wait = 0.0
        return wait

    async def _wait_out_outage(self, unreachable: httpx.TransportError, sends: _CellSends) -> None:
        error_class = unreachable.__class__.__name__
        if sends.down_since is None:
            sends.down_since = time.monotonic()
            logger.warning(
                "Backend unreachable (%s); waiting up to %.0fs for it to answer",
                error_class,
                BACKEND_OUTAGE_S,
            )
            sends.warn("transport_error", wait_s=BACKEND_OUTAGE_S, error_class=error_class)
        if not await self._reachable_within(sends.down_since):
            sends.warn("transport_error", wait_s=0.0, error_class=error_class, final=True)
            raise unreachable

    async def _in_process_cell(
        self,
        run: InProcessRun,
        sample: Sample,
        payload: dict[str, Any],
        *,
        bound: SendBound | None,
        billed: CellBilling,
    ) -> dict[str, Any]:
        """The arm's reply under its hold, with the cell's ONE clock on it: every second ``run``
        took, retries it made on its own included, on a reply and on a cell with no verdict alike."""
        with contextlib.ExitStack() as hold:
            admission: Admission | None = None
            if bound is not None and self.holds_own_sends:
                # Every send it makes is billed where it is made, so the cell is no send of its own.
                hold.enter_context(reserved(CELL, bound))
            elif bound is not None:
                admission = hold.enter_context(admitted(CELL, bound, model=None, provider=None))
            started = time.monotonic()
            try:
                result = await run(self.workload, sample, payload)
            except CellUnscoreableError as exc:
                # It ran to no verdict — a throttle included — and says what it paid for doing so.
                if admission is not None:
                    timings = dict.fromkeys(exc.spent, time.monotonic() - started)
                    spent = {"step_tokens": dict(exc.spent), "step_timings": timings}
                    _settle(admission, billed(spent))
                raise
            spent_s = time.monotonic() - started
            data: dict[str, Any] = result["data"]
            data["total_time"] = spent_s
            data["step_timings"] = {data["terminal_node"]: spent_s}
            if admission is not None:
                _settle(admission, billed(data))
            return result
