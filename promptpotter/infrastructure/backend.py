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
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from filelock import BaseFileLock, FileLock, Timeout

from promptpotter.config.paths import default_jobs_dir
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
_CELL = CallLabel("backend_cell", "backend")

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
                default_jobs_dir() / "machine" / connector.name, connector.max_cells_in_flight
            )
            if connector.cells_hold_the_machine
            else None
        ),
    )


def _is_session_error(resp: httpx.Response) -> bool:
    """True when a 400 body signals a missing session (→ recover + retry). Accepts either the machine-readable ``no_session`` code or the
    word in the message, across BOTH error envelopes, so a backend reload self-heals instead of aborting the round."""
    try:
        body = resp.json()
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception:
        return False
    if not isinstance(body, dict):
        return False
    if body.get("code") == "no_session":
        return True
    text = " ".join(str(body.get(k, "")) for k in ("message", "detail", "error"))
    return "session" in text.lower()


def _resend_refused(resp: httpx.Response) -> str | None:
    """The backend's reason where its error body says a resend ends the same way
    (``detail.retryable: false`` — TermNorm's deadline on a provider request), else ``None``."""
    try:
        body = resp.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, dict) or detail.get("retryable") is not False:
        return None
    return f"{detail.get('error_code')}: {detail.get('message')}"


