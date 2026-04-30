"""HTTP client for backend APIs — wire payloads + session lifecycle.

Fetches experiments, syncs data into the project store, and replays
pipeline queries. All API responses stored verbatim.

The TermNorm-specific assumptions live in two swappable seams (see
``promptpotter/domain/connector.py``):

* ``termnorm_wire_adapter`` — the outbound payload shape
  ``{"query", "steps", "node_config"}``. ``BackendClient.__init__``
  accepts an optional ``wire_adapter`` parameter; M12 connectors swap
  in their own.
* ``TermNormSession`` — the ``POST /sessions`` handshake with a
  ``terms`` array. ``BackendClient.__init__`` accepts an optional
  ``session`` parameter; backends without a session concept pass a
  no-op implementation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter.infrastructure.llm import (
    MAX_429_ATTEMPTS,
    parse_retry_after,
    wait_with_countdown,
)

QUERY_TIMEOUT: float = 120.0  # HTTP timeout for /matches endpoint

if TYPE_CHECKING:
    from promptpotter.domain.connector import SessionProtocol, WireAdapter
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)

__all__ = [
    "BackendClient",
    "TermNormSession",
    "extract_pipeline_config",
    "termnorm_wire_adapter",
]


def termnorm_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Default outbound payload shape — TermNorm's ``{"query", "steps", "node_config"}``.

    ``pipeline_params`` carries ``steps`` (which nodes to run) plus per-node
    override dicts (e.g. ``{"entity_profiling": {"prompt": "..."}}``)
    which become the ``node_config`` key in the wire payload. Non-dict
    pipeline_param values are dropped with a debug log — the backend
    contract is "everything beyond steps is a per-node config dict".
    """
    payload: dict[str, Any] = {"query": query}

    _pp = pipeline_params or {}

    if "steps" in _pp:
        payload["steps"] = _pp["steps"]

    wire_overrides: dict[str, dict] = {}
    for k, v in _pp.items():
        if k == "steps":
            continue
        if isinstance(v, dict):
            wire_overrides[k] = v
        else:
            logger.debug(
                "termnorm_wire_adapter: dropping non-dict pipeline_param %r=%r",
                k,
                v,
            )

    if wire_overrides:
        payload["node_config"] = wire_overrides

    return payload


def extract_pipeline_config(experiment_extract: dict) -> dict:
    """Extract pipeline config (steps + params) from synced experiment data."""
    runs = experiment_extract.get("runs", [])
    if not runs:
        return {"steps": [], "notation": "unknown", "name": "", "version": ""}
    pipeline = runs[0].get("pipeline", {})
    config = pipeline.get("config", {})
    return {
        "name": config.get("name", ""),
        "version": config.get("version", ""),
        "description": config.get("description", ""),
        "notation": pipeline.get("notation", ""),
        "config_id": pipeline.get("config_id", ""),
        "steps": config.get("steps", []),
        "metadata": config.get("metadata", {}),
    }


def _is_session_error(resp: httpx.Response) -> bool:
    """True when a 400 response body names a missing/invalid session."""
    try:
        detail = resp.json().get("detail", "")
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception:
        return False
    return "session" in detail.lower()


class TermNormSession:
    """TermNorm-shaped session lifecycle — implements ``SessionProtocol``.

    ``POST /sessions`` handshake with a ``terms`` array. Keeps
    ``BackendClient.run_query()`` free of session semantics: the HTTP
    transport asks the session to initialize or recover; the session
    owns the indexing terms, the idempotency check, and the reinit
    handshake. M12 connectors with a different (or no) session concept
    pass their own ``SessionProtocol`` implementation to
    ``BackendClient(__init__)``.
    """

    __slots__ = ("_terms",)

    def __init__(self) -> None:
        self._terms: list[str] | None = None

    @property
    def has_terms(self) -> bool:
        return bool(self._terms)

    async def set_terms(
        self, http: httpx.AsyncClient, base_url: str, terms: list[str]
    ) -> dict[str, Any]:
        """Install terms and ``POST /sessions``. Idempotent for identical terms."""
        if not terms:
            logger.warning(
                "init_session called with empty terms — session won't support /matches",
            )
            return {"status": "skipped", "terms_count": 0}
        if self._terms == terms:
            return {"status": "already_initialized", "terms_count": len(terms)}
        resp = await http.post(f"{base_url}/sessions", json={"terms": terms})
        resp.raise_for_status()
        self._terms = terms
        return resp.json()

    async def recover(self, http: httpx.AsyncClient, base_url: str) -> bool:
        """Reinit the session using stored terms. Returns True on success."""
        if not self._terms:
            logger.error(
                "Backend requires session but no terms available. "
                "Call init_session() with terms before running matches."
            )
            return False
        logger.warning("Got 400 (no session) — re-initializing")
        terms = self._terms
        self._terms = None  # clear so idempotency guard re-sends
        await self.set_terms(http, base_url, terms)
        return True


