"""Campaign initialization + lifecycle bookends — store, client, dataset, pipeline schema, loop init/finalize."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.application.optimization.phases import StopReason
from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.sample import Sample
from promptpotter.domain.tenant import TenantContext
from promptpotter.infrastructure.backend.client import BackendClient
from promptpotter.infrastructure.store import Stores, build_stores
from promptpotter.shared.scoring import RoundScorer, Scorer

if TYPE_CHECKING:
    from promptpotter.application.campaign.callbacks import RunListener
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.campaign.data import CampaignBaseline
    from promptpotter.application.intelligence.sample_index import SampleIndex
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.nodes.escalation import DegradationCheck
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import TaskDecomposition
    from promptpotter.infrastructure.persistence.session_emitter import (
        CampaignPersistenceEmitter,
    )
    from promptpotter.infrastructure.store.campaign_store import CampaignStore
    from promptpotter.infrastructure.tracing import ObservabilityBridge


logger = logging.getLogger(__name__)

__all__ = [
    "Session",
    "finalize_optimization_run",
    "init_optimization_loop",
    "init_services",
    "load_baseline_prompt",
    "resolve_campaign_id",
]


@dataclass
class Session:
    """Session-scoped identity + wire-up + loop-cycle infra + scoring.

    Absorbs the old ``SessionEnv`` + ``LoopEnv`` + ``ScoringEnv`` triad into a
    single object threaded through the optimization loop. ``project_root`` is
    the tenant base dir.
    """

    # ---- identity (from SessionEnv) ----
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

    # ---- loop-cycle infra (from LoopEnv) ----
    campaign_store: CampaignStore | None = None
    obs_campaign_id: str = ""
    scoring_dataset: list[Sample] = field(default_factory=list)
    degradation_checks: list[DegradationCheck] = field(default_factory=list)
    resumed_from_round: int = 0

    # ---- scoring (from ScoringEnv) ----
    obs: ObservabilityBridge | None = None
    # Compiled per-dataset scorer. Required by the scoring path but populated
    # after bootstrap (Session is built before the active campaign config's
    # scoring block is compiled).
    scorer: Scorer | None = None
    scorer_id: str = "none"
    scorer_formula: str | None = None
    round_scorer: RoundScorer | None = None
    max_consecutive_errors: int = 3
    stale_data_load_protocol: list[str] | None = None
    sample_index: SampleIndex | None = None
    source: str = ""
    stop_check: Callable[[], bool] | None = None

    @classmethod
    def for_loop(
        cls,
        base: Session,
        *,
        obs: ObservabilityBridge | None,
        experiment_id: str,
        cycle_id: str | None,
        max_consecutive_errors: int,
        stale_data_load_protocol: list[str] | None,
        scoring_formula: str | None,
        scoring_round_formula: str | None = None,
        scorer_id: str | None = None,
        source: str = "optimization_loop",
    ) -> Session:
        """Populate a ``Session`` in place with the scoring block for an optimization-loop run.

        Owns the derived ``experiment_id`` fallback (short cycle-id prefix
        when not explicitly set) and the scoring-formula compilation so the
        caller just hands over raw config. Mutates *base* (attaches scorer +
        derived fields) and returns it for chaining.
        """
        from promptpotter.shared.scoring import (
            auto_scorer_id,
            compile_round_scorer,
            compile_scorer,
        )

        derived_experiment_id = experiment_id or (
            cycle_id.replace("cycle_", "")[:12] if cycle_id else ""
        )
        round_scorer = (
            compile_round_scorer(scoring_round_formula) if scoring_round_formula else None
        )
        base.experiment_id = derived_experiment_id
        base.obs = obs
        base.source = source
        base.max_consecutive_errors = max_consecutive_errors
        base.stale_data_load_protocol = stale_data_load_protocol
        base.scorer = compile_scorer(scoring_formula)
        base.scorer_id = scorer_id or auto_scorer_id(scoring_formula)
        base.scorer_formula = scoring_formula
        base.round_scorer = round_scorer
        return base

    @classmethod
    def for_baseline(
        cls,
        base: Session,
        *,
        obs: ObservabilityBridge | None,
        scoring_formula: str | None,
        scoring_round_formula: str | None = None,
        scorer_id: str | None = None,
    ) -> Session:
        """Populate *base* with baseline scoring block (phase 0 of ``optimize``).

        Thin delegation to ``for_loop`` — keeps one source of truth for the
        scorer-trio compilation. Baseline runs before ``cycle_id`` is bound
        and on a small scoring set, so it doesn't carry the loop's
        cycle-derived ``experiment_id`` or stale-data protocol.
        """
        return cls.for_loop(
            base,
            obs=obs,
            experiment_id="",
            cycle_id=None,
            max_consecutive_errors=3,
            stale_data_load_protocol=None,
            scoring_formula=scoring_formula,
            scoring_round_formula=scoring_round_formula,
            scorer_id=scorer_id,
            source="baseline",
        )


def load_baseline_prompt(
    experiment_extract: dict,
    prompt_node_names: list[str] | None = None,
    dataset_name: str | None = None,
) -> OptSearchPoint:
    """Resolve baseline OptSearchPoint: experiment prompts → datasets/{name}/prompts → empty."""
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
            changes_description=f"Baseline prompt from {label} registry",
        )

    if dataset_name and names:
        from promptpotter.application.datasets.prompt_store import (
            has_dataset_prompts,
            load_node_prompt,
        )

        if has_dataset_prompts(dataset_name):
            for node_name in names:
                try:
                    template = load_node_prompt(dataset_name, node_name, "default")
                except FileNotFoundError:
                    continue
                logger.info(
                    "Baseline loaded from canonical store: datasets/%s/prompts/ → %s",
                    dataset_name,
                    node_name,
                )
                return OptSearchPoint.from_prompt_fields(
                    template.prompt_field_dict(),
                    changes_description=(
                        f"Baseline from datasets/{dataset_name}/prompts/ ({node_name})"
                    ),
                )

    logger.info(
        "No prompt found for nodes %s — baseline uses empty prompt (param-only optimization)",
        names,
    )
    return OptSearchPoint(
        instruction="",
        changes_description="Baseline (no prompt node active — param-only optimization)",
    )


def _load_static_pipeline_schema(dataset_name: str, project_root: Path) -> PipelineSchema | None:
    """Load schema from datasets/{name}/pipeline.json, or None if missing/unparseable."""
    import json

    from promptpotter.application.pipeline_discovery import parse_pipeline_response

    cfg_path = project_root / "datasets" / dataset_name / "pipeline.json"
    if not cfg_path.exists():
        return None
    try:
        schema = parse_pipeline_response(json.loads(cfg_path.read_text(encoding="utf-8")))
        logger.info("Static pipeline schema loaded: %s v%s", schema.name, schema.version)
        return schema
    except Exception as exc:
        logger.warning("Failed to parse static pipeline.json: %s", exc)
        return None


async def _fetch_pipeline_schema(
    client: BackendClient, status: Callable[[str], None]
) -> PipelineSchema | None:
    """Fetch schema via backend GET /pipeline, or None on failure."""
    from promptpotter.application.pipeline_discovery import parse_pipeline_response

    try:
        schema = parse_pipeline_response(await client.fetch_pipeline())
        logger.info("Pipeline schema loaded: %s v%s", schema.name, schema.version)
        status(f"Pipeline: {schema.name} ({len(schema.nodes)} nodes)")
        return schema
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.info("Could not fetch pipeline schema: %s", exc)
        status("Pipeline: unavailable")
        return None


def _check_active_pointer(tenant_id: str, take_over: bool, status: Callable[[str], None]) -> None:
    """Guardrail against tenant drift; take_over=True clears the pointer."""
    from promptpotter.infrastructure.store import clear_active_pointer, read_active_pointer
    from promptpotter.shared.errors import ActiveSessionMismatchError

    active_tid, active_sid, _ = read_active_pointer()
    if not active_tid or active_tid == tenant_id:
        return
    if not take_over:
        raise ActiveSessionMismatchError(
            active_tenant_id=active_tid,
            active_session_id=active_sid,
            requested_tenant_id=tenant_id,
        )
    # Take-over: clear the pointer. The smoke tool / notebook is sessionless
    # by design (M9 gap), so writing a partial pointer would be a lie that
    # downstream load_session() turns into an ugly "not found" error.
    clear_active_pointer()
    status(f"Took over active session: cleared pointer (was tenant {active_tid!r})")


def _hydrate_dataset(base: Session, dataset_name: str, status: Callable[[str], None]) -> None:
    """Load items from DatasetStore; auto-populate from DATASET_LOADERS registry if empty."""
    from promptpotter.application.datasets.builder import samples_from_dicts

    ds = base.store.backends.load_dataset(dataset_name)
    if not (ds and ds.get("items")):
        from promptpotter.application.datasets.builder import DATASET_LOADERS

        if dataset_name in DATASET_LOADERS:
            status(f"Loading dataset '{dataset_name}' from registry ...")
            loader_items = DATASET_LOADERS[dataset_name]()
            base.store.backends.save_dataset(dataset_name, loader_items)
            ds = {"items": loader_items}
            logger.info("Auto-loaded dataset %r: %d items", dataset_name, len(loader_items))

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
    logger.info(
        "Loaded dataset %r from store: %d items, %d session terms",
        dataset_name,
        len(items),
        len(base.index_terms),
    )
    status(f"Dataset: {dataset_name} ({len(items)} queries)")


async def _hydrate_experiment(
    base: Session,
    backend_id: str,
    experiment_id: str,
    client: BackendClient,
    backend_url: str,
    status: Callable[[str], None],
) -> None:
    """Load experiment extract from store; auto-sync from backend on stale or missing data."""
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
        return

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
    from promptpotter.application.datasets.builder import samples_from_dicts

    base.queries = samples_from_dicts(queries)
    base.experiment_extract = extract
    base.index_terms = index_terms


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

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    if not backend_id:
        backend_id = dataset_name or DEFAULT_BACKEND_ID
    if project_root is None:
        # campaign/campaign_setup.py → services → promptpotter → repo_root
        project_root = Path(__file__).resolve().parent.parent.parent.parent

    store = build_stores(project_root / ".promptpotter" / "projects", tenant_id=tenant_id)
    _check_active_pointer(tenant_id, take_over, status)

    pipeline_schema: PipelineSchema | None = None
    if dataset_name:
        pipeline_schema = _load_static_pipeline_schema(dataset_name, project_root)
        if pipeline_schema:
            status(f"Pipeline: {pipeline_schema.name} ({len(pipeline_schema.nodes)} nodes)")

    client = BackendClient(backend_url)
    status(f"Backend: {backend_url}")

    if not pipeline_schema:
        pipeline_schema = await _fetch_pipeline_schema(client, status)

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
        _hydrate_dataset(base, dataset_name, status)
    else:
        await _hydrate_experiment(base, backend_id, experiment_id, client, backend_url, status)
    return base


def resolve_campaign_id(
    store: Stores,
    backend_id: str,
    short_id: str,
) -> str | None:
    """Resolve short prefix/suffix to full campaign_id."""
    campaigns = store.campaigns.list_all(backend_id)
    matches = [c for c in campaigns if short_id in c["campaign_id"]]
    if len(matches) == 1:
        return matches[0]["campaign_id"]
    if len(matches) > 1:
        logger.warning(
            "Ambiguous ID '%s' — %d matches: %s",
            short_id,
            len(matches),
            [m["campaign_id"] for m in matches],
        )
        return None
    logger.warning("No campaign matching '%s'", short_id)
    return None


def _campaign_status_for(stop_reason: StopReason) -> str:
    return {
        StopReason.PAUSED_FOR_REVIEW: "paused",
        StopReason.USER_PAUSED: "paused",
        StopReason.USER_STOPPED: "stopped",
        StopReason.INTERRUPTED: "interrupted",
    }.get(stop_reason, "completed")


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
    langfuse_session_id: str | None,
    cycle_id: str | None,
    resume_from_round_override: int | None,
    experiment_id: str,
    session: Session,
    started_at: str,
) -> Cycle:
    """Build Cycle + attach loop-cycle infra onto ``session``: baseline, cycle resume, obs, scoring, search memory."""
    from promptpotter.application.campaign.bootstrap import bootstrap_cycle
    from promptpotter.application.campaign.config import run_preflight_checks
    from promptpotter.application.campaign.decisions import resume_with_divergence_check
    from promptpotter.application.datasets.builder import sample_dataset
    from promptpotter.application.intelligence.search_memory import SearchMemory
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.nodes.escalation import build_degradation_checks
    from promptpotter.application.optimization.nodes.l1.critique import sample_thinking_styles
    from promptpotter.application.optimization.phases import CampaignPhase, emit_phase
    from promptpotter.infrastructure.tracing import ObservabilityBridge
    from promptpotter.shared.scoring import compile_round_scorer

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

    active_steps = list(session.pipeline_schema.active_steps) if session.pipeline_schema else []
    campaign_store, resolved_cycle_id, resumed_from_round = bootstrap_cycle(
        config,
        session,
        baseline_osp.render(),
        baseline.baseline_acc,
        dataset,
        active_steps,
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

    Session.for_loop(
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

    if resumed_from_round > 0 and campaign_store and resolved_cycle_id:
        resume_with_divergence_check(
            campaign_store,
            session.backend_id,
            resolved_cycle_id,
            resumed_from_round,
            session,
            cycle,
            skip_divergence_check=no_divergence_check,
        )
    else:
        cycle.opt_sp.memory.thinking_styles = sample_thinking_styles(n=3, seed=opt.seed)
    if session.store:
        session.store.dataset_runs.register_prompt_alias(
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

    session.campaign_store = campaign_store
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


def finalize_optimization_run(
    cycle: Cycle,
    session: Session,
    emitter: CampaignPersistenceEmitter | None,
    stop_reason: StopReason,
    finished_at: str,
) -> str | None:
    """Finalize store, obs logger, emitter; return cloud trace id (or None)."""
    if session.campaign_store and session.cycle_id:
        session.campaign_store.mark_finished(
            session.backend_id,
            session.cycle_id,
            status=_campaign_status_for(stop_reason),
            stop_reason=stop_reason,
            best_accuracy=cycle.best_accuracy,
            best_round=cycle.best_round,
            n_rounds=len(cycle.rounds),
            finished_at=finished_at,
        )
    obs: ObservabilityBridge | None = session.obs
    if obs:
        obs.end_campaign(
            session.obs_campaign_id,
            best_accuracy=cycle.best_accuracy,
            n_rounds=len(cycle.rounds),
            stop_reason=stop_reason,
            best_round=cycle.best_round,
        )
    if emitter:
        emitter.finalize(
            n_rounds=len(cycle.rounds),
            best_accuracy=cycle.best_accuracy,
            best_round=cycle.best_round,
            stop_reason=stop_reason,
            cycle_id=session.cycle_id,
        )
    if stop_reason in (StopReason.INTERRUPTED, StopReason.PAUSED_FOR_REVIEW) or obs is None:
        return None
    return obs.get_langfuse_trace_id(session.obs_campaign_id)
