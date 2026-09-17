"""HTTP client for backend APIs — wire payloads + session lifecycle. Connector-agnostic;
per-connector adapters in `promptpotter.connectors`. API responses stored verbatim.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter.infrastructure.llm.rate_limit import (
    MAX_429_ATTEMPTS,
    decide_429_wait,
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
)
from promptpotter.shared.errors import CellUnscoreableError

# HTTP timeout for /matches. Longer than the backend's own retries can run — a few provider
# attempts per LLM node, each on its own timeout — because a read timeout is terminal: the backend
# is still working and billing, so the cell is charged its whole bound and never sent again.
QUERY_TIMEOUT: float = 600.0

# The hold a whole cell is admitted on, and the settle that reads what it billed off the reply —
# ``None`` where the reply reports nothing, which is charged in full.
CellBilling = Callable[[dict[str, Any]], "list[Billed] | None"]
_CELL = CallLabel("backend_cell", "backend")

if TYPE_CHECKING:
    from promptpotter.connectors.protocol import (
        Connector,
        InProcessRun,
        InProcessWorkload,
        PromptDelivery,
    )
    from promptpotter.domain.connector import (
        CellEnvelopeSeconds,
        ConnectorExecution,
        MeasuredUnit,
        SessionProtocol,
        WireAdapter,
    )
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
        cancel_stops_billing=connector.cancel_stops_billing,
        cell_envelope=connector.cell_envelope_s,
        measured_unit=connector.measured_unit,
        answer_key=connector.answer_key,
        prompt_delivery=connector.prompt_delivery,
        auth_token=connector.auth_token() if connector.auth_token else None,
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
        admission.charge_in_full()
    else:
        admission.settle(*reported)


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
        cancel_stops_billing: bool = False,
        cell_envelope: CellEnvelopeSeconds | None = None,
        measured_unit: MeasuredUnit = "sample",
        answer_key: str | None = None,
        prompt_delivery: PromptDelivery,
        timeout: float = 30.0,
        auth_token: str | None = None,
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
        self.cancel_stops_billing = cancel_stops_billing
        self._cell_envelope: CellEnvelopeSeconds | None = cell_envelope
        self._measured_unit: MeasuredUnit = measured_unit
        self._prompt_delivery: PromptDelivery = prompt_delivery
        # Where this backend's answer TEXT lives, when it emits one outside a ranking.
        self._answer_key: str | None = answer_key
        self._auth_token = auth_token or ""
        self._http: httpx.AsyncClient | None = None

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
        on_warning: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """One cell — POST /matches, or the in-process arm — held whole at ``bound`` against the
        run's spend book and settled off what the reply says it billed (``billed`` reads a reply's
        ``data``). ``bound`` is ``None`` only for a backend whose own sends are each admitted
        (``Connector.holds_own_sends``). A request that may have reached the backend is never sent
        again: only a 429, a 5xx, a connection never made and a lost session are. *on_warning*
        fires on each retry for ledger telemetry."""
        payload = self._wire_adapter(query, pipeline_params)

        if self._execution != "remote_http":
            # Declared-mode dispatch: a non-HTTP connector runs in this process via its own arm.
            # The registry guarantees the arm whenever the mode is ``in_process``.
            if self._in_process_run is None:
                raise RuntimeError(f"execution={self._execution!r} but no in_process_run wired")
            if bound is None:
                return await self._in_process_run(self.workload, query, payload)
            with admitted(_CELL, bound, model=None, provider=None) as admission:
                try:
                    result = await self._in_process_run(self.workload, query, payload)
                except CellUnscoreableError as exc:
                    # It ran to no verdict, and says what it paid for doing so.
                    _settle(admission, billed(_spent_data(exc)))
                    raise
                _settle(admission, billed(result.get("data") or {}))
                return result

        if bound is None:
            raise RuntimeError("a remote cell is held whole, so it needs the bound its nodes serve")
        client = self._get_http()

        def _warn(kind: str, *, attempt: int, wait_s: float, **extra: Any) -> None:
            if on_warning is None:
                return
            try:
                on_warning(
                    {
                        "kind": kind,
                        "attempt": attempt + 1,
                        "max_attempts": MAX_429_ATTEMPTS,
                        "wait_s": float(wait_s),
                        **extra,
                    }
                )
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception:
                logger.exception("on_warning callback failed; continuing retry loop")

        # 429 → Retry-After (RFC 7231); 5xx + a connection never made → exp backoff (1, 2, 4, 8s);
        # a lost session → one recovery; everything else, a read timeout included, exits.
        recovered = False
        for attempt in itertools.count():
            wait: float | None = None
            with admitted(_CELL, bound, model=None, provider=None) as admission:
                try:
                    resp = await client.post(
                        f"{self.base_url}/matches",
                        json=payload,
                        timeout=QUERY_TIMEOUT,
                    )
                except httpx.TransportError as exc:
                    error_class = exc.__class__.__name__
                    if never_sent(exc):
                        admission.release()
                        if attempt + 1 < MAX_429_ATTEMPTS:
                            wait = float(2**attempt)
                    if wait is None:
                        _warn(
                            "transport_error",
                            attempt=attempt,
                            wait_s=0.0,
                            error_class=error_class,
                            final=True,
                        )
                        raise
                    logger.warning(
                        "Backend unreachable (attempt %d/%d): %s; waiting %.1fs",
                        attempt + 1,
                        MAX_429_ATTEMPTS,
                        error_class,
                        wait,
                    )
                    _warn("transport_error", attempt=attempt, wait_s=wait, error_class=error_class)
                else:
                    code = resp.status_code
                    reported = billed(_reply_data(resp))
                    if reported is not None:
                        admission.settle(*reported)
                    elif resp.is_success or may_have_billed(code):
                        admission.charge_in_full()
                    else:
                        admission.release()
                    if code == 429:
                        decision = decide_429_wait(resp.headers, resp.text, attempt)
                        if decision is not None:
                            wait = decision.seconds
                            logger.warning(
                                "Backend 429 [%s] (attempt %d/%d); waiting %.1fs",
                                decision.scope,
                                attempt + 1,
                                MAX_429_ATTEMPTS,
                                wait,
                            )
                            _warn(
                                "rate_limit",
                                attempt=attempt,
                                wait_s=wait,
                                status_code=429,
                                scope=decision.scope,
                            )
                    elif 500 <= code < 600 and attempt + 1 < MAX_429_ATTEMPTS:
                        wait = float(2**attempt)
                        logger.warning(
                            "Backend %d (attempt %d/%d); waiting %.1fs",
                            code,
                            attempt + 1,
                            MAX_429_ATTEMPTS,
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
            if wait is None:
                break
            if wait:
                await wait_with_countdown(wait, "backend")

        resp.raise_for_status()
        match_result: dict[str, Any] = resp.json()
        return match_result