class BackendClient:
    """Async HTTP client for a single backend instance.

    Wire payload shape and session lifecycle are pluggable via
    ``wire_adapter`` / ``session``. Defaults preserve TermNorm behavior
    (the only connector implemented today). M12 connectors pass their
    own implementations — ``ConnectorProtocol`` lives in
    :mod:`promptpotter.domain.connector`.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        *,
        wire_adapter: WireAdapter | None = None,
        session: SessionProtocol | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._wire_adapter: WireAdapter = wire_adapter or termnorm_wire_adapter
        self._guard: SessionProtocol = session or TermNormSession()
        self._http: httpx.AsyncClient | None = None

    def _get_http(self) -> httpx.AsyncClient:
        """Return a shared async httpx client, creating lazily on first use."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    async def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        """GET ``{base_url}{path}`` and return parsed JSON."""
        kwargs: dict[str, Any] = {"params": params} if params else {}
        resp = await self._get_http().get(
            f"{self.base_url}{path}",
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        """Close the shared HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    # -- status check -------------------------------------------------------

    async def check_status(self) -> dict[str, Any]:
        """GET /status — returns backend health/state info.

        Returns parsed JSON on success, or a dict with:
        - ``"error"`` + ``"status"`` on failure
        - ``"status": "not_implemented"`` for 404 (endpoint missing)
        - ``"status": "unreachable"`` for connection errors
        """
        try:
            resp = await self._get_http().get(f"{self.base_url}/status")
            resp.raise_for_status()
            return resp.json()
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

    # -- sync operations (fetch verbatim API responses) -------------------

    async def fetch_experiments(self) -> dict[str, Any]:
        """GET /experiments — full response verbatim."""
        return await self._get_json("/experiments")

    async def fetch_experiment(
        self,
        experiment_id: str,
        include_traces: bool = True,
    ) -> dict[str, Any]:
        """GET /experiments/{id}/mappings — includes mappings, runs, eval results."""
        return await self._get_json(
            f"/experiments/{experiment_id}/mappings",
            **({"include_traces": "true"} if include_traces else {}),
        )

    # -- replay operations ------------------------------------------------

    async def init_session(self, terms: list[str]) -> dict[str, Any]:
        """POST /sessions with terms array.

        Thin pass-through to the session guard — idempotent for identical
        terms, stores terms internally so ``run_query()`` can auto-recover
        if the backend restarts mid-campaign.
        """
        return await self._guard.set_terms(self._get_http(), self.base_url, terms)

    async def run_query(
        self,
        query: str,
        pipeline_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /matches — payload shape is owned by ``self._wire_adapter``.

        Default ``termnorm_wire_adapter`` projects ``pipeline_params``
        into ``{"query", "steps", "node_config"}``; M12 connectors can
        swap to a different shape by passing a custom ``wire_adapter``
        to ``BackendClient(__init__)``.

        HTTP 400 with "session" in the detail triggers a one-shot session
        recovery via the session protocol, then retries once.
        """
        payload = self._wire_adapter(query, pipeline_params)

        client = self._get_http()

        # Bounded retry with visible countdown. 429 honors RFC 7231 Retry-After;
        # 5xx and transport errors fall back to exponential backoff (1, 2, 4, 8s).
        # Other statuses (incl. 2xx and 4xx-non-429) exit the loop immediately.
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
                    raise
                wait_t = float(2**attempt)
                logger.warning(
                    "Backend transport error (attempt %d/%d): %s; waiting %.1fs",
                    attempt + 1,
                    MAX_429_ATTEMPTS,
                    exc.__class__.__name__,
                    wait_t,
                )
                await wait_with_countdown(wait_t, "backend connection")
                continue

            code = resp.status_code
            if code == 429:
                wait_h = parse_retry_after(resp.headers)
                if wait_h is None or wait_h <= 0 or attempt == MAX_429_ATTEMPTS - 1:
                    break
                logger.warning(
                    "Backend 429 (attempt %d/%d); waiting %.1fs",
                    attempt + 1,
                    MAX_429_ATTEMPTS,
                    wait_h,
                )
                await wait_with_countdown(wait_h + 1.0, "backend")
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
        return resp.json()

    # -- high-level sync --------------------------------------------------

    async def sync_experiments(
        self,
        store: Stores,
        backend_id: str,
        include_traces: bool = True,
    ) -> int:
        """Fetch all experiments and store verbatim. Returns count."""
        data = await self.fetch_experiments()
        store.backends.save_sync(backend_id, "experiments.json", data)

        experiments = data.get("experiments", [])
        for exp in experiments:
            exp_id = exp["experiment_id"]
            if exp_id:
                detail = await self.fetch_experiment(
                    exp_id,
                    include_traces=include_traces,
                )
                store.backends.save_sync(
                    backend_id,
                    f"experiments/{exp_id}.json",
                    detail,
                )
        return len(experiments)

    async def sync_experiment(
        self,
        store: Stores,
        backend_id: str,
        experiment_id: str,
        include_traces: bool = True,
    ) -> dict[str, Any]:
        """Fetch one experiment and store verbatim."""
        detail = await self.fetch_experiment(
            experiment_id,
            include_traces=include_traces,
        )
        store.backends.save_sync(
            backend_id,
            f"experiments/{experiment_id}.json",
            detail,
        )
        return detail
