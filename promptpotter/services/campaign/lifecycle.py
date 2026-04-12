"""Campaign lifecycle — identity hashing, initialization, resume, finalization.

Combines cycle configuration identity (``TUNING_KEYS``,
``cycle_config_identity``), campaign create/resume (``init_campaign``),
cycle state initialization (``init_cycle_state``), and campaign
finalization (``finalize_campaign``) into a single module.
"""

from __future__ import annotations

__all__ = [
    "TUNING_KEYS",
    "cycle_config_identity",
    "finalize_campaign",
    "init_campaign",
    "init_cycle_state",
]

import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.scoring import ScoringEnv
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.services.campaign.campaign_setup import SessionEnv
from promptpotter.services.campaign.config import LoopConfig
from promptpotter.services.campaign.nodes.critique import sample_thinking_styles
from promptpotter.services.campaign.state import (
    CampaignPhase,
    EscalationCounters,
    LoopState,
    PhaseEvent,
    emit_phase,
)
from promptpotter.services.dataset_builder import sample_dataset
from promptpotter.services.metrics import compute_composite_score
from promptpotter.services.search.search_memory import SearchMemory
from promptpotter.shared.constants import DATASET_NAME
from promptpotter.shared.errors import graceful
from promptpotter.shared.hashing import HASH_TRUNCATE

if TYPE_CHECKING:
    from promptpotter.infrastructure.backend.client import BackendClient
    from promptpotter.infrastructure.store.campaign_store import CampaignStore
    from promptpotter.services.campaign.session_emitter import CampaignPersistenceEmitter

logger = logging.getLogger(__name__)


def _build_baseline_state(
    config: LoopConfig,
    baseline_prompt_fields: dict | None,
    baseline_accuracy: float,
    baseline_results: list | None,
) -> tuple[LoopState, OptSearchPoint]:
    """Construct LoopState from baseline config and prompt fields.

    Returns:
        (state, baseline_osp) — the initial loop state and the parsed
        baseline OptSearchPoint (needed by caller for resume/alias logic).
    """
    if baseline_prompt_fields is None:
        raise ValueError(
            "baseline_prompt_fields is required. Run baseline evaluation in the "
            "notebook before starting the feedback cycle.",
        )
    baseline_osp = (
        OptSearchPoint.from_prompt_fields(baseline_prompt_fields)
        if isinstance(baseline_prompt_fields, dict)
        else baseline_prompt_fields
    )
    current_results: list = baseline_results or []
    logger.debug("Using provided baseline (acc=%.3f)", baseline_accuracy)

    if current_results:
        assert config.pipeline_schema is not None, "pipeline_schema required for composite score"
        _bl_composite = compute_composite_score(
            current_results,
            config.pipeline_schema,
        )["composite"]
    else:
        _bl_composite = baseline_accuracy

    opt_sp = OptSearchPoint(
        task_context=config.task_context or TaskDecomposition(),
        persona=baseline_osp.persona,
        task_intent=baseline_osp.task_intent,
        problem_description=baseline_osp.problem_description,
        instruction=baseline_osp.instruction,
        thinking_style=baseline_osp.thinking_style,
        answer_format=baseline_osp.answer_format,
        plan=baseline_osp.plan,
        optimizer_params=dict(baseline_osp.optimizer_params),
    )
    schema = config.pipeline_schema
    baseline_sp = opt_sp.to_job_search_point(
        base_pipeline_params=schema.to_pipeline_params() if schema else None,
        schema=schema,
    )
    state = LoopState(
        current_sp=baseline_sp,
        current_accuracy=baseline_accuracy,
        current_composite=_bl_composite,
        current_results=current_results,
        best_accuracy=baseline_accuracy,
        best_composite=_bl_composite,
        best_sp=baseline_sp,
        opt_sp=opt_sp,
    )
    return state, baseline_osp


