"""Campaign initialization service.

Sets up project store, backend client, auto-syncs experiment data,
evaluates baselines, and returns a services dict ready for use.
"""
from __future__ import annotations

import logging
import time
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
    from api.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)


def init_services(
    backend_url: str = "http://127.0.0.1:8000",
    backend_id: str = "termnorm-local",
    experiment_id: str = "1_production_historical",
    project_root: Path | None = None,
    dataset_name: str | None = None,
    on_status: Callable[[str], None] | None = None,
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
    def _status(msg: str) -> None:
        if on_status:
            on_status(msg)

    if project_root is None:
        # Default: assume called from notebooks/ subdirectory
        project_root = Path(__file__).resolve().parent.parent

    store = ProjectStore(base_dir=project_root / ".promptpotter" / "projects")
    client = BackendClient(backend_url)
    _status(f"Backend: {backend_url}")

    if not store.backends.get(backend_id):
        store.backends.register(BackendConnection(
            id=backend_id, name="TermNorm Local",
            backend_type="termnorm", base_url=backend_url,
        ))

    # Fetch pipeline schema (best-effort — non-fatal)
    pipeline_schema = None
    try:
        from api.services.pipeline_discovery import parse_pipeline_response
        pipeline_resp = client.fetch_pipeline()
        pipeline_schema = parse_pipeline_response(pipeline_resp)
        logger.info("Pipeline schema loaded: %s v%s", pipeline_schema.name, pipeline_schema.version)
        _status(f"Pipeline: {pipeline_schema.name} ({len(pipeline_schema.steps)} steps)")
    except Exception as exc:
        logger.info("Could not fetch pipeline schema: %s", exc)
        _status("Pipeline: unavailable")

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
            _status(f"Dataset: {dataset_name} ({len(items)} queries)")
            return {
                **base,
                "queries": _dataset_items_to_queries(items),
                "exp_data": {},
                "session_terms": session_terms,
            }
        logger.info("Dataset %r not found in store, falling back to experiment sync", dataset_name)
        _status(f"Dataset '{dataset_name}' not found, falling back to experiment sync")

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
        _status(f"Syncing experiment {experiment_id} ...")
        try:
            exp_data = client.sync_experiment(
                store, backend_id, experiment_id, include_traces=True,
            )
            synced = True
            _status("Sync complete")
        except Exception as exc:
            logger.warning("Auto-sync failed: %s", exc)
            _status(f"Sync failed: {exc}")

    base["synced"] = synced

    if not exp_data:
        logger.warning(
            "No experiment data available. "
            "Downstream calls will fail until data is synced or datasets are loaded."
        )
        _status("WARNING: No experiment data available")
        return {
            **base,
            "queries": [],
            "exp_data": {},
            "session_terms": [],
        }

    queries = client.extract_replay_queries(exp_data)
    session_terms = client.extract_session_terms(exp_data)
    exp_name = exp_data.get("experiment", {}).get("name", experiment_id)
    _status(f"Experiment: {exp_name} ({len(queries)} queries, "
            f"{len(session_terms)} session terms)")

    return {
        **base,
        "queries": queries,
        "exp_data": exp_data,
        "session_terms": session_terms,
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


def _wait_session_ready(
    backend_client: BackendClient,
    max_attempts: int = 5,
    delay: float = 1.0,
) -> None:
    """Poll check_status until session is active with terms loaded.

    Retries up to ``max_attempts`` times with ``delay`` seconds between.
    Logs a warning if the session never becomes ready.
    """
    for attempt in range(1, max_attempts + 1):
        status = backend_client.check_status()
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
            time.sleep(delay)

    logger.warning(
        "Session not ready after %d attempts — evaluation may produce errors. "
        "Last status: %s", max_attempts, status,
    )


def _verify_matches_liveness(
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
            backend_client.run_match(probe_query)
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
                time.sleep(delay)
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


def run_baseline_eval(
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
    from api.services.prompt_eval import EvalContext, evaluate_prompt_cached

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
        backend_client.init_session(session_terms)
        _wait_session_ready(backend_client)
        _verify_matches_liveness(backend_client, probe_query=session_terms[0])

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
    ctx = EvalContext(
        backend_client=backend_client,
        store=store,
        backend_id=backend_id,
        obs=obs,
        source="baseline",
        model=model,
        temperature=temperature,
        pipeline_params=pipeline_params,
    )
    baseline_results, scores, _cached = evaluate_prompt_cached(
        sp, eval_data, ctx,
        label="Baseline",
        on_result=on_result,
    )

    campaign_rounds = [{
        "round": 0, "label": "baseline", "prompt_state": baseline,
        "accuracy": scores["accuracy"], "hits": scores["hits"],
        "total": scores["total"], "results": baseline_results,
    }]

    return campaign_rounds, baseline_results


# ---------------------------------------------------------------------------
# Functions extracted from notebooks/_campaign_lib
# ---------------------------------------------------------------------------


def resolve_experiment_id(
    store: ProjectStore, backend_id: str, short_id: str,
) -> str | None:
    """Resolve short prefix/suffix to full campaign_id."""
    campaigns = store.campaigns.list_all(backend_id)
    matches = [c for c in campaigns if short_id in c["campaign_id"]]
    if len(matches) == 1:
        return matches[0]["campaign_id"]
    if len(matches) > 1:
        logger.warning(
            "Ambiguous ID '%s' — %d matches: %s",
            short_id, len(matches),
            [m["campaign_id"] for m in matches],
        )
        return None
    logger.warning("No campaign matching '%s'", short_id)
    return None


def apply_experiment_overrides(
    campaign_config: dict,
    stored_cfg: dict,
) -> dict | None:
    """Merge stored experiment config into campaign_config (in-place).

    Returns updated pipeline_params if stored, else None.
    """
    _OVERRIDE_KEYS: dict[str, tuple[str, ...]] = {
        "patience": ("optimization",),
        "max_rounds": ("optimization",),
        "n_variants": ("optimization",),
        "creativity": ("optimization",),
        "model": ("eval_llm",),
        "sample_size": (),
    }
    for key, path in _OVERRIDE_KEYS.items():
        val = stored_cfg.get(key)
        if val is not None:
            target = campaign_config
            for p in path:
                target = target.setdefault(p, {})
            target[key] = val

    stored_pp = stored_cfg.get("pipeline_params")
    if stored_pp:
        campaign_config["pipeline_params"] = stored_pp
        return stored_pp
    return None


def save_campaign_winner(
    campaign_rounds: list,
    campaign_config: dict,
    store: ProjectStore,
    backend_id: str,
    *,
    experiment_id: str | None = None,
) -> dict:
    """Find best round, save to store + link to campaign. Returns save_data dict."""
    from datetime import datetime, timezone

    winner = campaign_rounds[-1]["prompt_state"]
    winner_acc = campaign_rounds[-1]["accuracy"]

    for rd in campaign_rounds:
        if rd["accuracy"] > winner_acc:
            winner = rd["prompt_state"]
            winner_acc = rd["accuracy"]

    baseline_acc = campaign_rounds[0]["accuracy"] if campaign_rounds else None
    save_data = {
        "winner": winner.model_dump(),
        "accuracy": winner_acc,
        "campaign_rounds": len(campaign_rounds),
        "baseline_accuracy": baseline_acc,
        "improvement": (winner_acc - baseline_acc) if baseline_acc is not None else None,
        "config": campaign_config,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    filename = f"optimization/campaign_winner_{winner.id[:12]}.json"
    store.backends.save_sync(backend_id, filename, save_data)

    # Link winner to campaign store if experiment_id provided
    if experiment_id:
        full_id = resolve_experiment_id(store, backend_id, experiment_id)
        if full_id:
            try:
                store.campaigns.update(backend_id, full_id, {
                    "winner_prompt_state_id": winner.id,
                    "winner_accuracy": winner_acc,
                    "winner_filename": filename,
                })
            except Exception:
                pass  # campaign may not exist yet

    logger.info("Winner saved: %s (acc=%.1f%%)", filename, winner_acc * 100)
    return {
        **save_data,
        "winner_id": winner.id,
        "filename": filename,
        "backend_id": backend_id,
    }


def build_all_session_terms(
    store: ProjectStore,
    backend_id: str,
) -> list[str]:
    """Unique ground_truth identifiers across all stored datasets (train + test).

    For /match to work correctly, the session must contain ALL identifiers:
    - Train: query->ground_truth mappings (used for optimization evaluation)
    - Test: ground_truth only (identifiers in candidate pool, no query mapping)
    """
    gt_set: set[str] = set()
    for name in ("train", "test_processes", "test_material"):
        ds = store.datasets.load(backend_id, name)
        if ds and ds.get("items"):
            for item in ds["items"]:
                gt = item.get("ground_truth", "").strip()
                if gt:
                    gt_set.add(gt)
    return sorted(gt_set)


def create_llm_client(
    campaign_config: dict,
) -> tuple["LLMClientBase", str]:
    """Create LLM client + model from campaign_config['eval_llm'].

    Returns:
        Tuple of (llm_client, model_name).
    """
    from api.services.llm_client import get_llm_client

    eval_llm = campaign_config["eval_llm"]
    url = eval_llm.get("provider_url", "")
    if "anthropic.com" in url:
        provider = "anthropic"
    elif "openai.com" in url:
        provider = "openai"
    else:
        provider = "groq"
    return get_llm_client(provider), eval_llm.get("model", "")
