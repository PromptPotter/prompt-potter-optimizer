"""Campaign initialization — Session bundle, store/client/dataset wiring, loop setup."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import RoundScorer, Scorer
from promptpotter.domain.tenant import TenantContext
from promptpotter.infrastructure.backend.client import BackendClient
from promptpotter.infrastructure.store import Stores, build_stores

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.campaign.data import CampaignBaseline
    from promptpotter.application.campaign.runner import RunListener
    from promptpotter.application.intelligence.sample_index import SampleIndex
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.elimination import DegradationCheck
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import TaskDecomposition
    from promptpotter.infrastructure.persistence.round_recorder import RoundRecorder
    from promptpotter.infrastructure.tracing import ObservabilityBridge


logger = logging.getLogger(__name__)

__all__ = [
    "Session",
    "auto_mint_session",
    "init_optimization_loop",
    "init_services",
    "new_session_state",
    "populate_session_scoring",
]


@dataclass
class Session:
    """Session-scoped identity + wire-up + loop-cycle infra + scoring."""

    store: Stores
    backend_id: str
    experiment_id: str
    backend_client: BackendClient
    pipeline_schema: PipelineSchema | None
    synced: bool
    queries: list[Sample] = field(default_factory=list)
    experiment_extract: dict = field(default_factory=dict)
    index_terms: list[str] = field(default_factory=list)
    tenant: TenantContext | None = None
    dataset_name: str | None = None
    session_id: str = ""
    cycle_id: str = ""
    project_root: str = ""
    pipeline_params: dict = field(default_factory=dict)

    obs_campaign_id: str = ""
    scoring_dataset: list[Sample] = field(default_factory=list)
    degradation_checks: list[DegradationCheck] = field(default_factory=list)
    resumed_from_round: int = 0

    obs: ObservabilityBridge | None = None
    round_recorder: RoundRecorder | None = None
    # Populated after bootstrap — Session is built before the active config's scoring block compiles.
    scorer: Scorer | None = None
    scorer_id: str = "none"
    scorer_formula: str | None = None
    round_scorer: RoundScorer | None = None
    max_consecutive_errors: int = 3
    stale_data_load_protocol: list[str] | None = None
    sample_index: SampleIndex | None = None
    source: str = ""
    stop_check: Callable[[], bool] | None = None


def populate_session_scoring(
    session: Session,
    *,
    obs: ObservabilityBridge | None,
    scoring_formula: str | None,
    scoring_round_formula: str | None = None,
    scorer_id: str | None = None,
    experiment_id: str = "",
    cycle_id: str | None = None,
    max_consecutive_errors: int = 3,
    stale_data_load_protocol: list[str] | None = None,
    source: str = "optimization_loop",
) -> None:
    """Attach the scoring block onto ``session`` (mutates in place)."""
    from promptpotter.domain.scoring import (
        auto_scorer_id,
        compile_round_scorer,
        compile_scorer,
    )

    session.experiment_id = experiment_id or (
        cycle_id.replace("cycle_", "")[:12] if cycle_id else ""
    )
    session.obs = obs
    session.source = source
    session.max_consecutive_errors = max_consecutive_errors
    session.stale_data_load_protocol = stale_data_load_protocol
    session.scorer = compile_scorer(scoring_formula)
    session.scorer_id = scorer_id or auto_scorer_id(scoring_formula)
    session.scorer_formula = scoring_formula
    session.round_scorer = (
        compile_round_scorer(scoring_round_formula) if scoring_round_formula else None
    )


def new_session_state(
    *,
    init_params: dict,
    campaign_config: dict,
    pipeline_params: dict,
    active_steps: list[str],
) -> dict[str, Any]:
    """Build a fresh campaign session-state dict — shared by CLI init and the orchestrator."""
    return {
        "phase": "init",
        "init_params": init_params,
        "campaign_config": campaign_config,
        "pipeline_params": pipeline_params,
        "active_steps": active_steps,
        "baseline_prompt_fields": {},
        "dataset_count": 0,
        "baseline_accuracy": 0.0,
        "task_context": None,
        "experiment_id": None,
    }


def auto_mint_session(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    cycle_hash: str,
    baseline_acc: float = 0.0,
    baseline_prompt_fields: dict | None = None,
    dataset_size: int = 0,
    experiment_id: str | None = None,
) -> tuple[str, str]:
    """Mint (session_id, cycle_id) + write session + claim active pointer when called outside CLI init."""
    from promptpotter.infrastructure.store import mint_session_id, save_active_pointer
    from promptpotter.infrastructure.store.base import validate_path_component

    validate_path_component(cycle_hash)
    session_id = mint_session_id()
    cycle_id = f"cycle_{cycle_hash}"

    state = new_session_state(
        init_params={
            "backend_url": session.backend_client.base_url,
            "backend_id": session.backend_id,
            "experiment_id": experiment_id,
            "dataset_name": session.dataset_name,
        },
        campaign_config=campaign_config.model_dump(),
        pipeline_params={},
        active_steps=[],
    )
    state["baseline_accuracy"] = baseline_acc
    state["dataset_count"] = dataset_size
    state["baseline_prompt_fields"] = baseline_prompt_fields or {}

    sessions = session.store.sessions
    sessions.create(session_id, state)
    sessions.ensure_narrative_files(session_id)

    save_active_pointer(session.store.tenant_id, session_id, cycle_id)
    logger.info("Auto-minted session %s + cycle %s", session_id, cycle_id)
    return session_id, cycle_id


async def init_services(
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = "",
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    project_root: Path | None = None,
    dataset_name: str | None = None,
    on_status: Callable[[str], None] | None = None,
    take_over: bool = False,
    tenant_id: str = "default",
) -> Session:
    """Init store, client, pipeline schema, eval data. Refuses tenant drift unless take_over=True."""
    from promptpotter.application.datasets.builder import DATASET_LOADERS, samples_from_dicts
    from promptpotter.application.pipeline_discovery import parse_pipeline_response
    from promptpotter.infrastructure.store import clear_active_pointer, read_active_pointer
    from promptpotter.shared.errors import ActiveSessionMismatchError

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    if not backend_id:
        backend_id = dataset_name or DEFAULT_BACKEND_ID
    if project_root is None:
        # campaign/campaign_setup.py → services → promptpotter → repo_root
        project_root = Path(__file__).resolve().parent.parent.parent.parent

    store = build_stores(project_root / ".promptpotter" / "projects", tenant_id=tenant_id)

    # Tenant-drift guardrail. The smoke tool / notebook is sessionless by design
    # (M9 gap), so when taking over we clear the pointer entirely rather than
    # writing a partial one that downstream load_session() would mis-resolve.
    active_tid, active_sid, _ = read_active_pointer()
    if active_tid and active_tid != tenant_id:
        if not take_over:
            raise ActiveSessionMismatchError(
                active_tenant_id=active_tid,
                active_session_id=active_sid,
                requested_tenant_id=tenant_id,
            )
        clear_active_pointer()
        status(f"Took over active session: cleared pointer (was tenant {active_tid!r})")

    pipeline_schema: PipelineSchema | None = None
    if dataset_name:
        cfg_path = project_root / "datasets" / dataset_name / "pipeline.json"
        if cfg_path.exists():
            try:
                pipeline_schema = parse_pipeline_response(
                    json.loads(cfg_path.read_text(encoding="utf-8"))
                )
                status(f"Pipeline: {pipeline_schema.name} ({len(pipeline_schema.nodes)} nodes)")
            except Exception as exc:
                logger.warning("Failed to parse static pipeline.json: %s", exc)
                pipeline_schema = None

    client = BackendClient(backend_url)
    status(f"Backend: {backend_url}")

    if not pipeline_schema:
        try:
            pipeline_schema = parse_pipeline_response(await client.fetch_pipeline())
            status(f"Pipeline: {pipeline_schema.name} ({len(pipeline_schema.nodes)} nodes)")
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.info("Could not fetch pipeline schema: %s", exc)
            status("Pipeline: unavailable")
            pipeline_schema = None

    if not store.backends.get(backend_id):
        store.backends.register(
            BackendConnection(
                id=backend_id,
                name=pipeline_schema.name if pipeline_schema else "Unknown",
                backend_type="backend" if pipeline_schema else "unknown",
                base_url=backend_url,
            )
        )

    base = Session(
        store=store,
        backend_id=backend_id,
        experiment_id=experiment_id,
        backend_client=client,
        pipeline_schema=pipeline_schema,
        synced=False,
        dataset_name=dataset_name,
        tenant=TenantContext(tenant_id=tenant_id),
        project_root=str(store.base_dir),
    )

    if dataset_name:
        ds = base.store.backends.load_dataset(dataset_name)
        if not (ds and ds.get("items")) and dataset_name in DATASET_LOADERS:
            status(f"Loading dataset '{dataset_name}' from registry ...")
            loader_items = DATASET_LOADERS[dataset_name]()
            base.store.backends.save_dataset(dataset_name, loader_items)
            ds = {"items": loader_items}

        if not (ds and ds.get("items")):
            status(f"Dataset '{dataset_name}' not available")
            raise ValueError(
                f"Dataset {dataset_name!r} not found in DatasetStore or DATASET_LOADERS. "
                f"Add a loader to DATASET_LOADERS in dataset_builder.py."
            )

        items = ds["items"]
        valid = [item for item in items if item.get("query") and item.get("ground_truth")]
        base.queries = samples_from_dicts(valid)
        base.index_terms = sorted({r["ground_truth"] for r in items if r.get("ground_truth")})
        status(f"Dataset: {dataset_name} ({len(items)} queries)")
        return base

    extract = base.store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")
    has_traces = bool(extract and extract.get("runs") and extract["runs"][0].get("traces"))

    if not extract or not has_traces:
        reason = "No stored experiment data" if not extract else "Stored data has no traces"
        logger.info("%s — syncing from %s ...", reason, backend_url)
        status(f"Syncing experiment {experiment_id} ...")
        try:
            extract = await client.sync_experiment(
                base.store, backend_id, experiment_id, include_traces=True
            )
            base.synced = True
            status("Sync complete")
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("Auto-sync failed: %s", exc)
            status(f"Sync failed: {exc}")

    if not extract:
        logger.warning(
            "No experiment data available. "
            "Downstream calls will fail until data is synced or datasets are loaded."
        )
        status("WARNING: No experiment data available")
        return base

    from promptpotter.config.extractors import EXPERIMENT_EXTRACTORS

    schema_key = base.pipeline_schema.name.lower() if base.pipeline_schema else ""
    extractor = EXPERIMENT_EXTRACTORS.get(schema_key)
    if extractor:
        queries, index_terms = extractor(extract)
    else:
        runs = extract.get("runs", [])
        queries = []
        gt_set: set[str] = set()
        for er in runs[0].get("evaluation_results", []) if runs else []:
            q, gt = er.get("query", ""), er.get("ground_truth", "")
            if q and gt:
                queries.append({"query": q, "ground_truth": gt})
                gt_set.add(gt)
        index_terms = sorted(gt_set)

    exp_name = extract.get("experiment", {}).get("name", experiment_id)
    status(f"Experiment: {exp_name} ({len(queries)} queries, {len(index_terms)} session terms)")

    base.queries = samples_from_dicts(queries)
    base.experiment_extract = extract
    base.index_terms = index_terms
    return base


async def init_optimization_loop(
    baseline: CampaignBaseline,
    dataset: list[Sample],
    config: CampaignConfig,
    *,
    cb: RunListener,
    task_context: TaskDecomposition,
    scoring_formula: str | None,
    scoring_round_formula: str | None,
    scorer_id: str,
    no_divergence_check: bool,
    fork_on_divergence: bool,
    langfuse_session_id: str | None,
    cycle_id: str | None,
    resume_from_round_override: int | None,
    experiment_id: str,
    session: Session,
    started_at: str,
) -> Cycle:
    """Build Cycle + attach loop-cycle infra onto ``session``: baseline, cycle resume, obs, scoring, search memory."""
    from promptpotter.application.campaign.config import run_preflight_checks
    from promptpotter.application.campaign.cycle_store import bootstrap_cycle
    from promptpotter.application.datasets.builder import sample_dataset
    from promptpotter.application.intelligence.search_memory import SearchMemory
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.decisions import resume_with_divergence_check
    from promptpotter.application.optimization.elimination import build_degradation_checks
    from promptpotter.domain.phases import CampaignPhase, emit_phase
    from promptpotter.domain.scoring import compile_round_scorer
    from promptpotter.infrastructure.tracing import ObservabilityBridge

    opt = config.optimization
    preflight_warnings = run_preflight_checks(config, dataset)
    for w in preflight_warnings:
        logger.warning("preflight[%s]: %s — %s", w.code, w.title, w.detail)
    emit_phase(
        cb.on_phase,
        CampaignPhase.INIT,
        "enter",
        config=config,
        dataset=dataset,
        env=session,
        warnings=preflight_warnings,
    )

    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)
    if baseline.baseline_ps is None:
        raise ValueError("baseline.baseline_ps is required; run baseline evaluation first.")

    baseline_osp = OptSearchPoint.from_prompt_fields(baseline.baseline_ps)
    baseline_round_scorer = (
        compile_round_scorer(scoring_round_formula) if scoring_round_formula else None
    )
    cycle = Cycle.start(
        baseline_osp,
        baseline.baseline_acc,
        task_context=task_context,
        schema=session.pipeline_schema,
        baseline_results=baseline.baseline_results,
        round_scorer=baseline_round_scorer,
        session=session,
        config=config,
    )

    base_pp = session.pipeline_schema.to_pipeline_params() if session.pipeline_schema else {}
    baseline_jsp = baseline_osp.to_job_search_point(
        base_pipeline_params=base_pp, schema=session.pipeline_schema
    )
    resolved_cycle_id, resumed_from_round = bootstrap_cycle(
        config,
        session,
        baseline_jsp,
        baseline.baseline_acc,
        dataset,
        cycle_id,
        parent_session_id=session.session_id,
        resume_from_round_override=resume_from_round_override,
    )

    obs_campaign_id = resolved_cycle_id or f"campaign_{started_at[:19].replace(':', '')}"
    obs = ObservabilityBridge.start_campaign(
        session.project_root,
        session.backend_id,
        config_snapshot=config.model_dump(mode="json"),
        baseline_accuracy=baseline.baseline_acc,
        dataset=dataset,
        obs_campaign_id=obs_campaign_id,
        langfuse_session_id=langfuse_session_id or resolved_cycle_id,
    )

    populate_session_scoring(
        session,
        obs=obs,
        experiment_id=experiment_id,
        cycle_id=resolved_cycle_id,
        max_consecutive_errors=opt.max_consecutive_errors,
        stale_data_load_protocol=opt.stale_data_load_protocol,
        scoring_formula=scoring_formula,
        scoring_round_formula=scoring_round_formula,
        scorer_id=scorer_id,
    )

    if resumed_from_round > 0 and resolved_cycle_id:
        fork_result = resume_with_divergence_check(
            session.store.campaigns,
            session.backend_id,
            resolved_cycle_id,
            resumed_from_round,
            session,
            cycle,
            skip_divergence_check=no_divergence_check,
            fork_on_divergence=fork_on_divergence,
        )
        if fork_result is not None:
            resolved_cycle_id = fork_result.new_cycle_id
            resumed_from_round = fork_result.new_resumed_from_round
    if session.store:
        session.store.archive.register_prompt_alias(
            session.backend_id, baseline.instruction, baseline_osp.render()
        )

    search_memory = SearchMemory.ensure_for(
        session.store,
        session.backend_id,
        scorer=session.scorer,
        scorer_id=session.scorer_id,
        scorer_formula=session.scorer_formula,
    )
    cycle.search_memory = search_memory

    if resolved_cycle_id:
        session.cycle_id = resolved_cycle_id
    session.obs_campaign_id = obs_campaign_id
    session.scoring_dataset = sample_dataset(dataset, config.sp_budget_ttest)
    session.degradation_checks = build_degradation_checks(config)
    session.resumed_from_round = resumed_from_round

    emit_phase(
        cb.on_phase,
        CampaignPhase.INIT,
        "exit",
        state=cycle,
        env=session,
        config=config,
        dataset=dataset,
    )
    return cycle
