from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter.config.paths import MACHINE_DIR
from promptpotter.domain.connector import BackendUnreachableError, ReplyReader, data_envelope
from promptpotter.domain.records.run_records import BackendWarningRecord
from promptpotter.infrastructure.docker_host import claim_machine, machine_step
from promptpotter.infrastructure.llm.base import KEEPALIVE_OPTIONS
from promptpotter.infrastructure.llm.pricing import priced_pair
from promptpotter.infrastructure.llm.send_failure import failed_send
from promptpotter.infrastructure.llm.send_pacing import (
    HELD_POLL_S,
    Backpressure,
    SendBudget,
    drawn_budget,
    held_wait,
    loop_now,
    report_throttle_stall,
)
from promptpotter.infrastructure.llm.spend_book import (
    Admission,
    CallLabel,
    Reported,
    SendBound,
    SendOutcome,
    admitted,
    replied,
    reserved,
)
from promptpotter.infrastructure.llm.telemetry import append_record
from promptpotter.infrastructure.store.io import HeldLock, take_lock
from promptpotter.shared.errors import (
    CellHaltedError,
    CellThrottledError,
    CellUnscoreableError,
    ErrorCategory,
    PayloadInvalidError,
)
from promptpotter.shared.run_scope import run_scope
from promptpotter.shared.tls import tls_context

_OUTAGE_POLL_S = 5.0
# How long a cell waits for a backend that stopped answering; the cell's own clock may end it sooner.
_OUTAGE_WAIT_S = 600.0
_HTTP_TIMEOUT_S = 30.0

# reply data -> (what it billed, `None` = reports nothing; caller's read).
type CellBilling[S] = Callable[[dict[str, Any]], tuple[Reported | None, S]]
# The 4xx a backend answers a model that generated nothing gradeable with: answered, so billed.
_ANSWERED_STATUSES = frozenset({422})
CELL = CallLabel("backend_cell", "backend")

if TYPE_CHECKING:
    from promptpotter.connectors.protocol import Connector, InProcessRun, InProcessWorkload
    from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
    from promptpotter.domain.pipeline_schema import NodeSpendBound, PipelineNode
    from promptpotter.domain.sample import Sample

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
class _CellSends:
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

    def wait_after(self, outcome: SendOutcome) -> float | None:
        return self.budget.resend_wait() if outcome.resendable else None


class MachineSlots:
    def __init__(
        self, jobs_dir: Path, name: str, capacity: int, *, compose_overlay: Path | None
    ) -> None:
        self._jobs_dir = jobs_dir
        self._root = jobs_dir / MACHINE_DIR / name
        self._capacity = capacity
        self._compose_overlay = compose_overlay

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
            await claim_machine(self._jobs_dir, compose_overlay=self._compose_overlay)
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


