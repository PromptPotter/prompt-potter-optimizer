"""
HTTP client for backend APIs.

Fetches experiments, syncs data into the project store, and replays
pipeline queries. All API responses stored verbatim.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

QUERY_TIMEOUT: float = 120.0  # HTTP timeout for /matches endpoint

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.project_store import ProjectStore

logger = logging.getLogger(__name__)

__all__ = ["BackendClient", "extract_pipeline_config"]


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


class BackendClient:
    """Async HTTP client for a single backend instance."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._index_terms: list[str] | None = None
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

        Idempotent: skips the HTTP call if already initialized with the
        same terms.  Stores ``terms`` internally so that ``run_query()``
        can auto-reinitialize the session if the backend restarts.
        """
        if not terms:
            logger.warning(
                "init_session called with empty terms — session won't support /matches",
            )
            return {"status": "skipped", "terms_count": 0}
        if self._index_terms == terms:
            return {"status": "already_initialized", "terms_count": len(terms)}
        resp = await self._get_http().post(
            f"{self.base_url}/sessions",
            json={"terms": terms},
        )
        resp.raise_for_status()
        self._index_terms = terms
        return resp.json()

    async def run_query(
        self,
        query: str,
        pipeline_params: dict[str, Any] | None = None,
        precomputed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /matches — translate pipeline_params to wire format.

        ``pipeline_params`` carries ``steps`` (which nodes to run) plus
        per-node override dicts (e.g. ``{"entity_profiling": {"prompt": "..."}}``)
        which become the ``node_config`` key in the wire payload.  The backend
        merges overrides with its own defaults — callers only send what changed.
        """
        payload: dict[str, Any] = {"query": query}

        _pp = pipeline_params or {}

        if "steps" in _pp:
            payload["steps"] = _pp["steps"]

        # Collect per-node overrides (everything except "steps")
        wire_overrides: dict[str, dict] = {}
        for k, v in _pp.items():
            if k == "steps":
                continue
            if isinstance(v, dict):
                wire_overrides[k] = v
            else:
                logger.debug(
                    "run_query: dropping non-dict pipeline_param %r=%r",
                    k,
                    v,
                )

        if wire_overrides:
            payload["node_config"] = wire_overrides

        # Wave 4: pass precomputed node outputs (gracefully ignored by older backends)
        if precomputed:
            payload["precomputed"] = precomputed

        client = self._get_http()
        resp = await client.post(
            f"{self.base_url}/matches",
            json=payload,
            timeout=QUERY_TIMEOUT,
        )

        # Auto-reinitialize session on 400 "no session" errors
        if resp.status_code == 400:
            try:
                detail = resp.json().get("detail", "")
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception:
                detail = ""
            is_session_error = "session" in detail.lower()

            if is_session_error and self._index_terms:
                logger.warning("Got 400 (no session) — re-initializing")
                terms = self._index_terms
                self._index_terms = None  # clear so idempotency guard re-sends
                await self.init_session(terms)
                resp = await client.post(
                    f"{self.base_url}/matches",
                    json=payload,
                    timeout=QUERY_TIMEOUT,
                )
            elif is_session_error:
                logger.error(
                    "Backend requires session but no terms available. "
                    "Call init_session() with terms before running matches."
                )

        resp.raise_for_status()
        return resp.json()

    # -- high-level sync --------------------------------------------------

    async def sync_experiments(
        self,
        store: ProjectStore,
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
        store: ProjectStore,
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
