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
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.store import Stores, build_stores

if TYPE_CHECKING:
    from promptpotter.application.baseline import CampaignBaseline
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.intelligence.indexes import SampleIndex
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.elimination import DegradationCheck
    from promptpotter.application.runner import RunListener
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
    from promptpotter.infrastructure.ledger import RunLedger
    from promptpotter.infrastructure.projections import AuditTrailProjection
    from promptpotter.infrastructure.tracing import ObservabilityBridge


logger = logging.getLogger(__name__)

__all__ = [
    "HOT_UPDATEABLE_KEYS",
    "CampaignState",
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
        "seed",
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
    """Resume an existing cycle or create a new one via ``session.store.campaigns``.

    Returns ``(cycle_id, resumed_from_round)``. Hot-updateable config keys
    on the existing cycle are refreshed from the current snapshot when
    ``cycle_id_override`` is set.
    """
    import json

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
                cfg_updated = False
                for k in HOT_UPDATEABLE_KEYS:
                    if stored_cfg.get(k) != config_snapshot.get(k):
                        stored_cfg[k] = config_snapshot.get(k)
                        cfg_updated = True
                if cfg_updated and stored_cfg:
                    store.update(session.backend_id, resolved, {"config": stored_cfg})
                    logger.info("Updated loop-control config for %s", resolved)
            return resolved, len(existing.get("trials", []))
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
    """Per-cycle scoring policy + the active scoring set.

    The compiled per-query ``scorer`` and per-round ``round_scorer`` plus
    their source formulas (kept for audit + the interactive
    ``scoring_steer.json`` hot-swap), the active ``scoring_dataset``
    slice, the ``sample_index`` digest, and the ``degradation_checks``
    that travel with the scoring set. All eight fields are populated
    after Session is built, in ``populate_session_scoring`` and
    ``init_optimization_loop`` — Session is constructed before the
    active config's scoring block compiles.
    """

    scorer: Scorer | None = None
    scorer_id: str = "none"
    scorer_formula: str | None = None
    round_scorer: RoundScorer | None = None
    # ``None`` = registry default; otherwise the formula string — kept so
    # ``scoring_steer.json`` can record what it replaced.
    scorer_round_formula: str | None = None
    scoring_dataset: list[Sample] = field(default_factory=list)
    sample_index: SampleIndex | None = None
    degradation_checks: list[DegradationCheck] = field(default_factory=list)


@dataclass
class CampaignState:
    """Per-cycle mutable state — bound when ``init_optimization_loop`` fires.

    ``cycle_id`` and ``obs_campaign_id`` flip on fork; ``obs``,
    ``round_recorder``, and ``ledger`` rebind to the new fork's
    directories. ``resumed_from_round`` records where the loop picked up.
    Wiring stays directly on ``Session`` and is shared across all
    cycles minted under one Session lifetime.

    ``ledger`` is the per-cycle ``RunLedger`` (``events.jsonl``); facts
    about the cycle (decisions, phase boundaries, candidate/sample
    snapshots, measurements) are appended here in addition to the
    legacy in-memory accumulators. Projections (``LiveDashboardProjection``,
    ``AuditTrailProjection``) subscribe via ``ledger.bind`` to receive
    the same records. Phase 5 will retire the parallel callback path.
    """

    cycle_id: str = ""
    obs_campaign_id: str = ""
    resumed_from_round: int = 0
    obs: ObservabilityBridge | None = None
    round_recorder: AuditTrailProjection | None = None
    ledger: RunLedger | None = None


@dataclass
class Session:
    """Session-scoped identity + wire-up + loop-cycle infra + scoring.

    Field groups (top-down):
    - Wiring: store + backend client + pipeline schema/params + dataset
      (set at ``init_services`` time; mostly immutable thereafter).
    - ``session_id`` + ``experiment_id``: session-stable identity.
    - ``state``: per-cycle mutable bundle (``CampaignState``).
    - ``scoring``: per-cycle scoring policy (``ScoringContext``).
    - Runtime config + lifecycle hook.
    """

    # -- Wiring ----------------------------------------------------------
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
    project_root: str = ""
    pipeline_params: dict = field(default_factory=dict)

    # -- Identity --------------------------------------------------------
    session_id: str = ""

    # -- Per-cycle bundles ----------------------------------------------
    state: CampaignState = field(default_factory=CampaignState)
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
    cycle_id: str,
    baseline_acc: float = 0.0,
    baseline_prompt_fields: dict | None = None,
    dataset_size: int = 0,
    experiment_id: str | None = None,
    pipeline_params: dict | None = None,
    active_steps: list[str] | None = None,
    create_campaign_dir: bool = True,
) -> tuple[str, str]:
    """Mint (session_id, cycle_id), write session state, claim active pointer.

    ``cycle_id`` must be the full prefixed form (``"cycle_<hash>"``).
    Mutates ``session.session_id`` and ``session.state.cycle_id`` in place
    so callers don't repeat the bind. Creates the campaign dir by default
    (CLI ``init`` wants the dir to exist before ``optimize``); set
    ``create_campaign_dir=False`` to defer to ``bootstrap_cycle``.
    """
    from promptpotter.infrastructure.store import mint_session_id, save_active_pointer
    from promptpotter.infrastructure.store.base import validate_path_component

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
    sessions.ensure_narrative_files(session_id)

    if create_campaign_dir:
        session.store.campaigns.create(
            session.backend_id, cycle_id, {"parent_session_id": session_id}
        )

    session.session_id = session_id
    session.state.cycle_id = cycle_id

    save_active_pointer(session.store.tenant_id, session_id, cycle_id)
    logger.info("Auto-minted session %s + cycle %s", session_id, cycle_id)
    return session_id, cycle_id