def _reply_data(resp: httpx.Response) -> dict[str, Any]:
    """The ``data`` a reply carries — on success, and on an error envelope reporting what the
    failed request billed before it failed."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else {}


def _spent_data(exc: CellUnscoreableError) -> dict[str, Any]:
    return {"step_tokens": dict(exc.spent), "step_timings": dict(exc.step_timings)}


def _settle(admission: Admission, reported: list[Billed] | None) -> None:
    if reported is None:
        admission.unreported()
    else:
        admission.settle(*reported)


# How often a cell waiting on the machine looks for a free slot.
_MACHINE_POLL_S = 0.5


class MachineSlots:
    """The machine's slots for cells that hold it (``Connector.cells_hold_the_machine``) — ONE pool
    for every run on the box, one OS lock file per slot in the machine-global jobs dir. The kernel
    drops a lock with its holder, so a crashed run frees its slots without a heartbeat. A cell
    waits for one like it waits out the provider's pushback: tick by tick, breaking on a pause,
    reported as stall so its wall-clock envelope gives the wait back."""

    def __init__(self, root: Path, capacity: int) -> None:
        self._root = root
        self._capacity = capacity

    def _take(self) -> BaseFileLock | None:
        self._root.mkdir(parents=True, exist_ok=True)
        for i in range(self._capacity):
            slot = FileLock(str(self._root / f"{i}.lock"), timeout=0)
            try:
                slot.acquire()
            except Timeout:
                continue
            return slot
        return None

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        abort = get_abort_check()
        while (slot := self._take()) is None:
            if abort is not None and abort():
                raise asyncio.CancelledError("machine-slot wait aborted")
            started = time.monotonic()
            await asyncio.sleep(_MACHINE_POLL_S)
            report_throttle_stall(time.monotonic() - started)
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

    def cell_envelope_s(self, query: str, pipeline_params: dict[str, Any] | None) -> float | None:
        """Seconds this cell may spend, or ``None`` where the backend declares no bound — see
        :attr:`Connector.cell_envelope_s`. A method, not a property: it is resolved per cell."""
        return None if self._cell_envelope is None else self._cell_envelope(query, pipeline_params)

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
        query: str,
        pipeline_params: dict[str, Any] | None = None,
        *,
        bound: SendBound | None,
        billed: CellBilling,
    ) -> dict[str, Any]:
        """One cell — POST /matches, or the in-process arm — held whole at ``bound`` against the
        run's spend book and settled off what the reply says it billed (``billed`` reads a reply's
        ``data``). Where the backend's own sends are each admitted as they are made
        (``Connector.holds_own_sends``) the cell only RESERVES ``bound``, and ``bound`` is ``None``
        where nothing bounds it. A request that may have reached the backend is never sent
        again: only a throttle, a 5xx, a connection never made and a lost session are — a throttle
        whenever the run's :attr:`backpressure` lets it, a connection never made until the backend
        answers within :data:`BACKEND_OUTAGE_S`, the rest a bounded number of times, each landing
        on the ledger through :func:`emit_backend_warning`. A 5xx whose body says a resend ends the
        same way is never sent again either: the cell is HALTED (:class:`CellHaltedError`)."""
        payload = self._wire_adapter(query, pipeline_params)

        if self._execution != "remote_http":
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
                            self._in_process_run, query, payload, bound=bound, billed=billed
                        )
                    except CellThrottledError as exc:
                        self.backpressure.throttled(ticket, headers=None, body=str(exc))
                        continue
                    self.backpressure.eased(ticket)
                    return result

        if bound is None:
            raise RuntimeError("a remote cell is held whole, so it needs the bound its nodes serve")
        client = self._get_http()

        def _warn(kind: str, *, attempt: int, wait_s: float, **extra: Any) -> None:
            # Straight to the ledger's own emitter rather than back up through a callback the
            # caller threads in: the in-process arm above could not be given one, so its retries —
            # the ones that carry a whole diagnosis — reached no surface at all.
            emit_backend_warning(
                kind=kind,
                attempt=attempt + 1,
                max_attempts=MAX_SEND_ATTEMPTS,
                wait_s=float(wait_s),
                query=query,
                **extra,
            )

        # 429 → the run's backpressure; a connection never made → wait out the outage; a 5xx → exp
        # backoff (1, 2, 4, 8s); a lost session → one recovery; everything else, a read timeout
        # included, exits.
        recovered = False
        attempt = 0
        down_since: float | None = None
        while True:
            wait: float | None = None
            unreachable: httpx.TransportError | None = None
            async with self.backpressure.send() as ticket:
                with admitted(_CELL, bound, model=None, provider=None) as admission:
                    try:
                        resp = await client.post(
                            f"{self.base_url}/matches",
                            json=payload,
                            timeout=QUERY_TIMEOUT,
                        )
                    except httpx.TransportError as exc:
                        if not never_sent(exc):
                            _warn(
                                "transport_error",
                                attempt=attempt,
                                wait_s=0.0,
                                error_class=exc.__class__.__name__,
                                final=True,
                            )
                            raise
                        admission.release()
                        unreachable = exc
                    else:
                        down_since = None
                        code = resp.status_code
                        reported = billed(_reply_data(resp))
                        if reported is not None:
                            admission.settle(*reported)
                        elif resp.is_success or may_have_billed(code):
                            admission.unreported()
                        else:
                            admission.release()
                        if code == 429:
                            self.backpressure.throttled(
                                ticket, headers=resp.headers, body=resp.text
                            )
                            continue
                        if resp.is_success:
                            self.backpressure.eased(ticket)
                        if 500 <= code < 600 and (refused := _resend_refused(resp)) is not None:
                            raise CellHaltedError(
                                f"HTTP {code} {refused}", spent={}, step_timings={}
                            )
                        if 500 <= code < 600 and attempt + 1 < MAX_SEND_ATTEMPTS:
                            wait = float(2**attempt)
                            logger.warning(
                                "Backend %d (attempt %d/%d); waiting %.1fs",
                                code,
                                attempt + 1,
                                MAX_SEND_ATTEMPTS,
                                wait,
                            )
                            _warn("server_error", attempt=attempt, wait_s=wait, status_code=code)
                        elif (
                            code == 400
                            and not recovered
                            and _is_session_error(resp)
                            and await self._guard.recover(client, self.base_url)
                        ):
                            recovered = True
                            wait = 0.0
            if unreachable is not None:
                error_class = unreachable.__class__.__name__
                if down_since is None:
                    down_since = time.monotonic()
                    logger.warning(
                        "Backend unreachable (%s); waiting up to %.0fs for it to answer",
                        error_class,
                        BACKEND_OUTAGE_S,
                    )
                    _warn(
                        "transport_error",
                        attempt=attempt,
                        wait_s=BACKEND_OUTAGE_S,
                        error_class=error_class,
                    )
                if not await self._reachable_within(down_since):
                    _warn(
                        "transport_error",
                        attempt=attempt,
                        wait_s=0.0,
                        error_class=error_class,
                        final=True,
                    )
                    raise unreachable
                continue
            if wait is None:
                break
            attempt += 1
            if wait:
                await wait_with_countdown(wait, "backend")

        resp.raise_for_status()
        match_result: dict[str, Any] = resp.json()
        return match_result

    async def _in_process_cell(
        self,
        run: InProcessRun,
        query: str,
        payload: dict[str, Any],
        *,
        bound: SendBound | None,
        billed: CellBilling,
    ) -> dict[str, Any]:
        if bound is None:
            return await run(self.workload, query, payload)
        if self.holds_own_sends:
            # Every send it makes is billed where it is made, so the cell is no send of its own.
            with reserved(_CELL, bound):
                return await run(self.workload, query, payload)
        with admitted(_CELL, bound, model=None, provider=None) as admission:
            try:
                result = await run(self.workload, query, payload)
            except CellUnscoreableError as exc:
                # It ran to no verdict — a throttle included — and says what it paid for doing so.
                _settle(admission, billed(_spent_data(exc)))
                raise
            _settle(admission, billed(result.get("data") or {}))
            return result
