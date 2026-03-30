"""
HTTP client for backend APIs (e.g. TermNorm).

Fetches experiments, syncs data into the project store, and replays
pipeline queries. All API responses stored verbatim.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

from api.config.settings import MATCH_TIMEOUT

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema
    from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


def extract_pipeline_config(exp_data: dict) -> dict:
    """Extract pipeline config (steps + params) from synced experiment data."""
    runs = exp_data.get("runs", [])
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


def build_pipeline_params(
    pipeline_config: dict,
    *,
    overrides: dict | None = None,
    exclude_nodes: list[str] | None = None,
    schema: PipelineSchema | None = None,
) -> dict:
    """Build pipeline_params from a (possibly shortened) pipeline config.

    Returns dict ready for evaluate_prompt(..., pipeline_params=params).
    Includes ``steps`` list and per-node override dicts (e.g.
    ``{"steps": [...], "llm_ranking": {"temperature": 0.5}}``).
    ``BackendClient.run_match()`` translates these to the ``node_config``
    wire format at the TermNorm boundary.

    Args:
        pipeline_config: Pipeline config with ``steps`` list.
        overrides: Optional parameter overrides using flat param names
            (e.g. ``ranking_temperature``). Translated to per-node dicts
            via the schema's ``override_map``.
        exclude_nodes: Node names to remove from the active pipeline
            (e.g. ``["llm_ranking"]`` for token-matching-only evaluation).
        schema: PipelineSchema for node-param and flat→wire lookup.
    """
    node_names = [s["name"] for s in pipeline_config["steps"]]
    if exclude_nodes:
        node_names = [n for n in node_names if n not in exclude_nodes]
    params: dict = {"steps": node_names}

    # Seed with current config from schema (no hidden defaults)
    if schema is not None:
        for node in schema.nodes:
            if node.name in node_names and node.current_config:
                params[node.name] = dict(node.current_config)

    if not overrides:
        return params

    if schema is not None:
        node_param_map = schema.node_param_keys()
        active_param_names: set = set()
        for name in node_names:
            active_param_names |= node_param_map.get(name, set())

        for k, v in overrides.items():
            if k not in active_param_names:
                continue
            resolved = schema.resolve_flat_param(k)
            if resolved:
                node_name, wire_key = resolved
                params.setdefault(node_name, {})[wire_key] = v
            else:
                # No override_map entry — pass through as flat key
                params[k] = v
    else:
        # No schema — pass overrides through as-is (legacy fallback)
        params.update(overrides)

    return params


class BackendClient:
    """Async HTTP client for a single backend instance."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session_terms: list[str] | None = None
        self._http: httpx.AsyncClient | None = None

    def _get_http(self) -> httpx.AsyncClient:
        """Return a shared async httpx client, creating lazily on first use."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    @staticmethod
    def split_query_parts(query: str) -> tuple[str, str]:
        """Split a TermNorm query string into (bom_material, process).

        TermNorm queries use the format ``bom_material / process``.
        If no slash is present, process is an empty string.

        Connector-specific: other backends implement their own query
        parsing inside their ``extract_replay_queries()`` method.
        """
        if "/" in query:
            last_slash = query.rfind("/")
            bom_material = query[:last_slash].strip()
            process = query[last_slash + 1:].strip()
        else:
            bom_material = query.strip()
            process = ""
        return bom_material, process

    async def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        """GET ``{base_url}{path}`` and return parsed JSON."""
        resp = await self._get_http().get(
            f"{self.base_url}{path}", **({"params": params} if params else {}),
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
        self, experiment_id: str, include_traces: bool = True,
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
        same terms.  Stores ``terms`` internally so that ``run_match()``
        can auto-reinitialize the session if the backend restarts.
        """
        if not terms:
            logger.warning(
                "init_session called with empty terms — session won't support /matches",
            )
            return {"status": "skipped", "terms_count": 0}
        if self._session_terms == terms:
            return {"status": "already_initialized", "terms_count": len(terms)}
        resp = await self._get_http().post(
            f"{self.base_url}/sessions",
            json={"terms": terms},
        )
        resp.raise_for_status()
        self._session_terms = terms
        return resp.json()

    async def run_match(
        self,
        query: str,
        pipeline_params: dict[str, Any] | None = None,
        ranking_prompt: str | None = None,
    ) -> dict[str, Any]:
        """POST /matches — translate pipeline_params to TermNorm wire format.

        ``pipeline_params`` is the PromptPotter-internal nested dict
        (e.g. ``{"steps": [...], "llm_ranking": {"temperature": 0.5}}``).
        This method translates non-``steps`` keys into the ``node_config``
        wire-format key that TermNorm expects.
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

        # ranking_prompt shorthand → inject into llm_ranking.prompt
        if ranking_prompt:
            wire_overrides.setdefault("llm_ranking", {})["prompt"] = ranking_prompt

        if wire_overrides:
            payload["node_config"] = wire_overrides

        client = self._get_http()
        resp = await client.post(
            f"{self.base_url}/matches",
            json=payload,
            timeout=MATCH_TIMEOUT,
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

            if is_session_error and self._session_terms:
                logger.warning("Got 400 (no session) — re-initializing")
                terms = self._session_terms
                self._session_terms = None  # clear so idempotency guard re-sends
                await self.init_session(terms)
                resp = await client.post(
                    f"{self.base_url}/matches",
                    json=payload,
                    timeout=MATCH_TIMEOUT,
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
        self, store: ProjectStore, backend_id: str,
        include_traces: bool = True,
    ) -> int:
        """Fetch all experiments and store verbatim. Returns count."""
        data = await self.fetch_experiments()
        store.backends.save_sync(backend_id, "experiments.json", data)

        experiments = data.get("experiments", [])
        for exp in experiments:
            exp_id = exp.get("experiment_id", exp.get("id", ""))
            if exp_id:
                detail = await self.fetch_experiment(
                    exp_id, include_traces=include_traces,
                )
                store.backends.save_sync(
                    backend_id, f"experiments/{exp_id}.json", detail,
                )
        return len(experiments)

    async def sync_experiment(
        self, store: ProjectStore, backend_id: str, experiment_id: str,
        include_traces: bool = True,
    ) -> dict[str, Any]:
        """Fetch one experiment and store verbatim."""
        detail = await self.fetch_experiment(
            experiment_id, include_traces=include_traces,
        )
        store.backends.save_sync(
            backend_id, f"experiments/{experiment_id}.json", detail,
        )
        return detail

    # -- replay helpers ---------------------------------------------------

    @staticmethod
    def extract_session_terms(experiment_data: dict) -> list[str]:
        """Extract unique non-empty dataset_entry values from mappings."""
        entries = set()
        for m in experiment_data.get("mappings", []):
            entry = m.get("dataset_entry", "").strip()
            if entry and entry != "--":
                entries.add(entry)
        return sorted(entries)

    @staticmethod
    def extract_replay_queries(
        experiment_data: dict,
    ) -> list[dict[str, Any]]:
        """Extract queries with valid ground truth from experiment data.

        Matches evaluation_result queries back to mappings via bom_material.
        """
        bom_to_gt: dict[str, str] = {}
        for m in experiment_data.get("mappings", []):
            bom = m["bom_material"]
            entry = m.get("dataset_entry", "").strip()
            if entry and entry != "--":
                bom_to_gt[bom] = entry

        runs = experiment_data.get("runs", [])
        if not runs:
            return []

        queries = []
        eval_results = runs[0].get("evaluation_results", [])

        for er in eval_results:
            query = er["query"]
            bom_material, process = BackendClient.split_query_parts(query)

            if bom_material not in bom_to_gt:
                continue

            queries.append({
                "query": query,
                "bom_material": bom_material,
                "process": process,
                "query_fields": {
                    "bom_material": bom_material,
                    "process": process,
                },
                "ground_truth": bom_to_gt[bom_material],
                "original_predicted": er.get("predicted", ""),
                "original_latency_ms": er.get("latency_ms", 0),
                "original_confidence": er.get("confidence", 0),
            })

        return queries