def _apply_tenant_guard(tenant_id: str, take_over: bool, status: Callable[[str], None]) -> None:
    """Refuse tenant drift unless ``take_over=True``; clears pointer when taking over.

    The smoke tool / notebook is sessionless by design (M9 gap), so when
    taking over we clear the pointer entirely rather than writing a
    partial one that downstream ``load_session()`` would mis-resolve.
    """
    from promptpotter.infrastructure.store import clear_active_pointer, read_active_pointer
    from promptpotter.shared.errors import ActiveSessionMismatchError

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


async def _resolve_pipeline_schema(
    client: BackendClient,
    project_root: Path,
    dataset_name: str | None,
    status: Callable[[str], None],
) -> PipelineSchema | None:
    """Resolve the active pipeline schema: static ``datasets/{name}/pipeline.json`` first,
    fall back to the backend's ``GET /pipeline``.  Returns ``None`` when both fail."""
    from promptpotter.application.pipeline_discovery import parse_pipeline_response

    if dataset_name:
        cfg_path = project_root / "datasets" / dataset_name / "pipeline.json"
        if cfg_path.exists():
            try:
                schema = parse_pipeline_response(json.loads(cfg_path.read_text(encoding="utf-8")))
                status(f"Pipeline: {schema.name} ({len(schema.nodes)} nodes)")
                return schema
            except Exception as exc:
                logger.warning("Failed to parse static pipeline.json: %s", exc)

    try:
        schema = parse_pipeline_response(await client.fetch_pipeline())
        status(f"Pipeline: {schema.name} ({len(schema.nodes)} nodes)")
        return schema
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.info("Could not fetch pipeline schema: %s", exc)
        status("Pipeline: unavailable")
        return None


def _load_dataset_into_session(
    session: Session, dataset_name: str, status: Callable[[str], None]
) -> None:
    """Populate ``session.queries`` + ``session.index_terms`` from the dataset store
    (or the ``DATASET_LOADERS`` registry on first use). Raises when neither resolves."""
    from promptpotter.application.datasets.datasets import DATASET_LOADERS, samples_from_dicts

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
    session.queries = samples_from_dicts(valid)
    session.index_terms = sorted({r["ground_truth"] for r in items if r.get("ground_truth")})
    status(f"Dataset: {dataset_name} ({len(items)} queries)")