class BackendClient:
    def __init__(
        self, connector: Connector, *, workload: InProcessWorkload, jobs_dir: Path
    ) -> None:
        if (unset := connector.unconfigured()) is not None:
            raise BackendUnreachableError(connector.name, "", unset)
        self._connector = connector
        self.base_url = connector.base_url()
        self.workload = workload
        self.backpressure = Backpressure("cells")
        self._guard = connector.session_factory()
        self._http: httpx.AsyncClient | None = None
        self._machine_slots = (
            MachineSlots(
                jobs_dir,
                connector.name,
                connector.max_cells_in_flight,
                compose_overlay=connector.compose_overlay,
            )
            if connector.cells_hold_the_machine
            else None
        )

    @property
    def connector(self) -> Connector:
        return self._connector

    def node_spend_bound(self, node: PipelineNode, cfg: NodeCall) -> NodeSpendBound | None:
        if (sent := self._connector.sent_spend_bound) is not None:
            return sent(node.name, cfg, self.workload.experiment)
        return node.spend_bound

    def priced_as(self, cfg: NodeCall) -> tuple[str | None, str | None]:
        return priced_pair(
            cfg.model, cfg.provider, names_provider=self._connector.model_names_provider
        )

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_S,
                headers=self._connector.credential_header() or None,
                transport=httpx.AsyncHTTPTransport(
                    verify=tls_context(), socket_options=KEEPALIVE_OPTIONS
                ),
            )
        return self._http

    async def aclose(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    async def _reachable_within(self, down_since: float) -> bool:
        abort = run_scope().abort_check
        while loop_now() - down_since < _OUTAGE_WAIT_S:
            if abort is not None and abort():
                raise asyncio.CancelledError("backend outage wait aborted")
            started = loop_now()
            await asyncio.sleep(_OUTAGE_POLL_S)
            answered = await self._connector.unreachable() is None
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
        billed: CellBilling[S],
    ) -> tuple[dict[str, Any], S]:
        """Never resends a request the backend may still be working: a read timeout is terminal."""
        query = sample.query
        payload = self._connector.wire_adapter(sample, pipeline_params)

        if self._connector.execution != "remote_http":
            return await self._in_process_until_admitted(
                sample, payload, bound=bound, billed=billed
            )
        if bound is None:
            raise RuntimeError("a remote cell is held whole, so it needs the bound its nodes serve")
        resp, data, spent = await self._post_until_answered(
            query, payload, bound=bound, billed=billed
        )
        resp.raise_for_status()
        if data is None:
            raise ValueError(f"backend answered HTTP {resp.status_code} with no answer to read")
        return data, spent

    async def _in_process_until_admitted[S](
        self,
        sample: Sample,
        payload: dict[str, Any],
        *,
        bound: SendBound | None,
        billed: CellBilling[S],
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
                        run, sample, payload, bound=bound, billed=billed
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
    ) -> tuple[httpx.Response, dict[str, Any] | None, S]:
        client = self.http
        sends = _CellSends(query, drawn_budget())
        while True:
            wait: float | None = None
            unreachable: httpx.TransportError | None = None
            async with self.backpressure.send() as ticket:
                with admitted(
                    CELL, bound, model=None, provider=None, paid_by=self._connector.cell_payer
                ) as admission:
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
                            unreachable = exc
                        else:
                            wait = sends.wait_after(outcome)
                            sends.warn(
                                "transport_error",
                                wait_s=wait or 0.0,
                                resending=wait is not None,
                                error_class=exc.__class__.__name__,
                                final=wait is None,
                            )
                            if wait is None:
                                raise
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
                await self._wait_out_outage(unreachable, sends)
                continue
            if wait is None:
                return resp, data, spent
            if wait:
                await held_wait(wait, "backend")

    async def _resend_wait(
        self,
        client: httpx.AsyncClient,
        resp: httpx.Response,
        outcome: SendOutcome,
        sends: _CellSends,
    ) -> float | None:
        code = resp.status_code
        if outcome.resendable and (refused := self._guard.resend_refused(resp)) is not None:
            raise CellHaltedError(f"HTTP {code} {refused}", spent={})
        if (wait := sends.wait_after(outcome)) is not None:
            logger.warning(
                "Backend %d (attempt %d/%d); waiting %.1fs",
                code,
                sends.budget.resent,
                sends.budget.attempts,
                wait,
            )
            sends.warn("server_error", wait_s=wait, resending=True, status_code=code)
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
            sends.down_since = loop_now()
            logger.warning(
                "Backend unreachable (%s); waiting up to %.0fs for it to answer",
                error_class,
                _OUTAGE_WAIT_S,
            )
            sends.warn("transport_error", wait_s=_OUTAGE_WAIT_S, error_class=error_class)
        if not await self._reachable_within(sends.down_since):
            sends.warn("transport_error", wait_s=0.0, error_class=error_class, final=True)
            raise unreachable

    async def _in_process_cell[S](
        self,
        run: InProcessRun,
        sample: Sample,
        payload: dict[str, Any],
        *,
        bound: SendBound | None,
        billed: CellBilling[S],
    ) -> tuple[dict[str, Any], S]:
        with contextlib.ExitStack() as hold:
            admission: Admission | None = None
            if bound is not None and self._connector.holds_own_sends:
                hold.enter_context(reserved(CELL, bound, paid_by=self._connector.cell_payer))
            elif bound is not None:
                admission = hold.enter_context(
                    admitted(
                        CELL, bound, model=None, provider=None, paid_by=self._connector.cell_payer
                    )
                )
            started = time.monotonic()
            try:
                result = await run(self.workload, sample, payload)
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
