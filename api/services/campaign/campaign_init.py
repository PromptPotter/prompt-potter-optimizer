"""Campaign initialization service.

Sets up project store, backend client, auto-syncs experiment data,
evaluates baselines, and returns a services dict ready for use.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from api.models.backend import BackendConnection
from api.models.opt_search_point import OptSearchPoint
from api.services.backend_client import BackendClient
from api.services.project_store import ProjectStore
from api.shared.constants import DATASET_NAME

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema

logger = logging.getLogger(__name__)


@dataclass
class InitResult:
    """Return value from ``init_services()``."""

    store: ProjectStore
    backend_id: str
    experiment_id: str
    backend_client: BackendClient
    pipeline_schema: PipelineSchema | None
    synced: bool
    queries: list[dict] = field(default_factory=list)
    exp_data: dict = field(default_factory=dict)
    session_terms: list[str] = field(default_factory=list)


@dataclass
class CampaignBaseline:
    """Extracted baseline state from campaign_rounds."""

    baseline_ps: dict | None
    baseline_acc: float
    baseline_results: list | None
    instruction: str


def extract_campaign_baseline(campaign_rounds: list[dict]) -> CampaignBaseline:
    """Extract baseline prompt state, accuracy, and results from campaign rounds.

    Searches reversed rounds for the last with actual eval ``results``,
    then overrides the prompt_fields with the tip (most recent round).
    """
    if not campaign_rounds:
        return CampaignBaseline(
            baseline_ps=None, baseline_acc=0.0,
            baseline_results=None, instruction="",
        )

    tip = campaign_rounds[-1]

    # Prefer accuracy from the last round with eval results; fall back to
    # the tip's accuracy (e.g. scan winner carries accuracy but no results).
    baseline_acc = tip.get("accuracy", 0.0)
    baseline_results: list = []
    for rd in reversed(campaign_rounds):
        if rd.get("results"):
            baseline_acc = rd.get("accuracy", baseline_acc)
            baseline_results = rd["results"]
            break

    tip_ps = tip["prompt_fields"]

    return CampaignBaseline(
        baseline_ps=tip_ps,
        baseline_acc=baseline_acc,
        baseline_results=baseline_results,
        instruction=tip_ps.instruction,
    )


def load_baseline_prompt(
    exp_data: dict,
    prompt_node_names: list[str] | None = None,
) -> OptSearchPoint:
    """Extract the prompt-bearing node's template from experiment data.

    Searches ``dependencies.prompts`` for a key matching any name in
    ``prompt_node_names`` (from ``PipelineSchema.prompt_node_names()``).

    Args:
        exp_data: Synced experiment data dict with ``dependencies.prompts``.
        prompt_node_names: Node names to search for in prompt registry keys.

    Returns:
        OptSearchPoint with the baseline prompt as ``instruction``.

    Raises:
        RuntimeError: If no matching prompt is found.
    """
    dependencies = exp_data.get("dependencies", {})
    prompts = dependencies.get("prompts", {})
    names = prompt_node_names or []

    matched_prompt = None
    matched_key = None
    for node_name in names:
        for key, prompt_info in prompts.items():
            if node_name in key:
                matched_prompt = prompt_info
                matched_key = key
                break
        if matched_prompt:
            break

    # Fallback: no node names provided but prompts exist — use the first one
    if matched_prompt is None and not names and prompts:
        matched_key, matched_prompt = next(iter(prompts.items()))

    if matched_prompt is None:
        raise RuntimeError(
            f"No prompt found for nodes {names} in synced experiment data. "
            "Re-sync the experiment after the prompt registry is initialized."
        )

    label = names[0] if names else matched_key
    return OptSearchPoint(
        instruction=matched_prompt["template"],
        changes_description=f"Baseline prompt from {label} registry",
    )


async def init_services(
    backend_url: str = "http://127.0.0.1:8000",
    backend_id: str = "termnorm-local",
    experiment_id: str = "1_production_historical",
    project_root: Path | None = None,
    dataset_name: str | None = None,
    on_status: Callable[[str], None] | None = None,
) -> InitResult:
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
        InitResult with all service handles and loaded data.
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
        store.backends.register(
            BackendConnection(
                id=backend_id,
                name="TermNorm Local",
                backend_type="termnorm",
                base_url=backend_url,
            )
        )

    # Fetch pipeline schema (best-effort — non-fatal)
    pipeline_schema = None
    try:
        from api.services.pipeline_discovery import parse_pipeline_response

        pipeline_resp = await client.fetch_pipeline()
        pipeline_schema = parse_pipeline_response(pipeline_resp)
        logger.info("Pipeline schema loaded: %s v%s", pipeline_schema.name, pipeline_schema.version)
        _status(f"Pipeline: {pipeline_schema.name} ({len(pipeline_schema.nodes)} nodes)")
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.info("Could not fetch pipeline schema: %s", exc)
        _status("Pipeline: unavailable")

    base = InitResult(
        store=store,
        backend_id=backend_id,
        experiment_id=experiment_id,
        backend_client=client,
        pipeline_schema=pipeline_schema,
        synced=False,
    )

    # --- Dataset store path (preferred when available) ---
    if dataset_name:
        ds = store.backends.load_dataset(backend_id, dataset_name)
        if ds and ds.get("items"):
            items = ds["items"]
            session_terms = sorted({r["ground_truth"] for r in items if r.get("ground_truth")})
            logger.info(
                "Loaded dataset %r from store: %d items, %d session terms",
                dataset_name,
                len(items),
                len(session_terms),
            )
            _status(f"Dataset: {dataset_name} ({len(items)} queries)")
            base.queries = _dataset_items_to_queries(items)
            base.session_terms = session_terms
            return base
        logger.info("Dataset %r not found in store, falling back to experiment sync", dataset_name)
        _status(f"Dataset '{dataset_name}' not found, falling back to experiment sync")

    # --- Experiment sync path (original) ---
    exp_data = store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")

    # Detect stale sync data: data exists but has no traces
    _has_traces = bool(exp_data and exp_data.get("runs") and exp_data["runs"][0].get("traces"))

    synced = False
    if not exp_data or not _has_traces:
        reason = "No stored experiment data" if not exp_data else "Stored data has no traces"
        logger.info("%s — syncing from %s ...", reason, backend_url)
        _status(f"Syncing experiment {experiment_id} ...")
        try:
            exp_data = await client.sync_experiment(
                store,
                backend_id,
                experiment_id,
                include_traces=True,
            )
            synced = True
            _status("Sync complete")
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("Auto-sync failed: %s", exc)
            _status(f"Sync failed: {exc}")

    base.synced = synced

    if not exp_data:
        logger.warning(
            "No experiment data available. "
            "Downstream calls will fail until data is synced or datasets are loaded."
        )
        _status("WARNING: No experiment data available")
        return base

    queries = client.extract_replay_queries(exp_data)
    session_terms = client.extract_session_terms(exp_data)
    exp_name = exp_data.get("experiment", {}).get("name", experiment_id)
    _status(f"Experiment: {exp_name} ({len(queries)} queries, {len(session_terms)} session terms)")

    base.queries = queries
    base.exp_data = exp_data
    base.session_terms = session_terms
    return base


def _dataset_items_to_queries(items: list[dict]) -> list[dict]:
    """Convert DatasetStore items to the query format used by replay/eval."""
    queries = []
    for item in items:
        query = item.get("query", "")
        gt = item.get("ground_truth", "")
        if not query or not gt:
            continue
        queries.append(
            {
                "query": query,
                "bom_material": query,
                "process": "",
                "query_fields": {"bom_material": query, "process": ""},
                "ground_truth": gt,
            }
        )
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
                attempt,
                max_attempts,
                data["terms_loaded"],
            )
            return
        logger.debug(
            "Session not ready (attempt %d/%d): session_active=%s, terms_loaded=%s",
            attempt,
            max_attempts,
            data.get("session_active"),
            data.get("terms_loaded"),
        )
        if attempt < max_attempts:
            await asyncio.sleep(delay)

    logger.warning(
        "Session not ready after %d attempts — evaluation may produce errors. Last status: %s",
        max_attempts,
        status,
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
            await backend_client.run_match(probe_query)
            logger.info(
                "Matches liveness probe succeeded (attempt %d/%d)",
                attempt,
                max_attempts,
            )
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400 and attempt < max_attempts:
                logger.debug(
                    "Matches probe got 400 (attempt %d/%d), retrying in %.0fs",
                    attempt,
                    max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    "Matches liveness probe failed (attempt %d/%d): %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                return
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning(
                "Matches liveness probe failed (attempt %d/%d): %s",
                attempt,
                max_attempts,
                exc,
            )
            return


async def run_baseline_eval(
    baseline: OptSearchPoint,
    eval_data: list,
    backend_client: BackendClient,
    pipeline_params: dict | None = None,
    store: ProjectStore | None = None,
    backend_id: str = "",
    experiment_id: str = "",
    on_result: Callable | None = None,
    obs: Any | None = None,
    session_terms: list[str] | None = None,
    prompt_node: str = "",
    pipeline_schema: Any | None = None,
) -> tuple[list, list]:
    """Evaluate baseline prompt and build initial campaign_rounds list.

    Args:
        baseline: Baseline OptSearchPoint.
        eval_data: Evaluation data. If empty and store+experiment_id are
            provided, attempts to load from store.
        backend_client: BackendClient for evaluation.
        pipeline_params: Optional pipeline parameter overrides.
        store: Optional ProjectStore.
        backend_id: Backend identifier.
        experiment_id: Experiment to load eval data from if eval_data is empty.
        on_result: Optional callback for progress reporting.
        obs: Optional ObsLogger for dataset registration.
        pipeline_schema: Optional PipelineSchema for composite scoring.

    Returns:
        Tuple of (campaign_rounds, baseline_results).

    Raises:
        RuntimeError: If no evaluation data is available.
    """
    from api.models.eval_context import EvalContext
    from api.services.prompt_eval import eval_search_point

    if not eval_data and store and experiment_id:
        from api.services.search.eval_dataset import load_eval_dataset

        eval_data = load_eval_dataset(store, backend_id, experiment_id)

    if not eval_data:
        raise RuntimeError(
            "No evaluation data available. "
            "Generate data first (e.g. load from DatasetStore or sync from backend)."
        )

    # Initialize backend session so /matches doesn't 400
    if session_terms:
        await backend_client.init_session(session_terms)
        await _wait_session_ready(backend_client)
        await _verify_matches_liveness(backend_client, probe_query=session_terms[0])
    else:
        logger.warning(
            "No session terms available — /matches calls will fail. "
            "Load datasets first (Excel ground truth → DatasetStore)."
        )

    # Register dataset items in obs if available
    if obs and eval_data:
        from api.services.campaign.helpers import graceful

        with graceful("Dataset registration in run_baseline_eval failed"):
            obs.register_dataset(DATASET_NAME, eval_data)

    _steps = (pipeline_params or {}).get("steps", [])
    sp = baseline.to_job_search_point(
        base_pipeline_params=pipeline_params,
        active_steps=_steps or None,
        prompt_node=prompt_node,
    )
    ctx = EvalContext(
        backend_client=backend_client,
        store=store,
        backend_id=backend_id,
        pipeline_schema=pipeline_schema,
        obs=obs,
        source="baseline",
    )
    baseline_results, scores, _cached = await eval_search_point(
        sp,
        eval_data,
        ctx,
        label="Baseline",
        on_result=on_result,
    )

    campaign_rounds = [
        {
            "round": 0,
            "label": "baseline",
            "prompt_fields": baseline,
            "accuracy": scores["accuracy"],
            "hits": scores["hits"],
            "total": scores["total"],
            "results": baseline_results,
        }
    ]

    return campaign_rounds, baseline_results


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
        ds = store.backends.load_dataset(backend_id, name)
        if ds and ds.get("items"):
            for item in ds["items"]:
                gt = item.get("ground_truth", "").strip()
                if gt:
                    gt_set.add(gt)
    return sorted(gt_set)


