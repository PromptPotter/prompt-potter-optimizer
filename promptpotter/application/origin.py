"""Campaign data loading and origin scoring."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bootstrap.scoring_context import populate_session_scoring
from promptpotter.application.bootstrap.session import Session
from promptpotter.application.config import CampaignConfig
from promptpotter.config.settings import DATASET_NAME
from promptpotter.domain.opt_search_point import IndividualLineage, OptSearchPoint
from promptpotter.domain.sample import Sample

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores


logger = logging.getLogger(__name__)

__all__ = [
    "CampaignOrigin",
    "DatasetRunSummary",
    "DatasetSummary",
    "build_campaign_emitter",
    "extract_campaign_origin",
    "load_origin_prompt",
    "prepare_datasets",
    "prepare_scoring_context",
    "summarize_archive_runs",
]


def build_campaign_emitter(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    origin_accuracy: float,
    resumed_from_round: int | None = None,
    recorder: Any | None = None,
) -> Any:
    """Build the live dashboard projection from session + config. Single factory shared by CLI and runner."""
    from promptpotter.infrastructure.projections import LiveDashboardView

    opt = campaign_config.optimization
    return LiveDashboardView.for_session(
        origin_accuracy,
        session.state.cycle_id,
        project_root=session.project_root,
        session_id=session.session_id,
        campaign_id=session.campaign_id,
        l1_patience=opt.l1_patience,
        n_variants=opt.n_variants,
        sp_budget_ttest=campaign_config.sp_budget_ttest,
        resumed_from_round=resumed_from_round,
        recorder=recorder,
    )


class CampaignOrigin(NamedTuple):
    """Extracted origin state from campaign_rounds."""

    origin_ps: dict | None
    origin_acc: float
    origin_results: list | None
    instruction: str


def extract_campaign_origin(campaign_rounds: list[dict]) -> CampaignOrigin:
    """Extract origin prompt state, accuracy, and results from campaign rounds.

    Searches reversed rounds for the last with actual scoring ``results``,
    then overrides the prompt_fields with the tip (most recent round).
    """
    if not campaign_rounds:
        return CampaignOrigin(
            origin_ps={},
            origin_acc=0.0,
            origin_results=None,
            instruction="",
        )

    tip = campaign_rounds[-1]

    # Prefer accuracy from the last round with scoring results; fall back to
    # the tip's accuracy (e.g. scan winner carries accuracy but no results).
    origin_acc = tip.get("accuracy", 0.0)
    origin_results: list = []
    for rd in reversed(campaign_rounds):
        if rd.get("results"):
            origin_acc = rd.get("accuracy", origin_acc)
            origin_results = rd["results"]
            break

    tip_ps = tip["prompt_fields"]

    return CampaignOrigin(
        origin_ps=tip_ps,
        origin_acc=origin_acc,
        origin_results=origin_results,
        instruction=tip_ps.instruction,
    )


def load_origin_prompt(
    experiment_extract: dict,
    prompt_node_names: list[str] | None = None,
    dataset_name: str | None = None,
) -> OptSearchPoint:
    """Resolve origin OptSearchPoint: experiment prompts → datasets/{name}/prompts → empty."""
    dependencies = experiment_extract.get("dependencies", {})
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

    if matched_prompt is None and not names and prompts:
        matched_key, matched_prompt = next(iter(prompts.items()))

    if matched_prompt is not None:
        label = names[0] if names else matched_key
        return OptSearchPoint(
            instruction=matched_prompt["template"],
            lineage=IndividualLineage(
                changes_description=f"Origin prompt from {label} registry",
                source="origin",
            ),
        )

    if dataset_name and names:
        from promptpotter.application.datasets import (
            has_dataset_prompts,
            load_node_prompt,
        )

        if has_dataset_prompts(dataset_name):
            for node_name in names:
                try:
                    template = load_node_prompt(dataset_name, node_name, "default")
                except FileNotFoundError:
                    continue
                return OptSearchPoint.from_prompt_fields(
                    template.prompt_field_dict(),
                    lineage=IndividualLineage(
                        changes_description=(
                            f"Origin from datasets/{dataset_name}/prompts/ ({node_name})"
                        ),
                        source="origin",
                    ),
                )

    return OptSearchPoint(
        instruction="",
        lineage=IndividualLineage(
            changes_description="Origin (no prompt node active — param-only optimization)",
            source="origin",
        ),
    )


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
    """Load origin prompt, set dataset, and produce a populated ``campaign_rounds[0]``."""
    from promptpotter.application.datasets import sample_dataset

    prompt_nodes = pipeline_schema.prompt_node_names() if pipeline_schema else []
    dataset_name = campaign_config.dataset_name if campaign_config else None
    origin = load_origin_prompt(
        experiment_extract or {},
        prompt_node_names=prompt_nodes,
        dataset_name=dataset_name,
    )
    dataset = train_data or []

    campaign_rounds: list = []
    origin_results: list = []
    if not (
        campaign_config is not None and svc is not None and dataset and origin.render().strip()
    ):
        return origin, dataset, campaign_rounds, origin_results

    from promptpotter.application.scoring.formula import split_scoring_block
    from promptpotter.application.scoring.search_point_scorer import score_search_point
    from promptpotter.domain.phases import CampaignPhase, emit_phase
    from promptpotter.shared.errors import graceful

    session: Session = svc
    sp_budget = campaign_config.sp_budget_ttest or 15
    scoring_set = sample_dataset(dataset, sp_budget)
    spec = split_scoring_block(campaign_config.scoring)

    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)
    else:
        logger.warning(
            "No session terms available — /matches calls will fail. "
            "Load datasets first (Excel ground truth → DatasetStore)."
        )

    if obs:
        with graceful("Dataset registration in origin scoring failed"):
            obs.register_dataset(DATASET_NAME, scoring_set)

    sp = origin.to_job_search_point(
        base_pipeline_params=pipeline_params,
        schema=pipeline_schema,
    )
    # populate_session_scoring overwrites session.scoring/source; loop repopulates before round 1.
    prior_schema = session.pipeline_schema
    if pipeline_schema is not None:
        session.pipeline_schema = pipeline_schema
    populate_session_scoring(
        session,
        obs=obs,
        scoring_formula=spec.per_sample,
        scoring_round_formula=spec.per_round,
        scorer_id=spec.scorer_id,
        source="origin",
    )

    # ci=0/ct=1 makes the dashboard emitter tick per-sample during origin like L1.
    if listener is not None:
        emit_phase(listener.on_phase, CampaignPhase.ORIGIN, "enter", round=0)

    try:
        origin_results, scores, _cached, _ = await score_search_point(
            sp,
            scoring_set,
            session,
            label="Origin",
            on_sample_starting=(
                partial(listener.on_sample_started, 0, 1) if listener is not None else None
            ),
            on_sample_scored=(
                partial(listener.on_sample_scored, 0, 1) if listener is not None else None
            ),
        )
    finally:
        if listener is not None:
            emit_phase(listener.on_phase, CampaignPhase.ORIGIN, "exit", round=0)
        session.pipeline_schema = prior_schema

    campaign_rounds = [
        {
            "round": 0,
            "label": "origin",
            "prompt_fields": origin,
            "accuracy": scores["accuracy"],
            "hits": scores["hits"],
            "total": scores["total"],
            "results": origin_results,
        }
    ]

    return origin, dataset, campaign_rounds, origin_results


class DatasetSummary(NamedTuple):
    """Return from ``prepare_datasets()``."""

    train_data: list[Sample] | None
    index_terms: list[str]
    splits: dict[str, list[Sample]]
    n_unique_samples: int


def prepare_datasets(
    store: Stores,
    excel_path: str | Path | None = None,
    *,
    force: bool = False,
) -> DatasetSummary:
    """Load/create datasets and build session terms (pure orchestration — notebook prints the summary)."""
    from promptpotter.application.datasets import (
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

    # Single pass — build samples, gt set (for /match index), and unique
    # query set in one disk-load per split. Test sets contribute GTs only;
    # train carries query→gt mappings used by the optimization loop.
    splits: dict[str, list[Sample]] = {}
    gt_set: set[str] = set()
    all_queries: set[str] = set()
    for name in ("train", "test_processes", "test_material"):
        ds = store.backends.load_dataset(name)
        raw_items = ds["items"] if ds and ds.get("items") else []
        splits[name] = samples_from_dicts(raw_items)
        for item in raw_items:
            gt = item.get("ground_truth", "").strip()
            if gt:
                gt_set.add(gt)
        for s in splits[name]:
            q = s.query.strip()
            if q:
                all_queries.add(q)

    return DatasetSummary(
        train_data=splits["train"] or None,
        index_terms=sorted(gt_set),
        splits=splits,
        n_unique_samples=len(all_queries),
    )


class DatasetRunSummary(NamedTuple):
    """Aggregated dataset run statistics for dashboard display."""

    total: int
    by_source: dict[str, int]
    best_accuracy: float
    best_name: str


def summarize_archive_runs(runs: list[dict]) -> DatasetRunSummary:
    """Aggregate measurement-archive runs by source prefix and find best accuracy."""
    by_source: dict[str, int] = {}
    best_acc = 0.0
    best_name = ""
    for r in runs:
        name = r.get("name", "")
        source = name.split("_")[0] if "_" in name else "other"
        by_source[source] = by_source.get(source, 0) + 1
        acc = r.get("scores", {}).get("accuracy", 0.0)
        if acc > best_acc:
            best_acc = acc
            best_name = name
    return DatasetRunSummary(
        total=len(runs),
        by_source=by_source,
        best_accuracy=best_acc,
        best_name=best_name,
    )
