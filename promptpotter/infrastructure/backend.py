"""HTTP client for backend APIs — wire payloads + session lifecycle. Connector-agnostic;
per-connector adapters in `promptpotter.connectors`. API responses stored verbatim.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter.infrastructure.llm.rate_limit import (
    MAX_429_ATTEMPTS,
    decide_429_wait,
    wait_with_countdown,
)

QUERY_TIMEOUT: float = 120.0  # HTTP timeout for /matches endpoint

if TYPE_CHECKING:
    from promptpotter.connectors.protocol import Connector, ConnectorExecution, InProcessRun
    from promptpotter.domain.connector import SessionProtocol, WireAdapter

logger = logging.getLogger(__name__)

__all__ = [
    "BackendClient",
    "build_backend_client",
]


def build_backend_client(connector: Connector, base_url: str) -> BackendClient:
    """The ONE ``BackendClient`` construction — every wire fact comes off the connector.

    Transport, payload shape, session AND credential are per-backend facts, so they are
    all read from the one place that declares them. Constructing the client by hand
    instead let the credential be named at the call site, where four sites each passed
    TermNorm's bearer token to whatever connector had been resolved — a second
    ``remote_http`` backend would have received it.
    """
    return BackendClient(
        base_url,
        wire_adapter=connector.wire_adapter,
        session=connector.session_factory(),
        execution=connector.execution,
        in_process_run=connector.in_process_run,
        auth_token=connector.auth_token() if connector.auth_token else None,
    )


def _is_session_error(resp: httpx.Response) -> bool:
    """True when a 400 body signals a missing/invalid session (→ recover + retry).

    The session-loss contract over the PP↔backend highway: the backend either
    stamps a machine-readable ``code: "no_session"`` (the stable signal — survives
    message rewording) OR carries the word ``session`` in its human message. We
    accept either, and across both error envelopes — TermNorm's
    ``{"status","message","code"}`` and FastAPI's default ``{"detail": …}`` — so a
    backend restart/reload (which wipes the in-memory session, e.g. on every
    ``--reload``) self-heals instead of aborting the round."""
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
        # The non-HTTP execution arm, supplied by an ``in_process`` connector.
        self._in_process_run: InProcessRun | None = in_process_run
        self._auth_token = auth_token or ""
        self._http: httpx.AsyncClient | None = None

    def _get_http(self) -> httpx.AsyncClient:
        """Return a shared async httpx client, creating lazily on first use."""
        if self._http is None or self._http.is_closed:
            headers = {"Authorization": f"Bearer {self._auth_token}"} if self._auth_token else None
            self._http = httpx.AsyncClient(timeout=self.timeout, headers=headers)
        return self._http

    @property
    def http(self) -> httpx.AsyncClient:
        """Public accessor for the shared httpx client.

        Used by bootstrap-side helpers (e.g. connector revision check) that
        need the same authenticated client without round-tripping through
        ``BackendClient``'s own methods.
        """
        return self._get_http()

    async def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        """GET ``{base_url}{path}`` and return parsed JSON."""
        kwargs: dict[str, Any] = {"params": params} if params else {}
        resp = await self._get_http().get(
            f"{self.base_url}{path}",
            **kwargs,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def aclose(self) -> None:
        """Close the shared HTTP client."""
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
        """GET /pipeline — full pipeline configuration with registry data."""
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
        on_warning: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """POST /matches. 400+session-in-detail → one-shot session recover + retry. *on_warning*
        fires on each retry (transport/429/5xx) for ledger telemetry — retry behaviour itself unchanged.
        """
        payload = self._wire_adapter(query, pipeline_params)

        if self._execution != "remote_http":
            # Declared-mode dispatch: a non-HTTP connector runs in this process
            # via its own arm (``promptpotter`` → an inner cycle). The connector
            # owns *how* it runs; the registry guarantees the arm is present
            # whenever the mode is ``in_process``.
            assert self._in_process_run is not None, (
                f"execution={self._execution!r} but no in_process_run wired"
            )
            return await self._in_process_run(query, payload)

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

        # 429 → Retry-After (RFC 7231); 5xx + transport → exp backoff (1, 2, 4, 8s); others exit.
        resp: httpx.Response | None = None
        for attempt in range(MAX_429_ATTEMPTS):
            try:
                resp = await client.post(
                    f"{self.base_url}/matches",
                    json=payload,
                    timeout=QUERY_TIMEOUT,
                )
            except httpx.TransportError as exc:
                if attempt == MAX_429_ATTEMPTS - 1:
                    _warn(
                        "transport_error",
                        attempt=attempt,
                        wait_s=0.0,
                        error_class=exc.__class__.__name__,
                        final=True,
                    )
                    raise
                wait_t = float(2**attempt)
                logger.warning(
                    "Backend transport error (attempt %d/%d): %s; waiting %.1fs",
                    attempt + 1,
                    MAX_429_ATTEMPTS,
                    exc.__class__.__name__,
                    wait_t,
                )
                _warn(
                    "transport_error",
                    attempt=attempt,
                    wait_s=wait_t,
                    error_class=exc.__class__.__name__,
                )
                await wait_with_countdown(wait_t, "backend connection")
                continue

            code = resp.status_code
            if code == 429:
                decision = decide_429_wait(resp.headers, resp.text, attempt)
                if decision is None:
                    break
                logger.warning(
                    "Backend 429 [%s] (attempt %d/%d); waiting %.1fs",
                    decision.scope,
                    attempt + 1,
                    MAX_429_ATTEMPTS,
                    decision.seconds,
                )
                _warn(
                    "rate_limit",
                    attempt=attempt,
                    wait_s=decision.seconds,
                    status_code=429,
                    scope=decision.scope,
                )
                await wait_with_countdown(decision.seconds, f"backend {decision.scope}")
                continue

            if 500 <= code < 600 and attempt < MAX_429_ATTEMPTS - 1:
                wait_5 = float(2**attempt)
                logger.warning(
                    "Backend %d (attempt %d/%d); waiting %.1fs",
                    code,
                    attempt + 1,
                    MAX_429_ATTEMPTS,
                    wait_5,
                )
                _warn("server_error", attempt=attempt, wait_s=wait_5, status_code=code)
                await wait_with_countdown(wait_5, f"backend {code}")
                continue

            break
        assert resp is not None  # loop invariant: set resp or raised TransportError

        if (
            resp.status_code == 400
            and _is_session_error(resp)
            and await self._guard.recover(client, self.base_url)
        ):
            resp = await client.post(
                f"{self.base_url}/matches",
                json=payload,
                timeout=QUERY_TIMEOUT,
            )

        resp.raise_for_status()
        match_result: dict[str, Any] = resp.json()
        return match_result
