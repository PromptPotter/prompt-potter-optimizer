"""
HTTP client for backend APIs (e.g. TermNorm).

Fetches experiments, syncs data into the project store, and replays
pipeline queries. All API responses stored verbatim.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import httpx

from api.services.constants import NO_RESULT
from api.services.query_utils import parse_bom_material

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema
    from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)

MATCH_TIMEOUT = 120.0


def load_pipeline_config(exp_data: dict) -> dict:
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
    overrides: dict | None = None,
    exclude_steps: list[str] | None = None,
    schema: "PipelineSchema | None" = None,
) -> dict:
    """Build pipeline_params from a (possibly shortened) pipeline config.

    Returns dict ready for evaluate_prompt(..., pipeline_params=params).
    Includes ``steps`` list and ``node_config`` dict in the same
    shape the backend already expects.

    Args:
        pipeline_config: Pipeline config with ``steps`` list.
        overrides: Optional parameter overrides using flat param names
            (e.g. ``ranking_temperature``). Translated to ``node_config``
            via the schema's ``override_map``.
        exclude_steps: Step names to remove from the active pipeline
            (e.g. ``["llm_ranking"]`` for token-matching-only evaluation).
        schema: PipelineSchema for step-param and flat→wire lookup.
    """
    step_names = [s["name"] for s in pipeline_config["steps"]]
    if exclude_steps:
        step_names = [s for s in step_names if s not in exclude_steps]
    params: dict = {"steps": step_names}

    if not overrides:
        return params

    if schema is not None:
        step_param_map = schema.step_param_keys()
        active_param_names: set = set()
        for name in step_names:
            active_param_names |= step_param_map.get(name, set())

        nc: dict[str, dict] = {}
        for k, v in overrides.items():
            if k not in active_param_names:
                continue
            resolved = schema.resolve_flat_param(k)
            if resolved:
                node, wire_key = resolved
                nc.setdefault(node, {})[wire_key] = v
            else:
                # No override_map entry — pass through as flat key
                params[k] = v
        if nc:
            params["node_config"] = nc
    else:
        # No schema — pass overrides through as-is (legacy fallback)
        params.update(overrides)

    return params


# TermNorm-specific query fields extracted from query_data.
# M8: replace with ConnectorProtocol.extract_query_fields()
_TERMNORM_QUERY_FIELDS = ("bom_material", "process")
_TERMNORM_VARIANT_B_FIELDS = {
    "variant_b_predicted": ("original_predicted", ""),
    "variant_b_latency_ms": ("original_latency_ms", 0),
    "variant_b_confidence": ("original_confidence", 0),
}


def _build_result_dict(
    query_data: dict[str, Any],
    *,
    predicted: str,
    confidence: float,
    ranked_candidates: list,
    latency_ms: float,
    status: str,
    pipeline_data: dict | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build an ExecutionResultItem-compatible dict.

    Centralizes the field assembly shared between success and error
    paths in ``replay_queries()``.
    """
    result: dict[str, Any] = {
        "query": query_data["query"],
        "ground_truth": query_data["ground_truth"],
        "predicted": predicted,
        "confidence": confidence,
        "ranked_candidates": ranked_candidates,
        "latency_ms": latency_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
    }
    # TermNorm-specific fields (M8: move to connector)
    for f in _TERMNORM_QUERY_FIELDS:
        result[f] = query_data[f]
    result["query_fields"] = {f: query_data[f] for f in _TERMNORM_QUERY_FIELDS}
    for dest, (src, default) in _TERMNORM_VARIANT_B_FIELDS.items():
        result[dest] = query_data.get(src, default)
    if pipeline_data is not None:
        result["pipeline_data"] = pipeline_data
        result["web_search_status"] = pipeline_data.get("web_search_status")
    if error is not None:
        result["error"] = error
    return result


