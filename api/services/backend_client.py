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
    from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)

MATCH_TIMEOUT = 120.0

# Maps each pipeline step name to the set of parameter names it uses.
PIPELINE_STEP_PARAMS = {
    "web_search": {"max_sites", "num_results", "content_char_limit"},
    "entity_profiling": {"raw_content_limit", "profiling_temperature", "profiling_max_tokens"},
    "token_matching": {"max_token_candidates", "relevance_weight_core"},
    "llm_ranking": {
        "ranking_temperature", "ranking_max_tokens",
        "ranking_sample_size", "ranking_prompt",
    },
}


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
    pipeline_config: dict, overrides: dict | None = None,
) -> dict:
    """Build pipeline_params from a (possibly shortened) pipeline config.

    Returns dict ready for evaluate_prompt(..., pipeline_params=params).
    Includes 'steps' list (sent to TermNorm) and any user overrides.
    """
    step_names = [s["name"] for s in pipeline_config["steps"]]
    params: dict = {"steps": step_names}

    active_param_names: set = set()
    for name in step_names:
        active_param_names |= PIPELINE_STEP_PARAMS.get(name, set())

    if overrides:
        for k, v in overrides.items():
            if k in active_param_names:
                params[k] = v

    return params


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
        "bom_material": query_data["bom_material"],
        "process": query_data["process"],
        "query_fields": {
            "bom_material": query_data["bom_material"],
            "process": query_data["process"],
        },
        "ground_truth": query_data["ground_truth"],
        "predicted": predicted,
        "confidence": confidence,
        "ranked_candidates": ranked_candidates,
        "latency_ms": latency_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "variant_b_predicted": query_data.get("original_predicted", ""),
        "variant_b_latency_ms": query_data.get("original_latency_ms", 0),
        "variant_b_confidence": query_data.get("original_confidence", 0),
    }
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

    # -- sync operations (fetch verbatim API responses) -------------------

    async def fetch_experiments(self) -> dict[str, Any]:
        """GET /experiments — returns full response verbatim."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/experiments", timeout=self.timeout,
            )
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
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/experiments/{experiment_id}/mappings",
                params=params,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()

    # -- replay operations ------------------------------------------------

    async def init_session(self, terms: list[str]) -> dict[str, Any]:
        """POST /sessions with terms array."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/sessions",
                json={"terms": terms},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()

    async def run_match(
        self,
        query: str,
        skip_llm_ranking: bool = True,
        pipeline_params: dict[str, Any] | None = None,
        ranking_prompt: str | None = None,
    ) -> dict[str, Any]:
        """POST /matches — run a single query through the backend pipeline."""
        payload: dict[str, Any] = {
            "query": query,
            "skip_llm_ranking": skip_llm_ranking,
        }
        if pipeline_params:
            payload.update(pipeline_params)
        if ranking_prompt:
            payload["ranking_prompt"] = ranking_prompt
        async with httpx.AsyncClient() as client:
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
        skip_llm_ranking: bool = True,
        delay_between: float = 2.0,
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
                    skip_llm_ranking=skip_llm_ranking,
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
