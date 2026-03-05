"""Campaign initialization service.

Sets up project store, backend client, auto-syncs experiment data,
evaluates baselines, and returns a services dict ready for use.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TYPE_CHECKING

import httpx

from api.models.backend import BackendConnection
from api.services.backend_client import BackendClient
from api.services.constants import DATASET_NAME
from api.services.project_store import ProjectStore

if TYPE_CHECKING:
    from api.models.prompt_state import PromptState

logger = logging.getLogger(__name__)


async def init_services(
    backend_url: str = "http://127.0.0.1:8000",
    backend_id: str = "termnorm-local",
    experiment_id: str = "1_production_historical",
    project_root: Path | None = None,
    dataset_name: str | None = None,
) -> dict:
    """Initialize store, client, and load experiment data.

    If experiment data is not in the project store, attempts an automatic
    sync from the backend.  Connection errors are caught and logged so
    callers can still proceed (the user gets a clear message instead of
    a crash).

    When ``dataset_name`` is provided, loads ground-truth data from the
    ``DatasetStore`` instead of requiring experiment traces.  This allows
    the optimization workflow to run with Excel-sourced datasets.

    Args:
        backend_url: Backend base URL.
        backend_id: Backend identifier.
        experiment_id: Experiment to load.
        project_root: Project root directory.  Defaults to two levels up
            from the notebooks directory.
        dataset_name: If set, load this named dataset from the DatasetStore
            (e.g. "train") instead of extracting queries from experiment
            traces.

    Returns:
        Dict with keys: store, client, queries, terms, exp_data,
        backend_id, experiment_id, backend_client, session_terms,
        synced (bool indicating whether auto-sync was attempted).
    """
    if project_root is None:
        # Default: assume called from notebooks/ subdirectory
        project_root = Path(__file__).resolve().parent.parent

    store = ProjectStore(base_dir=project_root / ".promptpotter" / "projects")
    client = BackendClient(backend_url)

    if not store.backends.get(backend_id):
        store.backends.register(BackendConnection(
            id=backend_id, name="TermNorm Local",
            backend_type="termnorm", base_url=backend_url,
        ))

    # Fetch pipeline schema (best-effort — non-fatal)
    pipeline_schema = None
    try:
        from api.services.pipeline_discovery import parse_pipeline_response
        pipeline_resp = await client.fetch_pipeline()
        pipeline_schema = parse_pipeline_response(pipeline_resp)
        logger.info("Pipeline schema loaded: %s v%s", pipeline_schema.name, pipeline_schema.version)
    except Exception as exc:
        logger.info("Could not fetch pipeline schema: %s", exc)

    base = {
        "store": store,
        "backend_id": backend_id,
        "experiment_id": experiment_id,
        "backend_client": client,
        "pipeline_schema": pipeline_schema,
        "synced": False,
    }

    # --- Dataset store path (preferred when available) ---
    if dataset_name:
        ds = store.datasets.load(backend_id, dataset_name)
        if ds and ds.get("items"):
            items = ds["items"]
            session_terms = sorted({r["ground_truth"] for r in items if r.get("ground_truth")})
            logger.info(
                "Loaded dataset %r from store: %d items, %d session terms",
                dataset_name, len(items), len(session_terms),
            )
            return {
                **base,
                "queries": _dataset_items_to_queries(items),
                "exp_data": {},
                "session_terms": session_terms,
            }
        logger.info("Dataset %r not found in store, falling back to experiment sync", dataset_name)

    # --- Experiment sync path (original) ---
    exp_data = store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")

    # Detect stale sync data: data exists but has no traces
    _has_traces = bool(
        exp_data
        and exp_data.get("runs")
        and exp_data["runs"][0].get("traces")
    )

    synced = False
    if not exp_data or not _has_traces:
        reason = "No stored experiment data" if not exp_data else "Stored data has no traces"
        logger.info("%s — syncing from %s ...", reason, backend_url)
        try:
            await client.sync_experiments(store, backend_id, include_traces=True)
            exp_data = store.backends.load_sync(
                backend_id, f"experiments/{experiment_id}.json",
            )
            synced = True
        except Exception as exc:
            logger.warning("Auto-sync failed: %s", exc)

    base["synced"] = synced

    if not exp_data:
        logger.warning(
            "No experiment data available. "
            "Downstream calls will fail until data is synced or datasets are loaded."
        )
        return {
            **base,
            "queries": [],
            "exp_data": {},
            "session_terms": [],
        }

    queries = client.extract_replay_queries(exp_data)

    return {
        **base,
        "queries": queries,
        "exp_data": exp_data,
        "session_terms": client.extract_session_terms(exp_data),
    }


def _dataset_items_to_queries(items: list[dict]) -> list[dict]:
    """Convert DatasetStore items to the query format used by replay/eval."""
    queries = []
    for item in items:
        query = item.get("query", "")
        gt = item.get("ground_truth", "")
        if not query or not gt:
            continue
        queries.append({
            "query": query,
            "bom_material": query,
            "process": "",
            "query_fields": {"bom_material": query, "process": ""},
            "ground_truth": gt,
        })
    return queries


async def _wait_session_ready(
    backend_client: BackendClient,
    max_attempts: int = 5,
    delay: float = 1.0,
) -> None:
    """Poll check_status until session is active with terms loaded.

    Retries up to ``max_attempts`` times with ``delay`` seconds between.
    Logs a warning if the session never becomes ready.
    """
    for attempt in range(1, max_attempts + 1):
        status = await backend_client.check_status()
        data = status.get("data", {})
        if data.get("session_active") and data.get("terms_loaded", 0) > 0:
            logger.info(
                "Session ready (attempt %d/%d): %d terms loaded",
                attempt, max_attempts, data["terms_loaded"],
            )
            return
        logger.debug(
            "Session not ready (attempt %d/%d): session_active=%s, terms_loaded=%s",
            attempt, max_attempts,
            data.get("session_active"), data.get("terms_loaded"),
        )
        if attempt < max_attempts:
            await asyncio.sleep(delay)

    logger.warning(
        "Session not ready after %d attempts — evaluation may produce errors. "
        "Last status: %s", max_attempts, status,
    )


async def _verify_matches_liveness(
    backend_client: BackendClient,
    probe_query: str,
    max_attempts: int = 3,
    delay: float = 2.0,
) -> None:
    """Send a lightweight /matches probe to verify the endpoint is live.

    Retries on 400 (the exact symptom of an unready session).
    Non-fatal: logs a warning if all probes fail.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{backend_client.base_url}/matches",
                    json={"query": probe_query},
                )
                resp.raise_for_status()
            logger.info(
                "Matches liveness probe succeeded (attempt %d/%d)",
                attempt, max_attempts,
            )
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400 and attempt < max_attempts:
                logger.debug(
                    "Matches probe got 400 (attempt %d/%d), retrying in %.0fs",
                    attempt, max_attempts, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    "Matches liveness probe failed (attempt %d/%d): %s",
                    attempt, max_attempts, exc,
                )
                return
        except Exception as exc:
            logger.warning(
                "Matches liveness probe failed (attempt %d/%d): %s",
                attempt, max_attempts, exc,
            )
            return