def _restore_from_checkpoint(
    state: LoopState,
    config: LoopConfig,
    campaign_store: CampaignStore,
    cycle_id: str,
    resumed_from_round: int,
) -> None:
    """Restore optimizer state from a campaign checkpoint (in-place).

    Only called when ``resumed_from_round > 0``.
    """
    _latest_trial = campaign_store.load_trial(
        config.backend_id,
        cycle_id,
        resumed_from_round - 1,
    )
    if _latest_trial:
        _osp = _latest_trial.get("opt_search_point", {})
        if _osp:
            known = {k: v for k, v in _osp.items() if k in OptSearchPoint.model_fields}
            missing = set(OptSearchPoint.model_fields) - set(_osp)
            if missing:
                logger.debug(
                    "Checkpoint missing %d OptSearchPoint field(s): %s "
                    "(using defaults — checkpoint predates schema change)",
                    len(missing),
                    ", ".join(sorted(missing)),
                )
            state.opt_sp = OptSearchPoint(**known)
        state.escalation = EscalationCounters.from_checkpoint_dict(_latest_trial)
        state.stall_count = _latest_trial.get("stall_count", 0)
        logger.debug(
            "Restored optimizer state from round %d "
            "(critique=%d chars, task_context=%d keys, "
            "escalation_journal=%d entries, l2_round=%d)",
            resumed_from_round - 1,
            len(state.opt_sp.critique_text),
            len(state.opt_sp.task_context),
            len(state.opt_sp.escalation_journal),
            state.escalation.l2_round,
        )


def _setup_scoring_context(
    state: LoopState,
    config: LoopConfig,
    instruction: str,
    baseline_osp: OptSearchPoint,
    backend_client: BackendClient,
    obs: Any,
    experiment_id: str,
    cycle_id: str | None,
    session: SessionEnv | None = None,
) -> list:
    """Wire up ScoringEnv on state and build escalation checks.

    Returns:
        degradation_checks list.
    """
    from promptpotter.shared.scoring import compile_scorer

    _store = session.store if session else None
    state.scoring_ctx = ScoringEnv(
        backend_client=backend_client,
        store=_store,
        backend_id=config.backend_id,
        pipeline_schema=config.pipeline_schema,
        obs=obs,
        source="optimization_loop",
        experiment_id=experiment_id or (cycle_id.replace("cycle_", "")[:12] if cycle_id else ""),
        max_consecutive_errors=config.max_consecutive_errors,
        stale_data_load_protocol=config.stale_data_load_protocol,
        stale_data_observations=state.opt_sp.stale_data_observations,
        scorer=compile_scorer(config.scoring_formula),
    )

    # Alias: raw instruction ↔ restructured baseline
    if _store and config.backend_id and instruction:
        _raw_hash = hashlib.sha256(instruction.encode()).hexdigest()[:HASH_TRUNCATE]
        _restructured_hash = hashlib.sha256(
            baseline_osp.render().encode(),
        ).hexdigest()[:HASH_TRUNCATE]
        if _raw_hash != _restructured_hash:
            _store.dataset_runs.register_alias(
                config.backend_id,
                _raw_hash,
                _restructured_hash,
            )
            logger.info(
                "Registered prompt alias: %s ↔ %s",
                _raw_hash[:8],
                _restructured_hash[:8],
            )

    from promptpotter.services.campaign.nodes.escalation import build_degradation_checks

    return build_degradation_checks(config)


