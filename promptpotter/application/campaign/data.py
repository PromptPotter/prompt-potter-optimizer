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

from promptpotter.application.campaign.campaign_setup import SessionEnv, load_baseline_prompt
from promptpotter.application.campaign.config import CampaignConfig
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.sample import Sample
from promptpotter.shared.constants import DATASET_NAME

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores


logger = logging.getLogger(__name__)

__all__ = [
    "CampaignBaseline",
    "DatasetRunSummary",
    "DatasetSummary",
    "build_all_index_terms",
    "extract_campaign_baseline",
    "prepare_datasets",
    "prepare_scoring_context",
    "summarize_dataset_runs",
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
            baseline_ps={},
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


async def _run_baseline_scoring(
    baseline: OptSearchPoint,
    dataset: list,
    session: SessionEnv,
    pipeline_params: dict | None = None,
    listener: Any | None = None,
    obs: Any | None = None,
    pipeline_schema: Any | None = None,
    scoring_formula: str | None = None,
    scoring_round_formula: str | None = None,
    scorer_id: str | None = None,
) -> tuple[list, list]:
    """Score the baseline prompt and build initial campaign_rounds list."""
    from promptpotter.application.optimization.phases import CampaignPhase, emit_phase
    from promptpotter.application.scoring.search_point_scorer import score_search_point
    from promptpotter.domain.scoring import ScoringEnv
    from promptpotter.shared.errors import graceful

    backend_client = session.backend_client
    store = session.store
    backend_id = session.backend_id
    index_terms = session.index_terms

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
        with graceful("Dataset registration in run_baseline_scoring failed"):
            obs.register_dataset(DATASET_NAME, dataset)

    sp = baseline.to_job_search_point(
        base_pipeline_params=pipeline_params,
        schema=pipeline_schema,
    )
    # Go through ScoringEnv.for_baseline so the trace's ``scored`` audit
    # map is keyed by the active scorer_id, matching the loop's bookkeeping.
    ctx = ScoringEnv.for_baseline(
        backend_client,
        store,
        backend_id,
        pipeline_schema,
        obs,
        scoring_formula=scoring_formula,
        scoring_round_formula=scoring_round_formula,
        scorer_id=scorer_id,
    )

    # Wrap the listener with baseline pseudo-candidate coords (ci=0/ct=1) so
    # the dashboard emitter ticks per-query during baseline exactly as it
    # does during L1 scoring.  Without this, baseline is invisible on disk.
    on_start_cb: Callable | None = None
    on_result_cb: Callable | None = None
    if listener is not None:
        emit_phase(listener.on_phase, CampaignPhase.BASELINE, "enter", round=0)

        def _baseline_on_start(query_text: str, qi: int, qt: int) -> None:
            listener.on_sample_started(0, 1, qi, qt, query_text)

        def _baseline_on_result(result: dict, qi: int, qt: int) -> None:
            listener.on_sample_scored(0, 1, qi, qt, result)

        on_start_cb = _baseline_on_start
        on_result_cb = _baseline_on_result

    try:
        baseline_results, scores, _cached, _ = await score_search_point(
            sp,
            dataset,
            ctx,
            label="Baseline",
            on_result=on_result_cb,
            on_start=on_start_cb,
        )
    finally:
        if listener is not None:
            emit_phase(listener.on_phase, CampaignPhase.BASELINE, "exit", round=0)

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


async def prepare_scoring_context(
    experiment_extract: dict | None,
    train_data: list[Sample] | None,
    campaign_config: CampaignConfig | None = None,
    *,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    svc: Any = None,
    listener: Any | None = None,
    obs: Any | None = None,
) -> tuple[OptSearchPoint, list[Sample], list, list]:
    """Load baseline prompt, set dataset, and produce a populated ``campaign_rounds[0]``.

    Baseline evaluates the loaded prompt on the **same t-test slice that L1
    uses** — ``sample_dataset(dataset, sp_budget_ttest)``, the deterministic
    top-``sp_budget_ttest`` prefix. Datasets are already shuffled at creation,
    so no second RNG is needed. Byte-identical to ``runner.py``'s L1 sampler,
    so baseline populates the per-query cache
    (``DatasetRunStore.load_reusable_results``, keyed by query text under
    matching ``node_configs``) in the exact shape L1 round 1 requests —
    100% hit bridge.

    Skipped only when the prompt is genuinely empty (param-only optimization)
    or when ``svc`` / ``campaign_config`` is missing.

    ``sp_budget_ttest`` stays on ``CampaignConfig.optimization`` and never
    enters ``pipeline_params`` → the ``JobSearchPoint`` hash remains
    target-layer-pure.
    """
    from promptpotter.application.datasets.builder import sample_dataset

    prompt_nodes = pipeline_schema.prompt_node_names() if pipeline_schema else []
    dataset_name = campaign_config.dataset_name if campaign_config else None
    baseline = load_baseline_prompt(
        experiment_extract or {},
        prompt_node_names=prompt_nodes,
        dataset_name=dataset_name,
    )
    dataset = train_data or []

    campaign_rounds: list = []
    baseline_results: list = []
    if campaign_config is not None and svc is not None and dataset and baseline.render().strip():
        from promptpotter.shared.scoring import split_scoring_block

        sp_budget = campaign_config.sp_budget_ttest or 15
        eval_dataset = sample_dataset(dataset, sp_budget)
        logger.info(
            "Baseline eval on t-test slice (%d/%d queries, top-N prefix) — shared with L1",
            len(eval_dataset),
            len(dataset),
        )
        spec = split_scoring_block(campaign_config.scoring)
        campaign_rounds, baseline_results = await _run_baseline_scoring(
            baseline,
            eval_dataset,
            svc,
            pipeline_params=pipeline_params,
            pipeline_schema=pipeline_schema,
            scoring_formula=spec.per_query,
            scoring_round_formula=spec.per_round,
            scorer_id=spec.scorer_id,
            listener=listener,
            obs=obs,
        )

    return baseline, dataset, campaign_rounds, baseline_results


@dataclass
class DatasetSummary:
    """Return from ``prepare_datasets()``."""

    train_data: list[Sample] | None
    index_terms: list[str]
    splits: dict[str, list[Sample]]
    n_unique_queries: int


def prepare_datasets(
    store: Stores,
    excel_path: str | Path | None = None,
    *,
    force: bool = False,
) -> DatasetSummary:
    """Load/create datasets and build session terms (pure orchestration — notebook prints the summary)."""
    from promptpotter.application.datasets.builder import (
        SHEET_COLUMN_MAP,
        load_excel_ground_truth,
        samples_from_dicts,
        split_train_test,
    )

    if excel_path:
        excel_path = Path(excel_path)
        existing = store.backends.load_dataset("train")
        needs_create = force or not (existing and existing.get("items"))

        if needs_create:
            all_rows = load_excel_ground_truth(excel_path, SHEET_COLUMN_MAP)
            train, test_sets = split_train_test(all_rows)
            store.backends.save_dataset("train", train, source_file=excel_path.name)
            for name, items in test_sets.items():
                store.backends.save_dataset(name, items, source_file=excel_path.name)

    splits: dict[str, list[Sample]] = {}
    for name in ("train", "test_processes", "test_material"):
        ds = store.backends.load_dataset(name)
        raw_items = ds["items"] if ds and ds.get("items") else []
        splits[name] = samples_from_dicts(raw_items)

    train_data = splits["train"] or None
    index_terms = build_all_index_terms(store)

    all_queries: set[str] = set()
    for samples in splits.values():
        for s in samples:
            q = s.query.strip()
            if q:
                all_queries.add(q)

    return DatasetSummary(
        train_data=train_data,
        index_terms=index_terms,
        splits=splits,
        n_unique_queries=len(all_queries),
    )


def build_all_index_terms(store: Stores) -> list[str]:
    """Unique ground_truth identifiers across all stored datasets (train + test).

    For /match to work correctly, the session must contain ALL identifiers:
    - Train: query->ground_truth mappings (used for optimization evaluation)
    - Test: ground_truth only (identifiers in candidate pool, no query mapping)
    """
    gt_set: set[str] = set()
    for name in ("train", "test_processes", "test_material"):
        ds = store.backends.load_dataset(name)
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
