"""HTTP client for backend APIs — wire payloads + session lifecycle.

Fetches experiments, syncs data into the project store, and replays
pipeline queries. All API responses stored verbatim.

Connector-specific assumptions (wire payload shape, session lifecycle,
experiment-data extraction) live in :mod:`promptpotter.connectors`.
``BackendClient`` is connector-agnostic — callers look up a
``Connector`` and pass its ``wire_adapter`` + ``session_factory()`` in.
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
    "extract_pipeline_config",
]


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


class BackendClient:
    """Async HTTP client for a single backend instance.

    Connector-agnostic: ``wire_adapter`` (outbound payload shape) and
    ``session`` (lifecycle) are required at construction, looked up by
    callers via :mod:`promptpotter.connectors`.
    """

    def __init__(
        self,
        base_url: str,
        *,
        wire_adapter: WireAdapter,
        session: SessionProtocol,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._wire_adapter: WireAdapter = wire_adapter
        self._guard: SessionProtocol = session
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