async def init_cycle_state(
    instruction: str,
    dataset: list[dict[str, Any]],
    config: LoopConfig,
    baseline_prompt_fields: dict | None,
    baseline_accuracy: float,
    baseline_results: list | None,
    on_phase: Callable[[PhaseEvent], None] | None,
    langfuse_session_id: str | None,
    cycle_id: str | None,
    experiment_id: str,
    session: SessionEnv | None,
    started_at: str,
) -> tuple[LoopState, CampaignPersistenceEmitter | None]:
    """Initialize all cycle state: baseline, resume, obs, eval context.

    Returns ``(state, emitter)``. ``state`` is fully wired with infrastructure
    fields (campaign_store, cycle_id, scoring_dataset, etc.). ``emitter`` is
    kept out of ``LoopState`` so the mutation contract stays tight — the
    caller wires it onto the user's callbacks directly.
    """
    emit_phase(
        on_phase,
        CampaignPhase.INIT,
        "enter",
        max_rounds=config.max_rounds,
        patience=config.l1_patience,
        n_variants=config.n_variants,
        model=config.model or "(default)",
        sample_size=config.sp_budget_ttest,
        enable_l2=config.enable_l2,
        enable_l3=config.enable_l3,
        dataset_count=len(dataset),
        baseline_accuracy=baseline_accuracy,
        has_scan_brief=config.scan_brief is not None,
        enable_critique=config.enable_critique,
        pipeline_params=config.pipeline_schema.to_pipeline_params()
        if config.pipeline_schema
        else None,
        node_param_keys=(
            {s: sorted(k) for s, k in config.pipeline_schema.node_param_keys().items()}
            if config.pipeline_schema
            else None
        ),
    )

    if session is None:
        raise ValueError("session (SessionEnv) is required for init_cycle_state")
    _bc = session.backend_client
    _index_terms = session.index_terms or None
    if _index_terms:
        await _bc.init_session(_index_terms)

    scoring_dataset = sample_dataset(dataset, config.sp_budget_ttest, config.seed)

    # 1. Build baseline state
    state, baseline_osp = _build_baseline_state(
        config,
        baseline_prompt_fields,
        baseline_accuracy,
        baseline_results,
    )

    # 2. Resume detection + obs init
    campaign_store, cycle_id, resumed_from_round, obs, obs_campaign_id = init_campaign(
        config,
        dataset,
        baseline_osp.prompt_field_dict(),
        baseline_prompt_fields,
        baseline_accuracy,
        started_at,
        cycle_id_override=cycle_id,
        langfuse_session_id=langfuse_session_id,
    )
    if resumed_from_round > 0 and campaign_store and cycle_id:
        _restore_from_checkpoint(
            state,
            config,
            campaign_store,
            cycle_id,
            resumed_from_round,
        )
    state.opt_sp.thinking_styles = sample_thinking_styles(n=3, seed=config.seed)

    # 3. Eval context + escalation checks
    degradation_checks = _setup_scoring_context(
        state,
        config,
        instruction,
        baseline_osp,
        _bc,
        obs,
        experiment_id,
        cycle_id,
        session=session,
    )

    # 4. SearchMemory — load + refresh from historical data
    search_memory: SearchMemory | None = None
    _store = state.scoring_ctx.store if state.scoring_ctx else None
    if _store and config.backend_id:
        from pathlib import Path

        _sm_path = Path(_store.base_dir) / config.backend_id / "search_memory.json"
        search_memory = SearchMemory.load(_sm_path)
        if search_memory.refresh(_store, config.backend_id):
            search_memory.save(_sm_path)

    # Wire SearchMemory onto state (single wiring point)
    state.search_memory = search_memory
    if state.scoring_ctx and search_memory:
        state.scoring_ctx.search_memory = search_memory

    # Build restored state summary for display
    _restored = {}
    if resumed_from_round:
        _restored = {
            "critique_chars": len(state.opt_sp.critique_text),
            "task_context_keys": len(state.opt_sp.task_context),
            "escalation_journal_entries": len(state.opt_sp.escalation_journal),
            "l2_round": state.escalation.l2_round,
        }

    emit_phase(
        on_phase,
        CampaignPhase.INIT,
        "exit",
        cycle_id=cycle_id,
        resumed_from_round=resumed_from_round,
        baseline_accuracy=baseline_accuracy,
        obs_enabled=obs is not None,
        sample_count=len(scoring_dataset),
        enable_critique=config.enable_critique,
        restored_state=_restored,
    )

    # 5. Persistence emitter — auto-created for all entry points
    persistence_emitter = None
    if config.project_root and config.backend_id and config.session_id:
        from pathlib import Path

        from promptpotter.services.campaign.session_emitter import CampaignPersistenceEmitter

        _session_dir = (
            Path(config.project_root) / config.backend_id / "sessions" / config.session_id
        )

        # UI counter continuity on resume — cosmetic only. Optimizer resume
        # lives in trial_NNNN.json via _restore_from_checkpoint. Emitter picks
        # out the counters it cares about via .get() with defaults, so we can
        # just hand it the prior dict verbatim (baseline override excepted).
        resume_from: dict[str, Any] | None = None
        _prior_state = _session_dir / "campaign_state.json"
        if _prior_state.exists():
            try:
                resume_from = json.loads(_prior_state.read_text(encoding="utf-8"))
                resume_from["baseline"] = baseline_accuracy
            except (json.JSONDecodeError, OSError):
                pass

        persistence_emitter = CampaignPersistenceEmitter(
            _session_dir,
            config,
            resume_from=resume_from,
            cycle_id=cycle_id,
        )

    # Populate infrastructure fields on state
    state.campaign_store = campaign_store
    state.cycle_id = cycle_id
    state.obs_campaign_id = obs_campaign_id
    state.scoring_dataset = scoring_dataset
    state.degradation_checks = degradation_checks
    state.resumed_from_round = resumed_from_round

    return state, persistence_emitter


# ---------------------------------------------------------------------------
# Cycle identity — determines whether two runs share cached candidates
# ---------------------------------------------------------------------------