async def run_baseline_eval(
    baseline: "PromptState",
    eval_data: list,
    backend_client: BackendClient,
    pipeline_params: dict | None = None,
    store: ProjectStore | None = None,
    backend_id: str = "",
    experiment_id: str = "",
    model: str = "",
    temperature: float = 0.0,
    on_result: Callable | None = None,
    obs: Any | None = None,
    session_terms: list[str] | None = None,
) -> tuple[list, list]:
    """Evaluate baseline prompt and build initial campaign_rounds list.

    Args:
        baseline: Baseline PromptState.
        eval_data: Evaluation data. If empty and store+experiment_id are
            provided, attempts to load from store.
        backend_client: BackendClient for evaluation.
        pipeline_params: Optional pipeline parameter overrides.
        store: Optional ProjectStore.
        backend_id: Backend identifier.
        experiment_id: Experiment to load eval data from if eval_data is empty.
        model: Model identifier for content hash.
        temperature: Temperature for content hash.
        on_result: Optional callback for progress reporting.
        obs: Optional ObsLogger for dataset registration.

    Returns:
        Tuple of (campaign_rounds, baseline_results).

    Raises:
        RuntimeError: If no evaluation data is available.
    """
    from api.models.search_point import SearchPoint
    from api.services.prompt_eval import evaluate_prompt_cached

    if not eval_data and store and experiment_id:
        from api.services.search.eval_dataset import load_eval_dataset
        eval_data = load_eval_dataset(store, backend_id, experiment_id)

    if not eval_data:
        raise RuntimeError(
            "No evaluation data available. "
            "Generate data first (e.g. run evaluation.ipynb or load from DatasetStore)."
        )

    # Initialize backend session so /matches doesn't 400
    if session_terms:
        await backend_client.init_session(session_terms)
        await _wait_session_ready(backend_client)
        await _verify_matches_liveness(backend_client, probe_query=session_terms[0])

    # Register dataset items in obs if available
    if obs and eval_data:
        try:
            obs.register_dataset(DATASET_NAME, eval_data)
        except Exception:
            logger.warning("Dataset registration in run_baseline_eval failed", exc_info=True)

    sp = SearchPoint(
        prompt_state=baseline,
        model=model,
        temperature=temperature,
        pipeline_params=pipeline_params,
    )
    baseline_results, scores, _cached = await evaluate_prompt_cached(
        sp, eval_data, backend_client,
        store=store, backend_id=backend_id,
        label="Baseline",
        on_result=on_result,
        obs=obs,
        source="baseline",
    )

    campaign_rounds = [{
        "round": 0, "label": "baseline", "prompt_state": baseline,
        "accuracy": scores["accuracy"], "hits": scores["hits"],
        "total": scores["total"], "results": baseline_results,
    }]

    return campaign_rounds, baseline_results
