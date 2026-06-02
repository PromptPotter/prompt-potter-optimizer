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
from promptpotter.domain.run_records import ForkSeed
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
    "prepare_datasets",
    "prepare_scoring_context",
    "resolve_origin_opt_search_point",
    "summarize_archive_runs",
]


def build_campaign_emitter(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    origin_accuracy: float,
    resumed_from_round: int | None = None,
    recorder: Any | None = None,
    seed_from_cycle_id: str | None = None,
) -> Any:
    """Live dashboard projection from session + config (shared by CLI + runner).

    ``seed_from_cycle_id`` (set when building a fork's dashboard) names the
    parent cycle to seed prior trajectory from; ``None`` seeds from the cycle's
    own dir.
    """
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
        seed_from_cycle_id=seed_from_cycle_id,
    )


class CampaignOrigin(NamedTuple):
    """Extracted origin state from campaign_rounds."""

    origin_ps: dict[str, Any] | None
    origin_acc: float
    origin_results: list[Any] | None
    instruction: str


def extract_campaign_origin(campaign_rounds: list[dict[str, Any]]) -> CampaignOrigin:
    """Origin prompt state, accuracy, results from campaign rounds.
    Walks reversed rounds for the last with scoring results; prompt_fields override to the tip."""
    if not campaign_rounds:
        return CampaignOrigin(
            origin_ps={},
            origin_acc=0.0,
            origin_results=None,
            instruction="",
        )

    tip = campaign_rounds[-1]

    # Prefer accuracy from last round with scoring results; fall back to tip (scan winner: acc but no results).
    origin_acc = tip.get("accuracy", 0.0)
    origin_results: list[Any] = []
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


def resolve_origin_opt_search_point(
    experiment_extract: dict[str, Any],
    prompt_node_names: list[str] | None = None,
    dataset_dir: Path | None = None,
    *,
    fork_seed: ForkSeed | None = None,
) -> OptSearchPoint:
    """Resolve the origin OptSearchPoint by priority: fork-seed → experiment
    prompts → {dataset_dir}/prompts → empty.

    A *fork_seed* with a non-empty ``starting_prompt`` wins outright — an
    operator-steered fork's origin *is* the edited searchpoint, so we build the
    OSP straight from those fields and short-circuit (no dataset/experiment
    lookup). *dataset_dir* is the resolved config dir
    (``Session.dataset_config_dir``, tenant-first), so an ingested dataset's
    authored prompts are found the same way a repo benchmark's are."""
    if fork_seed is not None and fork_seed.starting_prompt:
        return OptSearchPoint.from_prompt_fields(
            fork_seed.starting_prompt,
            lineage=IndividualLineage(
                changes_description="Operator-steered fork — edited searchpoint as origin",
                source="fork_seed",
            ),
        )

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

    if dataset_dir is not None and names:
        from promptpotter.application.datasets import (
            has_dataset_prompts,
            load_node_prompt,
        )

        if has_dataset_prompts(dataset_dir):
            for node_name in names:
                try:
                    template = load_node_prompt(dataset_dir, node_name, "default")
                except FileNotFoundError:
                    continue
                return OptSearchPoint.from_prompt_fields(
                    template.prompt_field_dict(),
                    lineage=IndividualLineage(
                        changes_description=(f"Origin from {dataset_dir}/prompts/ ({node_name})"),
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
    experiment_extract: dict[str, Any] | None,
    train_data: list[Sample] | None,
    campaign_config: CampaignConfig | None = None,
    *,
    pipeline_params: dict[str, Any] | None = None,
    pipeline_schema: PipelineSchema | None = None,
    svc: Any = None,
    listener: Any | None = None,
    obs: Any | None = None,
    fork_seed: ForkSeed | None = None,
) -> tuple[OptSearchPoint, list[Sample], list[dict[str, Any]], list[Any]]:
    """Resolve origin (fork-seed wins), set dataset, produce a populated ``campaign_rounds[0]``."""
    from promptpotter.application.datasets import sample_dataset

    prompt_nodes = pipeline_schema.prompt_node_names() if pipeline_schema else []
    origin = resolve_origin_opt_search_point(
        experiment_extract or {},
        prompt_node_names=prompt_nodes,
        dataset_dir=getattr(svc, "dataset_config_dir", None),
        fork_seed=fork_seed,
    )
    dataset = train_data or []

    campaign_rounds: list[dict[str, Any]] = []
    origin_results: list[Any] = []
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
    # populate_session_scoring overwrites scoring/source; loop repopulates before round 1.
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

    # ci=0/ct=1 ⇒ dashboard ticks per-sample during origin like L1.
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
    """Load/create datasets + build session terms (pure orchestration)."""
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

    # Single pass per split: samples + GT set (/match index) + unique query set. Test contributes GTs only.
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


def summarize_archive_runs(runs: list[dict[str, Any]]) -> DatasetRunSummary:
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