class BackendClient:
    """HTTP client for a single backend instance."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session_terms: list[str] | None = None
        self._http: httpx.AsyncClient | None = None

    def _get_http(self) -> httpx.AsyncClient:
        """Return a shared httpx client, creating lazily on first use."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

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
        except Exception as exc:
            logger.warning("Backend status check failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    # -- pipeline config ---------------------------------------------------

    async def fetch_pipeline(self) -> dict[str, Any]:
        """GET /pipeline — returns full pipeline configuration.

        Includes node configs, models, temperatures, and resolved schema/prompt
        registry data (if the backend supports enrichment).
        """
        resp = await self._get_http().get(f"{self.base_url}/pipeline")
        resp.raise_for_status()
        return resp.json()

    # -- sync operations (fetch verbatim API responses) -------------------

    async def fetch_experiments(self) -> dict[str, Any]:
        """GET /experiments — returns full response verbatim."""
        resp = await self._get_http().get(f"{self.base_url}/experiments")
        resp.raise_for_status()
        return resp.json()

    async def fetch_experiment(
        self, experiment_id: str, include_traces: bool = True,
    ) -> dict[str, Any]:
        """GET /experiments/{id}/mappings — returns full response verbatim.

        Uses the /mappings sub-endpoint which includes mappings, runs,
        and evaluation_results needed for replay.
        """
        params: dict[str, str] = {}
        if include_traces:
            params["include_traces"] = "true"
        resp = await self._get_http().get(
            f"{self.base_url}/experiments/{experiment_id}/mappings",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    # -- replay operations ------------------------------------------------

    async def init_session(self, terms: list[str]) -> dict[str, Any]:
        """POST /sessions with terms array.

        Idempotent: skips the HTTP call if already initialized with the
        same terms.  Stores ``terms`` internally so that ``run_match()``
        can auto-reinitialize the session if the backend restarts.
        """
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
        """POST /matches — forward node_config to the backend as-is."""
        payload: dict[str, Any] = {"query": query}

        _pp = pipeline_params or {}

        if "steps" in _pp:
            payload["steps"] = _pp["steps"]

        node_config: dict[str, dict] = _pp.get("node_config", {})

        # ranking_prompt shorthand → inject into node_config.llm_ranking.prompt
        if ranking_prompt:
            node_config.setdefault("llm_ranking", {})["prompt"] = ranking_prompt

        if node_config:
            payload["node_config"] = node_config

        client = self._get_http()
        resp = await client.post(
            f"{self.base_url}/matches",
            json=payload,
            timeout=MATCH_TIMEOUT,
        )

        # Auto-reinitialize session on 400 (lost after backend restart)
        if resp.status_code == 400 and self._session_terms:
            logger.warning("Got 400 from /matches — re-initializing session")
            terms = self._session_terms
            self._session_terms = None  # clear so idempotency guard re-sends
            await self.init_session(terms)
            resp = await client.post(
                f"{self.base_url}/matches",
                json=payload,
                timeout=MATCH_TIMEOUT,
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
        store.backends.save_sync(backend_id, f"experiments/{experiment_id}.json", detail)
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
            bom_material, process = parse_bom_material(query)

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

    async def replay_queries(
        self,
        queries: list[dict[str, Any]],
        terms: list[str],
        delay_between: float = 0.0,
        on_result: Callable[[dict[str, Any], int, int], Any] | None = None,
        pipeline_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Replay queries sequentially against the backend.

        Initializes a session with ``terms``, then runs each query.
        Returns a list of result dicts compatible with ExecutionResultItem.

        If ``on_result`` is provided, it is called after each query completes
        with ``(result_dict, index, total_count)``.  May be sync or async.
        """
        await self.init_session(terms)

        results: list[dict[str, Any]] = []
        total = len(queries)
        for i, q in enumerate(queries):
            start = time.time()
            try:
                response = await self.run_match(
                    q["query"],
                    pipeline_params=pipeline_params,
                )
                elapsed = time.time() - start
                data = response.get("data", {})
                ranked = data.get("ranked_candidates", [])
                top = ranked[0] if ranked else {}

                results.append(_build_result_dict(
                    q,
                    predicted=top.get("candidate", NO_RESULT),
                    confidence=top.get("relevance_score", 0),
                    ranked_candidates=ranked[:20],
                    latency_ms=round(elapsed * 1000, 1),
                    status="success",
                    pipeline_data=data,
                ))
            except Exception as e:
                elapsed = time.time() - start
                results.append(_build_result_dict(
                    q,
                    predicted="ERROR",
                    confidence=0.0,
                    ranked_candidates=[],
                    latency_ms=round(elapsed * 1000, 1),
                    status="error",
                    error=str(e),
                ))

            if on_result is not None:
                cb_result = on_result(results[-1], i, total)
                if inspect.isawaitable(cb_result):
                    await cb_result

            if i < len(queries) - 1:
                await asyncio.sleep(delay_between)

        return results