# Tuning keys — excluded from cycle identity in experiment mode.
#
# These control *how* the optimizer runs, not *what problem* it solves.
# In experiment mode (default), changing any of these between runs does NOT
# create a new cycle — cached candidates and dataset_run results carry over.
#
# In strict mode (for publication), all params are included in the hash so
# any deviation creates a distinct experiment for reproducibility.
#
# Also used by resume logic to hot-update stored configs on existing cycles.
TUNING_KEYS = frozenset(
    {
        # Loop control — how long/aggressively the loop runs
        "max_rounds",
        "l1_patience",
        "l2_patience",
        "l3_patience",
        "degradation_threshold",
        # Optimization strategy — tweakable between runs
        "model",
        "n_variants",
        "creativity",
        "improvement_threshold",
        "sp_budget_ttest",
        "seed",
    }
)


def cycle_config_identity(
    config: LoopConfig,
    baseline_rendered: str,
    dataset: list[dict],
    *,
    strict: bool = False,
) -> str:
    """Compute a stable identity hash for a feedback cycle configuration.

    Two-tier system:

    **Experiment mode** (``strict=False``, default):
        Cycle identity is based only on what defines the *problem*:
        active pipeline steps, baseline prompt, and dataset.  Everything
        else — optimizer model, seed, n_variants, creativity, patience,
        thresholds — is excluded (see ``TUNING_KEYS``).  This means you
        can freely tweak optimization strategy, switch between ``--round``
        and full loop, change the optimizer model, or interrupt and resume
        without creating a new cycle.  Cached candidates and dataset_run
        results carry over across invocations.

    **Strict mode** (``strict=True``):
        Every parameter is included in the identity hash.  Changing
        anything — even ``max_rounds`` — creates a new cycle with fresh
        candidates.  Use this only for publication experiments where exact
        reproducibility is required — the same config must always produce the
        same cycle, and any deviation must be flagged as a distinct experiment.
        Enable via ``"strict_cycle_identity": true`` in campaign.json.

    Infrastructure fields (backend_url, project_root, etc.) are always
    excluded in both modes.
    """
    payload_dict: dict[str, Any] = {
        "max_rounds": config.max_rounds,
        "l1_patience": config.l1_patience,
        "n_variants": config.n_variants,
        "creativity": config.creativity,
        "improvement_threshold": config.improvement_threshold,
        "model": config.model,
        "sp_budget_ttest": config.sp_budget_ttest,
        "seed": config.seed,
        "l2_patience": config.l2_patience,
        "l3_patience": config.l3_patience,
        "degradation_threshold": config.degradation_threshold,
        "active_steps": list(config.pipeline_schema.active_steps) if config.pipeline_schema else [],
        "baseline_rendered": baseline_rendered,
        "dataset_pairs": sorted((d.get("query", ""), d.get("ground_truth", "")) for d in dataset),
    }
    if not strict:
        for k in TUNING_KEYS:
            payload_dict.pop(k, None)
    payload = json.dumps(payload_dict, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"cycle_{digest}"


# ---------------------------------------------------------------------------
# Campaign create / resume + observability init
# ---------------------------------------------------------------------------


def init_campaign(
    config: LoopConfig,
    dataset: list[dict],
    current_ps: dict,
    baseline_prompt_fields: dict | None,
    baseline_accuracy: float,
    started_at: str,
    *,
    cycle_id_override: str | None = None,
    langfuse_session_id: str | None = None,
) -> tuple[Any | None, str | None, int, Any | None, str]:
    """Resume/create campaign + init observability in one call.

    Returns:
        (campaign_store, cycle_id, resumed_from_round, obs, obs_campaign_id)
    """
    from promptpotter.infrastructure.tracing.observability_logger import (
        ObsLogger,  # lazy: heavy dep
    )

    # --- Resume detection ---
    campaign_store = None
    cycle_id: str | None = None
    resumed_from_round = 0

    if config.project_root and config.backend_id:
        try:
            from promptpotter.infrastructure.store.campaign_store import CampaignStore

            store_base = Path(config.project_root)
            campaign_store = CampaignStore(store_base)

            if cycle_id_override:
                cycle_id = cycle_id_override
            else:
                bl_ps = baseline_prompt_fields if baseline_prompt_fields is not None else current_ps
                bl_osp = (
                    OptSearchPoint.from_prompt_fields(bl_ps) if isinstance(bl_ps, dict) else bl_ps
                )
                cycle_id = cycle_config_identity(
                    config, bl_osp.render(), dataset, strict=config.strict_cycle_identity
                )
            logger.debug("Cycle identity: %s", cycle_id)

            existing = campaign_store.load(config.backend_id, cycle_id)
            if existing is not None:
                if cycle_id_override:
                    stored_cfg = existing.get("config", {})
                    if stored_cfg:
                        _validate_config_match(config, stored_cfg, cycle_id)
                        current_cfg = config.model_dump(mode="json")
                        cfg_updated = False
                        for k in TUNING_KEYS:
                            if stored_cfg.get(k) != current_cfg.get(k):
                                stored_cfg[k] = current_cfg.get(k)
                                cfg_updated = True
                        if cfg_updated:
                            campaign_store.update(
                                config.backend_id,
                                cycle_id,
                                {"config": stored_cfg},
                            )
                            logger.info("Updated loop-control config for %s", cycle_id)
                resumed_from_round = len(existing.get("trials", []))
                if resumed_from_round:
                    logger.debug(
                        "Resuming cycle %s — %d prior round(s) on disk",
                        cycle_id,
                        resumed_from_round,
                    )
            else:
                campaign_store.create(
                    config.backend_id,
                    cycle_id,
                    {
                        "type": "optimization_loop",
                        "config": config.model_dump(mode="json"),
                        "baseline_accuracy": baseline_accuracy,
                    },
                )
        except ValueError:
            raise
        except (OSError, json.JSONDecodeError, KeyError):
            logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
            campaign_store, cycle_id, resumed_from_round = None, None, 0

    # --- Derive obs campaign ID ---
    if not langfuse_session_id and cycle_id:
        langfuse_session_id = cycle_id
    obs_campaign_id = cycle_id or f"campaign_{started_at[:19].replace(':', '')}"

    # --- Observability init ---
    obs: ObsLogger | None = None
    if config.project_root and config.backend_id:
        with graceful("Failed to create ObsLogger"):
            obs = ObsLogger(config.project_root, config.backend_id)

    if obs:
        with graceful("ObsLogger.log_campaign_start failed"):
            obs.log_campaign_start(
                campaign_id=obs_campaign_id,
                config=config.model_dump(mode="json"),
                baseline_accuracy=baseline_accuracy,
                session_id=langfuse_session_id,
            )
        with graceful("Dataset registration failed"):
            dataset_item_map = obs.register_dataset(DATASET_NAME, dataset)
            if dataset_item_map:
                logger.debug(
                    "Registered %d dataset items for '%s'",
                    len(dataset_item_map),
                    DATASET_NAME,
                )

    return campaign_store, cycle_id, resumed_from_round, obs, obs_campaign_id


def _validate_config_match(
    config: LoopConfig,
    stored_cfg: dict,
    cycle_id: str,
) -> None:
    """Validate current config matches stored campaign config."""
    check_keys = [
        "n_variants",
        "creativity",
        "improvement_threshold",
        "model",
        "sp_budget_ttest",
    ]
    mismatches = []
    for k in check_keys:
        cv = getattr(config, k, None)
        sv = stored_cfg.get(k)
        if cv != sv:
            mismatches.append(f"  {k}: stored={sv} vs current={cv}")
    if mismatches:
        raise ValueError(
            f"Config mismatch for experiment {cycle_id}.\n"
            f"Set EXPERIMENT_ID = None to start a new experiment, "
            f"or update campaign_config to match.\n" + "\n".join(mismatches)
        )


# ---------------------------------------------------------------------------
# Campaign finalization
# ---------------------------------------------------------------------------


def finalize_campaign(
    campaign_store,
    cycle_id: str | None,
    config: LoopConfig,
    state: LoopState,
    stop_reason: str,
    finished_at: str,
    obs,
    obs_campaign_id: str,
    *,
    status: str = "completed",
) -> str | None:
    """Mark campaign on disk and finalize observability.

    Returns:
        Cloud Langfuse trace ID (or None).
    """
    if campaign_store and cycle_id:
        with graceful("Campaign completion update failed"):
            campaign_store.update(
                config.backend_id,
                cycle_id,
                {
                    "status": status,
                    "stop_reason": stop_reason,
                    "best_accuracy": state.best_accuracy,
                    "best_round": state.best_round,
                    "n_rounds": len(state.rounds),
                    "finished_at": finished_at,
                },
            )

    cloud_trace_id: str | None = None
    if obs:
        with graceful("ObsLogger campaign end failed"):
            obs.log_campaign_end(
                campaign_id=obs_campaign_id,
                best_accuracy=state.best_accuracy,
                n_rounds=len(state.rounds),
                stop_reason=stop_reason,
                best_round=state.best_round,
            )
            obs.flush()
            cloud_trace_id = obs.get_cloud_trace_id(obs_campaign_id)

    return cloud_trace_id