async def _sync_and_extract_experiment(
    session: Session,
    backend_url: str,
    experiment_id: str,
    status: Callable[[str], None],
) -> None:
    """Populate ``session.queries`` + ``session.index_terms`` + ``experiment_extract`` from
    the experiment trace. Auto-syncs from the backend if the on-disk extract is missing
    or trace-less. Logs a warning + returns silently when no data is available."""
    from promptpotter.application.datasets.datasets import samples_from_dicts

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
            session.synced = True
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

    from promptpotter.application.config import EXPERIMENT_EXTRACTORS

    schema_key = session.pipeline_schema.name.lower() if session.pipeline_schema else ""
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

    session.queries = samples_from_dicts(queries)
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

    client = BackendClient(backend_url)
    status(f"Backend: {backend_url}")

    pipeline_schema = await _resolve_pipeline_schema(client, project_root, dataset_name, status)

    if not store.backends.get(backend_id):
        store.backends.register(
            BackendConnection(
                id=backend_id,
                name=pipeline_schema.name if pipeline_schema else "Unknown",
                backend_type="backend" if pipeline_schema else "unknown",
                base_url=backend_url,
            )
        )

    session = Session(
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
        _load_dataset_into_session(session, dataset_name, status)
    else:
        await _sync_and_extract_experiment(session, backend_url, experiment_id, status)
    return session


async def _emit_preflight_and_init_session(
    config: CampaignConfig,
    dataset: list[Sample],
    cb: RunListener,
    session: Session,
) -> None:
    """Run preflight checks, emit ``INIT.enter``, and call backend init_session.

    Side-effects only.
    """
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
    """Build the baseline ``OptSearchPoint`` + ``Cycle.start`` and bootstrap cycle storage.

    Returns ``(cycle, baseline_osp, resolved_cycle_id, resumed_from_round)``.
    The ``baseline_osp`` is returned so the resume/fork stage can register
    its prompt alias without re-deriving it. Raises ``ValueError`` when
    ``baseline.baseline_ps`` is missing — required for OSP construction.
    """
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
    """Start the ObservabilityBridge for this cycle and populate scoring on ``session``.

    Returns ``(obs_campaign_id, obs)``. ``obs`` may be ``None`` when the
    bridge can't be built (no project_root/backend_id, or graceful failure).
    """
    from promptpotter.infrastructure.tracing import ObservabilityBridge

    opt = config.optimization
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
    return obs_campaign_id, obs


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
    """Replay decisions on resume; fork on divergence when configured. Register the
    baseline prompt alias. Returns the (possibly rebound) ``(cycle_id, resumed_from_round)``.
    """
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


def _open_cycle_ledger(session: Session, cycle_id: str) -> RunLedger | None:
    """Open the per-cycle ``RunLedger`` (``events.jsonl``) under the cycle's audit dir.

    Returns ``None`` if no store is wired (some test paths bypass storage).
    Idempotent: re-opens with cumulative offsets so resumed cycles continue
    appending after the existing tail.
    """
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.infrastructure.ledger import RunLedger

    if session.store is None:
        return None
    cycle_dir = CycleDir(session.store.campaigns.campaign_dir(cycle_id))
    return RunLedger.open(cycle_dir)


def _finalize_loop_state(
    cycle: Cycle,
    session: Session,
    config: CampaignConfig,
    dataset: list[Sample],
    cb: RunListener,
    *,
    resolved_cycle_id: str | None,
    obs_campaign_id: str,
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
        session.state.ledger = _open_cycle_ledger(session, resolved_cycle_id)
    session.state.obs_campaign_id = obs_campaign_id
    session.scoring.scoring_dataset = sample_dataset(dataset, config.sp_budget_ttest)
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
    """Build Cycle + attach loop-cycle infra onto ``session``: baseline, cycle resume, obs, scoring, axis index."""
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

    obs_campaign_id, _obs = _start_observability_and_scoring(
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
        obs_campaign_id=obs_campaign_id,
        resumed_from_round=resumed_from_round,
    )
    return cycle
