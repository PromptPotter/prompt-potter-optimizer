"""Campaign lifecycle — initialization, resume, finalization.

Campaign create/resume (``init_campaign``), cycle state initialization
(``init_cycle_state``), and campaign finalization (``finalize_campaign``).
Cycle-identity hashing lives in :mod:`promptpotter.domain.cycle_identity`.
"""

from __future__ import annotations

__all__ = [
    "finalize_campaign",
    "init_campaign",
    "init_cycle_state",
]

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign.campaign_setup import SessionEnv
from promptpotter.application.campaign.config import LoopConfig
from promptpotter.application.datasets.builder import sample_dataset
from promptpotter.application.optimization.nodes.critique import sample_thinking_styles
from promptpotter.application.scoring.metrics import compute_composite_score
from promptpotter.application.search.search_memory import SearchMemory
from promptpotter.domain.cycle_identity import TUNING_KEYS, cycle_config_identity
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.scoring import ScoringEnv
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.persistence.state import (
    CampaignPhase,
    LoopState,
    PhaseEvent,
    emit_phase,
)
from promptpotter.shared.constants import DATASET_NAME
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.campaign_store import CampaignStore
    from promptpotter.infrastructure.tracing.observability_logger import ObsLogger

logger = logging.getLogger(__name__)


def _parse_and_build_baseline(
    config: LoopConfig,
    baseline_prompt_fields: dict | None,
    baseline_accuracy: float,
    baseline_results: list | None,
) -> tuple[LoopState, OptSearchPoint]:
    """Parse prompt fields, compute composite, delegate to ``LoopState.from_baseline``."""
    if baseline_prompt_fields is None:
        raise ValueError(
            "baseline_prompt_fields is required. Run baseline evaluation in the "
            "notebook before starting the feedback cycle.",
        )
    baseline_osp = OptSearchPoint.from_prompt_fields(baseline_prompt_fields)
    logger.debug("Using provided baseline (acc=%.3f)", baseline_accuracy)

    if baseline_results:
        assert config.pipeline_schema is not None, "pipeline_schema required for composite score"
        baseline_composite = compute_composite_score(
            baseline_results,
            config.pipeline_schema,
        )["composite"]
    else:
        baseline_composite = baseline_accuracy

    state = LoopState.from_baseline(
        baseline_osp,
        baseline_accuracy,
        baseline_composite,
        task_context=config.task_context or TaskDecomposition(),
        schema=config.pipeline_schema,
        baseline_results=baseline_results,
    )
    return state, baseline_osp


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
) -> LoopState:
    """Initialize all cycle state: baseline, resume, obs, eval context.

    Returns a ``LoopState`` fully wired with infrastructure fields
    (``campaign_store``, ``cycle_id``, ``scoring_dataset``, …). The caller is
    responsible for building the persistence emitter via
    ``build_persistence_emitter`` — keeping it out of ``init_cycle_state``
    means the function only mutates ``LoopState``.
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
    state, baseline_osp = _parse_and_build_baseline(
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
        trial = campaign_store.load_trial(config.backend_id, cycle_id, resumed_from_round - 1)
        if trial:
            state.restore_from_trial(trial)
    else:
        # thinking_styles is part of OptSearchPoint.MEMORY_FIELDS — only sample
        # on fresh init, otherwise _restore_from_checkpoint's restored value wins.
        state.opt_sp.thinking_styles = sample_thinking_styles(n=3, seed=config.seed)

    # 3. Eval context + escalation checks + baseline alias
    state.scoring_ctx = ScoringEnv.for_loop(
        _bc,
        session.store,
        config.backend_id,
        config.pipeline_schema,
        obs,
        experiment_id,
        cycle_id,
        max_consecutive_errors=config.max_consecutive_errors,
        stale_data_load_protocol=config.stale_data_load_protocol,
        stale_data_observations=state.opt_sp.stale_data_observations,
        scoring_formula=config.scoring_formula,
    )
    if session.store:
        session.store.dataset_runs.register_prompt_alias(
            config.backend_id, instruction, baseline_osp.render()
        )

    from promptpotter.application.optimization.nodes.escalation import build_degradation_checks

    degradation_checks = build_degradation_checks(config)

    # 4. SearchMemory — load + refresh from historical data
    search_memory = SearchMemory.ensure_for(session.store, config.backend_id)
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

    # Populate infrastructure fields on state
    state.campaign_store = campaign_store
    state.cycle_id = cycle_id
    state.obs_campaign_id = obs_campaign_id
    state.scoring_dataset = scoring_dataset
    state.degradation_checks = degradation_checks
    state.resumed_from_round = resumed_from_round

    return state


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
) -> tuple[CampaignStore | None, str | None, int, ObsLogger | None, str]:
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


# ---------------------------------------------------------------------------
# Campaign finalization
# ---------------------------------------------------------------------------


def finalize_campaign(
    campaign_store: CampaignStore | None,
    cycle_id: str | None,
    config: LoopConfig,
    state: LoopState,
    stop_reason: str,
    finished_at: str,
    obs: ObsLogger | None,
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
