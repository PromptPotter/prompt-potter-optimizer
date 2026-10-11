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
from promptpotter.infrastructure.llm.pricing import inline_route
from promptpotter.infrastructure.llm.send_failure import failed_send
from promptpotter.infrastructure.llm.send_pacing import (
    HELD_POLL_S,
    Backpressure,
    SendBudget,
    drawn_budget,
    get_abort_check,
    held_wait,
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
from promptpotter.infrastructure.llm.telemetry import emit_backend_warning
from promptpotter.infrastructure.tls import tls_context
from promptpotter.shared.errors import (
    CellHaltedError,
    CellThrottledError,
    CellUnscoreableError,
    ErrorCategory,
)

_OUTAGE_POLL_S = 5.0
_HTTP_TIMEOUT_S = 30.0

# (reply data, refused before generation) -> (what it billed, `None` = reports nothing; caller's read).
type CellBilling[S] = Callable[[dict[str, Any], bool], tuple[Reported | None, S]]
# The 4xx a backend answers a model that generated nothing gradeable with: answered, so billed.
_ANSWERED_STATUSES = frozenset({422})
CELL = CallLabel("backend_cell", "backend")

if TYPE_CHECKING:
    from promptpotter.connectors.protocol import Connector, InProcessRun, InProcessWorkload
    from promptpotter.domain.connector import ConnectorExecution, MeasuredUnit
    from promptpotter.domain.pipeline_schema import NodeSpendBound, PipelineNode
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.value_tree import Delivery

logger = logging.getLogger(__name__)

__all__ = [
    "BackendClient",
]


def _reply_data(resp: httpx.Response) -> dict[str, Any] | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    return data if isinstance(data, dict) else {}


@dataclass
class _CellSends:
    query: str
    budget: SendBudget
    recovered: bool = False
    down_since: float | None = None

    def warn(self, kind: str, *, wait_s: float, resending: bool = False, **extra: Any) -> None:
        emit_backend_warning(
            kind=kind,
            attempt=self.budget.attempt - resending,
            max_attempts=self.budget.attempts,
            wait_s=float(wait_s),
            query=self.query,
            **extra,
        )

    def wait_after(self, outcome: SendOutcome) -> float | None:
        return self.budget.resend_wait() if outcome.resendable else None


class MachineSlots:
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
            await asyncio.sleep(HELD_POLL_S)
            report_throttle_stall(time.monotonic() - started)
        return held

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        self._root.mkdir(parents=True, exist_ok=True)
        with machine_step():
            await claim_machine(compose_overlay=self._compose_overlay)
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
    def __init__(self, connector: Connector, base_url: str, *, workload: InProcessWorkload) -> None:
        self._connector = connector
        self.base_url = base_url.rstrip("/")
        self.workload = workload
        self.backpressure = Backpressure("cells")
        self._guard = connector.session_factory()
        self._http: httpx.AsyncClient | None = None
        self._machine_slots = (
            MachineSlots(
                default_jobs_dir() / "machine" / connector.name,
                connector.max_cells_in_flight,
                compose_overlay=connector.compose_overlay,
            )
            if connector.cells_hold_the_machine
            else None
        )

    @property
    def execution(self) -> ConnectorExecution:
        return self._connector.execution

    @property
    def max_cells_in_flight(self) -> int:
        return self._connector.max_cells_in_flight

    @property
    def measured_unit(self) -> MeasuredUnit:
        return self._connector.measured_unit

    @property
    def answer_key(self) -> str | None:
        return self._connector.answer_key

    @property
    def holds_own_sends(self) -> bool:
        return self._connector.holds_own_sends

    @property
    def cancel_stops_billing(self) -> bool:
        return self._connector.cancel_stops_billing

    @property
    def cell_attempts(self) -> int:
        return self._connector.cell_attempts

    @property
    def prompt_fields_as_node_params(self) -> bool:
        return self._connector.prompt_fields_as_node_params

    @property
    def derives_spend_bounds(self) -> bool:
        return self._connector.sent_spend_bound is not None

    def cell_envelope_s(
        self, sample: Sample, pipeline_params: dict[str, Any] | None
    ) -> float | None:
        envelope = self._connector.cell_envelope_s
        return None if envelope is None else envelope(sample, pipeline_params)

    def prompt_delivery(self, pipeline_params: dict[str, Any] | None) -> Delivery:
        return self._connector.prompt_delivery(pipeline_params)

    def node_spend_bound(self, node: PipelineNode, cfg: Mapping[str, Any]) -> NodeSpendBound | None:
        if (sent := self._connector.sent_spend_bound) is not None:
            return sent(node.name, cfg)
        return node.spend_bound

    def priced_as(self, cfg: Mapping[str, Any]) -> tuple[str | None, str | None]:
        model, provider = cfg.get("model"), cfg.get("provider")
        if not isinstance(model, str):
            return None, None
        if self._connector.model_names_provider and provider is None:
            return inline_route(model)
        return model, provider if isinstance(provider, str) else None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            token = self._connector.auth_token() if self._connector.auth_token else None
            self._http = httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_S,
                headers={"Authorization": f"Bearer {token}"} if token else None,
                verify=tls_context(),
            )
        return self._http

    async def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"params": params} if params else {}
        resp = await self.http.get(
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

    async def check_status(self) -> dict[str, Any]:
        try:
            resp = await self.http.get(f"{self.base_url}/status")
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
        abort = get_abort_check()
        while time.monotonic() - down_since < self._connector.cell_wait_s:
            if abort is not None and abort():
                raise asyncio.CancelledError("backend outage wait aborted")
            started = time.monotonic()
            await asyncio.sleep(_OUTAGE_POLL_S)
            answered = (await self.check_status()).get("status") != "unreachable"
            report_throttle_stall(time.monotonic() - started)
            if answered:
                return True
        return False

    async def fetch_pipeline(self) -> dict[str, Any]:
        return await self._get_json("/pipeline")

    async def init_session(self, terms: list[str]) -> dict[str, Any]:
        return await self._guard.set_terms(self.http, self.base_url, terms)

    async def run_query[S](
        self,
        sample: Sample,
        pipeline_params: dict[str, Any] | None = None,
        *,
        bound: SendBound | None,
        billed: CellBilling[S],
    ) -> tuple[dict[str, Any], S]:
        """Never resends a request the backend may still be working: a read timeout is terminal."""
        query = sample.query
        payload = self._connector.wire_adapter(query, pipeline_params)

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
            raise ValueError(f"backend answered HTTP {resp.status_code} with no JSON object")
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
                with admitted(CELL, bound, model=None, provider=None) as admission:
                    try:
                        resp = await client.post(
                            f"{self.base_url}/matches",
                            json=payload,
                            timeout=self._connector.cell_wait_s,
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
                        data = _reply_data(resp)
                        reported, spent = billed(data or {}, not outcome.may_have_billed)
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
            sends.down_since = time.monotonic()
            logger.warning(
                "Backend unreachable (%s); waiting up to %.0fs for it to answer",
                error_class,
                self._connector.cell_wait_s,
            )
            sends.warn(
                "transport_error", wait_s=self._connector.cell_wait_s, error_class=error_class
            )
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
                hold.enter_context(reserved(CELL, bound))
            elif bound is not None:
                admission = hold.enter_context(admitted(CELL, bound, model=None, provider=None))
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
                        reported, _ = billed(paid, False)
                    admission.close(
                        SendOutcome(
                            sent=True, may_have_billed=True, reported=reported, failure=exc.category
                        )
                    )
                raise
            spent_s = time.monotonic() - started
            data: dict[str, Any] = result["data"]
            data["total_time"] = spent_s
            data["step_timings"] = {data["terminal_node"]: spent_s}
            reported, spent = billed(data, False)
            if admission is not None:
                admission.close(SendOutcome(sent=True, may_have_billed=True, reported=reported))
            return data, spent
