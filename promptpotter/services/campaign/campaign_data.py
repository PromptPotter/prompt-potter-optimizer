"""Campaign data loading and baseline evaluation.

Loads datasets, evaluates baselines, and prepares evaluation contexts
for campaign optimization loops.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.services.campaign.bootstrap import BackendContext, load_baseline_prompt
from promptpotter.services.campaign.config import CampaignConfig
from promptpotter.shared.constants import DATASET_NAME

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.project_store import ProjectStore


logger = logging.getLogger(__name__)

__all__ = [
    "CampaignBaseline",
    "DatasetSummary",
    "build_all_index_terms",
    "extract_campaign_baseline",
    "prepare_datasets",
    "prepare_eval_context",
    "run_baseline_eval",
]


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
            baseline_ps=None,
            baseline_acc=0.0,
            baseline_results=None,
            instruction="",
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


async def run_baseline_eval(
    baseline: OptSearchPoint,
    dataset: list,
    session: BackendContext,
    pipeline_params: dict | None = None,
    experiment_id: str = "",
    on_result: Callable | None = None,
    obs: Any | None = None,
    pipeline_schema: Any | None = None,
    scoring_formula: str | None = None,
) -> tuple[list, list]:
    """Evaluate baseline prompt and build initial campaign_rounds list.

    Args:
        baseline: Baseline OptSearchPoint.
        dataset: Evaluation data. If empty and store+experiment_id are
            provided, attempts to load from store.
        session: BackendContext bundling backend_client, store, backend_id, index_terms.
        pipeline_params: Optional pipeline parameter overrides.
        experiment_id: Experiment to load eval data from if dataset is empty.
        on_result: Optional callback for progress reporting.
        obs: Optional ObsLogger for dataset registration.
        pipeline_schema: Optional PipelineSchema for composite scoring.
        scoring_formula: Optional per-dataset scoring expression.

    Returns:
        Tuple of (campaign_rounds, baseline_results).

    Raises:
        RuntimeError: If no evaluation data is available.
    """
    from promptpotter.models.eval_context import EvalContext
    from promptpotter.services.eval_gateway import eval_search_point
    from promptpotter.shared.errors import graceful
    from promptpotter.shared.scoring import compile_scorer

    # Unpack session
    backend_client = session.backend_client
    store = session.store
    backend_id = session.backend_id
    index_terms = session.index_terms

    if not dataset and store and experiment_id:
        from promptpotter.services.search.context import load_eval_dataset

        dataset = load_eval_dataset(store, backend_id, experiment_id)

    if not dataset:
        raise RuntimeError(
            "No evaluation data available. "
            "Generate data first (e.g. load from DatasetStore or sync from backend)."
        )

    # Initialize backend session so /matches doesn't 400
    if index_terms:
        await backend_client.init_session(index_terms)
    else:
        logger.warning(
            "No session terms available — /matches calls will fail. "
            "Load datasets first (Excel ground truth → DatasetStore)."
        )

    # Register dataset items in obs if available
    if obs and dataset:
        with graceful("Dataset registration in run_baseline_eval failed"):
            obs.register_dataset(DATASET_NAME, dataset)

    sp = baseline.to_job_search_point(
        base_pipeline_params=pipeline_params,
        schema=pipeline_schema,
    )
    ctx = EvalContext(
        backend_client=backend_client,
        store=store,
        backend_id=backend_id,
        pipeline_schema=pipeline_schema,
        obs=obs,
        source="baseline",
        scorer=compile_scorer(scoring_formula),
    )
    baseline_results, scores, _cached = await eval_search_point(
        sp,
        dataset,
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


async def prepare_eval_context(
    exp_data: dict | None,
    train_data: list[dict] | None,
    campaign_config: CampaignConfig | None = None,
    *,
    run_baseline: bool = False,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    svc: Any = None,
) -> tuple[OptSearchPoint, list[dict], list, list]:
    """Load baseline prompt, set dataset, optionally run baseline eval."""
    prompt_nodes = pipeline_schema.prompt_node_names() if pipeline_schema else []
    baseline = load_baseline_prompt(exp_data or {}, prompt_node_names=prompt_nodes)
    dataset = train_data or []

    campaign_rounds: list = []
    baseline_results: list = []
    if run_baseline and campaign_config is not None and svc is not None:
        campaign_rounds, baseline_results = await run_baseline_eval(
            baseline,
            dataset,
            svc,
            pipeline_params=pipeline_params,
            pipeline_schema=pipeline_schema,
            scoring_formula=campaign_config.get("scoring"),
        )

    return baseline, dataset, campaign_rounds, baseline_results


@dataclass
class DatasetSummary:
    """Return from ``prepare_datasets()``."""

    train_data: list[dict] | None
    index_terms: list[str]
    splits: dict[str, list[dict]]
    n_unique_queries: int


def prepare_datasets(
    store: ProjectStore,
    backend_id: str,
    excel_path: str | Path | None = None,
    *,
    force: bool = False,
) -> DatasetSummary:
    """Load/create datasets and build session terms.

    Pure orchestration — no display.  The notebook wrapper prints the
    summary table.

    Returns:
        DatasetSummary with train_data, index_terms, splits dict, and unique query count.
    """
    from promptpotter.services.dataset_builder import (
        SHEET_COLUMN_MAP,
        load_excel_ground_truth,
        train_test_split,
    )

    if excel_path:
        excel_path = Path(excel_path)
        existing = store.backends.load_dataset(backend_id, "train")
        needs_create = force or not (existing and existing.get("items"))

        if needs_create:
            all_rows = load_excel_ground_truth(excel_path, SHEET_COLUMN_MAP)
            train, test_sets = train_test_split(all_rows)
            store.backends.save_dataset(backend_id, "train", train, source_file=excel_path.name)
            for name, items in test_sets.items():
                store.backends.save_dataset(backend_id, name, items, source_file=excel_path.name)

    splits: dict[str, list[dict]] = {}
    for name in ("train", "test_processes", "test_material"):
        ds = store.backends.load_dataset(backend_id, name)
        splits[name] = ds["items"] if ds and ds.get("items") else []

    train_data = splits["train"] or None
    index_terms = build_all_index_terms(store, backend_id)

    all_queries: set[str] = set()
    for items in splits.values():
        for item in items:
            q = item.get("query", "").strip()
            if q:
                all_queries.add(q)

    return DatasetSummary(
        train_data=train_data,
        index_terms=index_terms,
        splits=splits,
        n_unique_queries=len(all_queries),
    )


def build_all_index_terms(
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


@dataclass
class DatasetRunSummary:
    """Aggregated dataset run statistics for dashboard display."""

    total: int
    by_source: dict[str, int]
    best_accuracy: float
    best_name: str


def summarize_dataset_runs(runs: list[dict]) -> DatasetRunSummary:
    """Aggregate dataset runs by source prefix and find best accuracy."""
    by_source: dict[str, int] = {}
    best_acc = 0.0
    best_name = ""
    for r in runs:
        name = r.get("name", "")
        source = name.split("_")[0] if "_" in name else "other"
        by_source[source] = by_source.get(source, 0) + 1
        acc = r.get("scores", {}).get("accuracy", 0.0) or 0.0
        if acc > best_acc:
            best_acc = acc
            best_name = name
    return DatasetRunSummary(
        total=len(runs),
        by_source=by_source,
        best_accuracy=best_acc,
        best_name=best_name,
    )
