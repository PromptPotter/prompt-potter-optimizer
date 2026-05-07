"""Campaign initialization — Session bundle, store/client/dataset wiring, loop setup."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter import connectors
from promptpotter.application.datasets.datasets import (
    DATASET_LOADERS,
    samples_from_dicts,
)
from promptpotter.application.pipeline_discovery import parse_pipeline_response
from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
    settings,
)
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import RoundScorer, Scorer
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.store import (
    Stores,
    build_stores,
    clear_active_pointer,
    mint_session_id,
    read_active_pointer,
    save_active_pointer,
)
from promptpotter.infrastructure.store.base import read_json_optional, validate_path_component
from promptpotter.shared.errors import ActiveSessionMismatchError

if TYPE_CHECKING:
    from promptpotter.application.baseline import CampaignBaseline
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.intelligence.indexes import SampleIndex
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
    from promptpotter.domain.validators import StopRule
    from promptpotter.infrastructure.ledger import CycleLedger
    from promptpotter.infrastructure.projections import AuditTrailProjection
    from promptpotter.infrastructure.tracing import LangfuseLogger, ObservabilityBridge


logger = logging.getLogger(__name__)

__all__ = [
    "HOT_UPDATEABLE_KEYS",
    "CampaignBundle",
    "ScoringContext",
    "Session",
    "TenantContext",
    "auto_mint_session",
    "bootstrap_cycle",
    "init_optimization_loop",
    "init_services",
    "new_session_state",
    "populate_session_scoring",
]


# Hot-updateable on resume — these don't change WHAT the cycle solves, only HOW it searches.
HOT_UPDATEABLE_KEYS: frozenset[str] = frozenset(
    {
        "max_rounds",
        "l1_patience",
        "l2_patience",
        "l3_patience",
        "degradation_threshold",
        "model",
        "n_variants",
        "creativity",
        "improvement_threshold",
        "sp_budget_ttest",
    }
)


def bootstrap_cycle(
    config: CampaignConfig,
    session: Session,
    baseline_jsp: JobSearchPoint,
    baseline_accuracy: float,
    dataset: list,
    cycle_id_override: str | None,
    *,
    parent_session_id: str = "",
    resume_from_round_override: int | None = None,
) -> tuple[str | None, int]:
    """Resume an existing cycle or create one. Returns (cycle_id, resumed_from_round).

    Hot-updateable config keys refresh from the current snapshot when
    cycle_id_override is set.
    """
    from promptpotter.application.runner import cycle_config_identity

    if not session.backend_id:
        return None, 0
    try:
        store = session.store.campaigns
        resolved = cycle_id_override or cycle_config_identity(baseline_jsp, dataset)
        if resume_from_round_override is not None:
            store.rewind_to_round(session.backend_id, resolved, resume_from_round_override)
        config_snapshot = config.model_dump(mode="json")
        existing = store.load(session.backend_id, resolved)
        if existing is not None:
            if cycle_id_override:
                stored_cfg = existing.get("config", {}) or {}
                changed = {
                    k: config_snapshot.get(k)
                    for k in HOT_UPDATEABLE_KEYS
                    if stored_cfg.get(k) != config_snapshot.get(k)
                }
                if changed:
                    stored_cfg.update(changed)
                    store.update(session.backend_id, resolved, {"config": stored_cfg})
                    logger.info("Updated loop-control config for %s", resolved)
            # Diag forks inherit parent's baseline_accuracy but re-measure
            # against their own JSP — refresh top-level field if drift.
            if existing.get("baseline_accuracy") != baseline_accuracy:
                store.update(session.backend_id, resolved, {"baseline_accuracy": baseline_accuracy})
            return resolved, len(existing.get("rounds", []))
        store.create(
            session.backend_id,
            resolved,
            {
                "type": "optimization_loop",
                "config": config_snapshot,
                "baseline_accuracy": baseline_accuracy,
                "parent_session_id": parent_session_id,
            },
        )
        return resolved, 0
    except (OSError, json.JSONDecodeError, KeyError):
        logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
        return None, 0


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: str | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass
class ScoringContext:
    """Per-cycle scoring policy + active scoring set; populated post-Session-init."""

    scorer: Scorer | None = None
    scorer_id: str = "none"
    scorer_formula: str | None = None
    round_scorer: RoundScorer | None = None
    scorer_round_formula: str | None = (
        None  # None = registry default; else what scoring_steer.json replaced.
    )
    scoring_set: list[Sample] = field(default_factory=list)
    sample_index: SampleIndex | None = None
    degradation_checks: list[StopRule] = field(default_factory=list)


@dataclass
class CampaignBundle:
    """Per-cycle mutable bundle — flips on fork; ledger is the sole event ingress.

    Bundle of objects (ledger, projection writer, observability bridge), not a
    state machine. ``EscalationState`` is the FSM; this is plumbing.
    """

    cycle_id: str = ""
    tracing_campaign_id: str = ""
    resumed_from_round: int = 0
    obs: ObservabilityBridge | None = None
    audit_projection: AuditTrailProjection | None = None
    ledger: CycleLedger | None = None


@dataclass
class Session:
    """Session-scoped wiring + identity + per-cycle bundles."""

    # -- Wiring ----------------------------------------------------------
    store: Stores
    backend_id: str
    experiment_id: str  # Backend-side experiment id (TermNorm vocabulary). Distinct from cycle_id.
    backend_client: BackendClient
    pipeline_schema: PipelineSchema | None
    backend_index_synced: bool
    samples: list[Sample] = field(default_factory=list)
    experiment_extract: dict = field(default_factory=dict)
    index_terms: list[str] = field(default_factory=list)
    tenant: TenantContext | None = None
    dataset_name: str | None = None
    project_root: str = ""
    pipeline_params: dict = field(default_factory=dict)
    langfuse: LangfuseLogger | None = None

    # -- Identity --------------------------------------------------------
    session_id: str = ""

    # -- Per-cycle bundles ----------------------------------------------
    state: CampaignBundle = field(default_factory=CampaignBundle)
    scoring: ScoringContext = field(default_factory=ScoringContext)

    # -- Runtime config --------------------------------------------------
    max_consecutive_errors: int = 3
    stale_data_load_protocol: list[str] | None = None
    source: str = ""

    # -- Lifecycle hook --------------------------------------------------
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
    from promptpotter.application.scoring.formula import (
        auto_scorer_id,
        compile_round_scorer,
        compile_scorer,
    )

    session.experiment_id = experiment_id or (
        cycle_id.replace("cycle_", "")[:12] if cycle_id else ""
    )
    session.state.obs = obs
    session.source = source
    session.max_consecutive_errors = max_consecutive_errors
    session.stale_data_load_protocol = stale_data_load_protocol
    session.scoring.scorer = compile_scorer(scoring_formula)
    session.scoring.scorer_id = scorer_id or auto_scorer_id(scoring_formula)
    session.scoring.scorer_formula = scoring_formula
    session.scoring.round_scorer = (
        compile_round_scorer(scoring_round_formula) if scoring_round_formula else None
    )
    session.scoring.scorer_round_formula = scoring_round_formula


def new_session_state(
    *,
    init_params: dict,
    campaign_config: dict,
    pipeline_params: dict,
    active_steps: list[str],
) -> dict[str, Any]:
    """Fresh campaign session-state dict (shared by CLI init + orchestrator)."""
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


def _build_index_header(session: Session, dataset_size: int) -> dict[str, Any]:
    """index.json header — tool/version/pipeline/backend/dataset identity."""
    from promptpotter.config.settings import APP_VERSION

    schema = session.pipeline_schema
    nodes = list(schema.nodes) if schema else []
    return {
        "tool": "promptpotter",
        "version": APP_VERSION,
        "n_nodes": len(nodes),
        "steps": [n.name for n in nodes],
        "backend_url": session.backend_client.base_url,
        "backend_id": session.backend_id,
        "dataset_name": session.dataset_name,
        "dataset_size": dataset_size,
    }


def auto_mint_session(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    cycle_id: str,
    baseline_acc: float = 0.0,
    baseline_prompt_fields: dict | None = None,
    dataset_size: int = 0,
    experiment_id: str | None = None,
    pipeline_params: dict | None = None,
    active_steps: list[str] | None = None,
    create_campaign_dir: bool = True,
) -> tuple[str, str]:
    """Mint session_id, write session state, claim active pointer; mutates session in place."""
    cycle_hash = cycle_id.removeprefix("cycle_")
    validate_path_component(cycle_hash)
    session_id = mint_session_id()

    state = new_session_state(
        init_params={
            "backend_url": session.backend_client.base_url,
            "backend_id": session.backend_id,
            "experiment_id": experiment_id,
            "dataset_name": session.dataset_name,
        },
        campaign_config=campaign_config.model_dump(),
        pipeline_params=pipeline_params or {},
        active_steps=list(active_steps or []),
    )
    state["baseline_accuracy"] = baseline_acc
    state["dataset_count"] = dataset_size
    state["baseline_prompt_fields"] = baseline_prompt_fields or {}

    sessions = session.store.sessions
    sessions.create(session_id, state)

    if create_campaign_dir:
        session.store.campaigns.create(
            session.backend_id,
            cycle_id,
            {
                "parent_session_id": session_id,
                "header": _build_index_header(session, dataset_size),
            },
        )

    session.session_id = session_id
    session.state.cycle_id = cycle_id

    save_active_pointer(session.store.tenant_id, session_id, cycle_id)
    logger.info("Auto-minted session %s + cycle %s", session_id, cycle_id)
    return session_id, cycle_id


def _apply_tenant_guard(tenant_id: str, take_over: bool, status: Callable[[str], None]) -> None:
    """Refuse tenant drift unless take_over=True; on take-over, clear the pointer."""
    active_tid, active_sid, _ = read_active_pointer()
    if not (active_tid and active_tid != tenant_id):
        return
    if not take_over:
        raise ActiveSessionMismatchError(
            active_tenant_id=active_tid,
            active_session_id=active_sid,
            requested_tenant_id=tenant_id,
        )
    clear_active_pointer()
    status(f"Took over active session: cleared pointer (was tenant {active_tid!r})")


def _apply_dataset_overlay(backend_resp: dict, local_raw: dict) -> dict:
    """Merge dataset pipeline.json overlay onto the backend response.

    Dataset overlay can carry: ``pipelines.default`` (which subset of nodes
    is active for this dataset), per-node config deltas, and metadata like
    ``available_models`` / ``prompt_meta`` / ``optimizer.param_keys``. The
    backend stays SoT for runtime defaults; the overlay layers operator
    intent on top.
    """
    out = copy.deepcopy(backend_resp.get("data") or backend_resp)
    if "pipelines" in local_raw:
        out["pipelines"] = local_raw["pipelines"]
    for node_name, node_def in (local_raw.get("nodes") or {}).items():
        if not isinstance(node_def, dict):
            continue
        out.setdefault("nodes", {}).setdefault(node_name, {})
        for k, v in node_def.items():
            if k == "config" and isinstance(v, dict):
                out["nodes"][node_name].setdefault("config", {}).update(v)
            else:
                out["nodes"][node_name][k] = v
    return out


async def _resolve_pipeline_schema(
    client: BackendClient,
    project_root: Path,
    dataset_name: str | None,
    status: Callable[[str], None],
) -> PipelineSchema | None:
    """Backend ``GET /pipeline`` is authoritative for runtime defaults.

    Local ``datasets/{name}/pipeline.json`` is the operator overlay:
    ``pipelines.default`` selects the active node subset, ``nodes.X.config``
    carries per-key deltas, plus per-dataset metadata. The two are merged
    here before parsing — backend underneath, dataset on top. Backend
    unreachable → fall back to the local file alone (offline mode).
    """
    backend_resp: dict | None = None
    try:
        backend_resp = await client.fetch_pipeline()
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.info("Could not fetch pipeline schema from backend: %s", exc)

    local_raw: dict | None = None
    if dataset_name:
        local_raw = read_json_optional(project_root / "datasets" / dataset_name / "pipeline.json")

    if backend_resp:
        merged = _apply_dataset_overlay(backend_resp, local_raw or {})
        try:
            schema = parse_pipeline_response(merged)
            status(f"Pipeline: {schema.name} ({len(schema.nodes)} nodes)")
            return schema
        except Exception as exc:
            logger.warning("Failed to parse merged pipeline schema: %s", exc)

    if local_raw is not None:
        try:
            schema = parse_pipeline_response(local_raw)
            status(f"Pipeline: {schema.name} ({len(schema.nodes)} nodes, offline)")
            return schema
        except Exception as exc:
            logger.warning("Failed to parse offline pipeline.json: %s", exc)

    status("Pipeline: unavailable")
    return None


def _read_backend_type(project_root: Path, dataset_name: str | None) -> str:
    """Resolve backend_type from datasets/{name}/pipeline.json. Required field."""
    if not dataset_name:
        raise ValueError("dataset_name required to resolve backend_type for connector lookup")
    raw = read_json_optional(project_root / "datasets" / dataset_name / "pipeline.json")
    bt = (raw or {}).get("backend_type")
    if not isinstance(bt, str) or not bt:
        raise ValueError(f"backend_type missing or empty in datasets/{dataset_name}/pipeline.json")
    return bt.lower()


def _load_dataset_into_session(
    session: Session, dataset_name: str, status: Callable[[str], None]
) -> None:
    """Populate session.samples + index_terms from DatasetStore or DATASET_LOADERS."""
    ds = session.store.backends.load_dataset(dataset_name)
    if not (ds and ds.get("items")) and dataset_name in DATASET_LOADERS:
        status(f"Loading dataset '{dataset_name}' from registry ...")
        loader_items = DATASET_LOADERS[dataset_name]()
        session.store.backends.save_dataset(dataset_name, loader_items)
        ds = {"items": loader_items}

    if not (ds and ds.get("items")):
        status(f"Dataset '{dataset_name}' not available")
        raise ValueError(
            f"Dataset {dataset_name!r} not found in DatasetStore or DATASET_LOADERS. "
            f"Add a loader to DATASET_LOADERS in dataset_builder.py."
        )

    items = ds["items"]
    valid = [item for item in items if item.get("query") and item.get("ground_truth")]
    session.samples = samples_from_dicts(valid)
    session.index_terms = sorted({r["ground_truth"] for r in items if r.get("ground_truth")})
    status(f"Dataset: {dataset_name} ({len(items)} queries)")


async def _sync_and_extract_experiment(
    session: Session,
    backend_url: str,
    experiment_id: str,
    status: Callable[[str], None],
) -> None:
    """Populate queries/index_terms/experiment_extract; auto-sync from backend if missing."""
    backend_id = session.backend_id
    extract = session.store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")
    has_traces = bool(extract and extract.get("runs") and extract["runs"][0].get("traces"))

    if not extract or not has_traces:
        reason = "No stored experiment data" if not extract else "Stored data has no traces"
        logger.info("%s — syncing from %s ...", reason, backend_url)
        status(f"Syncing experiment {experiment_id} ...")
        try:
            extract = await session.backend_client.sync_experiment(
                session.store, backend_id, experiment_id, include_traces=True
            )
            session.backend_index_synced = True
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

    schema_key = session.pipeline_schema.name.lower() if session.pipeline_schema else ""
    try:
        connector = connectors.get(schema_key) if schema_key else None
    except KeyError:
        connector = None
    if connector is not None:
        queries, index_terms = connector.extract_experiment(extract)
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

    session.samples = samples_from_dicts(queries)
    session.experiment_extract = extract
    session.index_terms = index_terms


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
    """Init store, client, pipeline schema, scoring data. Refuses tenant drift unless ``take_over=True``."""

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    if not backend_id:
        backend_id = dataset_name or DEFAULT_BACKEND_ID
    if project_root is None:
        # campaign/campaign_setup.py → services → promptpotter → repo_root
        project_root = Path(__file__).resolve().parent.parent.parent.parent

    store = build_stores(project_root / ".promptpotter" / "projects", tenant_id=tenant_id)
    _apply_tenant_guard(tenant_id, take_over, status)

    backend_type = _read_backend_type(project_root, dataset_name)
    connector = connectors.get(backend_type)
    client = BackendClient(
        backend_url,
        wire_adapter=connector.wire_adapter,
        session=connector.session_factory(),
        auth_token=settings.TERMNORM_TOKEN or None,
    )
    status(f"Backend: {backend_url}")

    pipeline_schema = await _resolve_pipeline_schema(client, project_root, dataset_name, status)

    if not store.backends.get(backend_id):
        store.backends.register(
            BackendConnection(
                id=backend_id,
                name=pipeline_schema.name if pipeline_schema else "Unknown",
                backend_type=backend_type,
                base_url=backend_url,
            )
        )

    from promptpotter.infrastructure.tracing import LangfuseLogger

    session = Session(
        store=store,
        backend_id=backend_id,
        experiment_id=experiment_id,
        backend_client=client,
        pipeline_schema=pipeline_schema,
        backend_index_synced=False,
        dataset_name=dataset_name,
        tenant=TenantContext(tenant_id=tenant_id),
        project_root=str(store.base_dir),
        langfuse=LangfuseLogger(),
    )

    if dataset_name:
        _load_dataset_into_session(session, dataset_name, status)
    else:
        await _sync_and_extract_experiment(session, backend_url, experiment_id, status)
    return session


async def _emit_preflight_and_init_session(
    config: CampaignConfig,
    dataset: list[Sample],
    cb: RunCallbacks,
    session: Session,
) -> None:
    """Preflight + emit INIT.enter + backend.init_session."""
    from promptpotter.application.config import run_preflight_checks
    from promptpotter.domain.phases import CampaignPhase, emit_phase

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


def _build_cycle_and_bootstrap(
    baseline: CampaignBaseline,
    task_context: TaskDecomposition,
    scoring_round_formula: str | None,
    session: Session,
    config: CampaignConfig,
    dataset: list[Sample],
    cycle_id: str | None,
    resume_from_round_override: int | None,
) -> tuple[Cycle, OptSearchPoint, str | None, int]:
    """Build baseline OSP + Cycle.start + bootstrap storage; raise on missing baseline_ps."""
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.scoring.formula import compile_round_scorer

    if baseline.baseline_ps is None:
        raise ValueError("baseline.baseline_ps is required; run baseline scoring first.")
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
    return cycle, baseline_osp, resolved_cycle_id, resumed_from_round


def _start_observability_and_scoring(
    session: Session,
    config: CampaignConfig,
    baseline: CampaignBaseline,
    dataset: list[Sample],
    *,
    resolved_cycle_id: str | None,
    started_at: str,
    langfuse_session_id: str | None,
    experiment_id: str,
    scoring_formula: str | None,
    scoring_round_formula: str | None,
    scorer_id: str,
) -> tuple[str, ObservabilityBridge | None]:
    """Start ObservabilityBridge + populate scoring; obs may be None on failure."""
    from promptpotter.infrastructure.tracing import ObservabilityBridge

    opt = config.optimization
    tracing_campaign_id = resolved_cycle_id or f"campaign_{started_at[:19].replace(':', '')}"
    obs = ObservabilityBridge.start_campaign(
        session.project_root,
        session.backend_id,
        config_snapshot=config.model_dump(mode="json"),
        baseline_accuracy=baseline.baseline_acc,
        dataset=dataset,
        tracing_campaign_id=tracing_campaign_id,
        langfuse_session_id=langfuse_session_id or resolved_cycle_id,
        langfuse=session.langfuse,
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
    return tracing_campaign_id, obs


def _apply_resume_fork(
    session: Session,
    cycle: Cycle,
    baseline: CampaignBaseline,
    baseline_osp: OptSearchPoint,
    resolved_cycle_id: str | None,
    resumed_from_round: int,
    *,
    no_divergence_check: bool,
    fork_on_divergence: bool,
) -> tuple[str | None, int]:
    """Replay decisions; fork on divergence; register baseline alias. Returns possibly-rebound (id, round)."""
    from promptpotter.application.optimization.cycle import resume_with_divergence_check

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
    return resolved_cycle_id, resumed_from_round


def _open_cycle_ledger(session: Session, cycle_id: str) -> CycleLedger | None:
    """Open per-cycle CycleLedger; None when no store; idempotent (offsets cumulate)."""
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.infrastructure.ledger import CycleLedger

    if session.store is None:
        return None
    cycle_dir = CycleDir(session.store.campaigns.campaign_dir(cycle_id))
    return CycleLedger.open(cycle_dir)


def _finalize_loop_state(
    cycle: Cycle,
    session: Session,
    config: CampaignConfig,
    dataset: list[Sample],
    cb: RunCallbacks,
    *,
    resolved_cycle_id: str | None,
    tracing_campaign_id: str,
    resumed_from_round: int,
) -> None:
    """Init AxisIndex, write final session/cycle state, emit ``INIT.exit``."""
    from promptpotter.application.datasets.datasets import sample_dataset
    from promptpotter.application.intelligence.indexes import AxisIndex
    from promptpotter.application.optimization.elimination import build_degradation_checks
    from promptpotter.domain.phases import CampaignPhase, emit_phase

    cycle.axes = AxisIndex.ensure_for(
        session.store,
        session.backend_id,
        scorer=session.scoring.scorer,
        scorer_id=session.scoring.scorer_id,
        scorer_formula=session.scoring.scorer_formula,
    )

    if resolved_cycle_id:
        session.state.cycle_id = resolved_cycle_id
        # Idempotent: runner.py may have pre-opened the ledger.
        if session.state.ledger is None:
            session.state.ledger = _open_cycle_ledger(session, resolved_cycle_id)
    session.state.tracing_campaign_id = tracing_campaign_id
    session.scoring.scoring_set = sample_dataset(dataset, config.sp_budget_ttest)
    session.scoring.degradation_checks = build_degradation_checks(config)
    session.state.resumed_from_round = resumed_from_round

    emit_phase(
        cb.on_phase,
        CampaignPhase.INIT,
        "exit",
        state=cycle,
        env=session,
        config=config,
        dataset=dataset,
    )


async def init_optimization_loop(
    baseline: CampaignBaseline,
    dataset: list[Sample],
    config: CampaignConfig,
    *,
    cb: RunCallbacks,
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
    """Build Cycle + attach loop infra: baseline, resume/fork, obs, scoring, axes."""
    await _emit_preflight_and_init_session(config, dataset, cb, session)

    cycle, baseline_osp, resolved_cycle_id, resumed_from_round = _build_cycle_and_bootstrap(
        baseline,
        task_context,
        scoring_round_formula,
        session,
        config,
        dataset,
        cycle_id,
        resume_from_round_override,
    )

    tracing_campaign_id, _obs = _start_observability_and_scoring(
        session,
        config,
        baseline,
        dataset,
        resolved_cycle_id=resolved_cycle_id,
        started_at=started_at,
        langfuse_session_id=langfuse_session_id,
        experiment_id=experiment_id,
        scoring_formula=scoring_formula,
        scoring_round_formula=scoring_round_formula,
        scorer_id=scorer_id,
    )

    resolved_cycle_id, resumed_from_round = _apply_resume_fork(
        session,
        cycle,
        baseline,
        baseline_osp,
        resolved_cycle_id,
        resumed_from_round,
        no_divergence_check=no_divergence_check,
        fork_on_divergence=fork_on_divergence,
    )

    _finalize_loop_state(
        cycle,
        session,
        config,
        dataset,
        cb,
        resolved_cycle_id=resolved_cycle_id,
        tracing_campaign_id=tracing_campaign_id,
        resumed_from_round=resumed_from_round,
    )
    return cycle
