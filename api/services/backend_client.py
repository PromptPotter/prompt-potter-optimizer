"""
HTTP client for backend APIs (e.g. TermNorm).

Fetches experiments, syncs data into the project store, and replays
pipeline queries. All API responses stored verbatim.
"""

import asyncio
import time
from datetime import datetime, timezone
import inspect
from typing import Any, Callable, Dict, List, Optional

import httpx


class BackendClient:
    """HTTP client for a single backend instance."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- sync operations (fetch verbatim API responses) -------------------

    async def fetch_experiments(self) -> Dict[str, Any]:
        """GET /experiments — returns full response verbatim."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/experiments", timeout=self.timeout
            )
            resp.raise_for_status()
            return resp.json()

    async def fetch_experiment(
        self, experiment_id: str, include_traces: bool = True,
    ) -> Dict[str, Any]:
        """GET /experiments/{id}/mappings — returns full response verbatim.

        Uses the /mappings sub-endpoint which includes mappings, runs,
        and evaluation_results needed for replay.

        Args:
            experiment_id: Experiment identifier.
            include_traces: When True, request Langfuse-style trace data
                from the backend (pipeline observations per query).
        """
        params: Dict[str, str] = {}
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

    async def init_session(self, terms: List[str]) -> Dict[str, Any]:
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
        pipeline_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """POST /matches — run a single query through the backend pipeline."""
        payload: Dict[str, Any] = {
            "query": query,
            "skip_llm_ranking": skip_llm_ranking,
        }
        if pipeline_params:
            payload.update(pipeline_params)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/matches",
                json=payload,
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json()

    # -- high-level sync --------------------------------------------------

    async def sync_experiments(
        self, store: "ProjectStore", backend_id: str,
        include_traces: bool = True,
    ) -> int:
        """Fetch all experiments and store verbatim. Returns count."""
        from api.services.project_store import ProjectStore  # avoid circular

        data = await self.fetch_experiments()
        store.save_sync(backend_id, "experiments.json", data)

        experiments = data.get("experiments", [])
        for exp in experiments:
            exp_id = exp.get("experiment_id", exp.get("id", ""))
            if exp_id:
                detail = await self.fetch_experiment(
                    exp_id, include_traces=include_traces,
                )
                store.save_sync(
                    backend_id, f"experiments/{exp_id}.json", detail
                )
        return len(experiments)

    async def sync_experiment(
        self, store: "ProjectStore", backend_id: str, experiment_id: str,
        include_traces: bool = True,
    ) -> Dict[str, Any]:
        """Fetch one experiment and store verbatim."""
        detail = await self.fetch_experiment(
            experiment_id, include_traces=include_traces,
        )
        store.save_sync(backend_id, f"experiments/{experiment_id}.json", detail)
        return detail

    # -- replay helpers ---------------------------------------------------

    @staticmethod
    def extract_session_terms(experiment_data: Dict) -> List[str]:
        """Extract unique non-empty dataset_entry values from mappings."""
        entries = set()
        for m in experiment_data.get("mappings", []):
            entry = m.get("dataset_entry", "").strip()
            if entry and entry != "--":
                entries.add(entry)
        return sorted(entries)

    @staticmethod
    def extract_replay_queries(experiment_data: Dict) -> List[Dict[str, Any]]:
        """Extract queries with valid ground truth from experiment data.

        Matches evaluation_result queries back to mappings via bom_material.
        """
        bom_to_gt: Dict[str, str] = {}
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
            if "/" in query:
                last_slash = query.rfind("/")
                bom_material = query[:last_slash].strip()
                process = query[last_slash + 1 :].strip()
            else:
                bom_material = query.strip()
                process = ""

            if bom_material not in bom_to_gt:
                continue

            queries.append(
                {
                    "query": query,
                    "bom_material": bom_material,
                    "process": process,
                    "ground_truth": bom_to_gt[bom_material],
                    "original_predicted": er.get("predicted", ""),
                    "original_latency_ms": er.get("latency_ms", 0),
                    "original_confidence": er.get("confidence", 0),
                }
            )

        return queries

    async def replay_queries(
        self,
        queries: List[Dict[str, Any]],
        terms: List[str],
        skip_llm_ranking: bool = True,
        delay_between: float = 2.0,
        on_result: Optional[Callable[[Dict[str, Any], int, int], Any]] = None,
        pipeline_params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Replay queries sequentially against the backend.

        Initializes a session with ``terms``, then runs each query.
        Returns a list of result dicts compatible with ExecutionResultItem.

        If ``on_result`` is provided, it is called after each query completes
        with ``(result_dict, index, total_count)``.  May be sync or async.
        """
        await self.init_session(terms)

        results: List[Dict[str, Any]] = []
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

                results.append(
                    {
                        "query": q["query"],
                        "bom_material": q["bom_material"],
                        "process": q["process"],
                        "ground_truth": q["ground_truth"],
                        "predicted": top.get("candidate", "NO_RESULT"),
                        "confidence": top.get("relevance_score", 0),
                        "ranked_candidates": ranked[:20],
                        "latency_ms": round(elapsed * 1000, 1),
                        "web_search_status": data.get("web_search_status"),
                        "pipeline_data": data,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "success",
                        "variant_b_predicted": q.get("original_predicted", ""),
                        "variant_b_latency_ms": q.get("original_latency_ms", 0),
                        "variant_b_confidence": q.get("original_confidence", 0),
                    }
                )
            except Exception as e:
                elapsed = time.time() - start
                results.append(
                    {
                        "query": q["query"],
                        "bom_material": q["bom_material"],
                        "process": q["process"],
                        "ground_truth": q["ground_truth"],
                        "status": "error",
                        "error": str(e),
                        "predicted": "ERROR",
                        "confidence": 0.0,
                        "ranked_candidates": [],
                        "latency_ms": round(elapsed * 1000, 1),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "variant_b_predicted": q.get("original_predicted", ""),
                        "variant_b_latency_ms": q.get("original_latency_ms", 0),
                        "variant_b_confidence": q.get("original_confidence", 0),
                    }
                )

            if on_result is not None:
                cb_result = on_result(results[-1], i, total)
                if inspect.isawaitable(cb_result):
                    await cb_result

            if i < len(queries) - 1:
                await asyncio.sleep(delay_between)

        return results
