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
    emit_phase(on_phase, CampaignPhase.INIT, "enter", config=config, dataset=dataset)

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
        baseline_osp,
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

    # Populate infrastructure fields on state before INIT:exit so that
    # downstream phase consumers (display, persistence emitter) can read
    # cycle_id / scoring_dataset / resumed_from_round off the state ref.
    state.campaign_store = campaign_store
    state.cycle_id = cycle_id
    state.obs_campaign_id = obs_campaign_id
    state.scoring_dataset = scoring_dataset
    state.degradation_checks = degradation_checks
    state.resumed_from_round = resumed_from_round

    emit_phase(
        on_phase,
        CampaignPhase.INIT,
        "exit",
        state=state,
        config=config,
        dataset=dataset,
    )
    return state


# ---------------------------------------------------------------------------
# Campaign create / resume + observability init
# ---------------------------------------------------------------------------


def init_campaign(
    config: LoopConfig,
    dataset: list[dict],
    baseline_osp: OptSearchPoint,
    baseline_accuracy: float,
    started_at: str,
    *,
    cycle_id_override: str | None = None,
    langfuse_session_id: str | None = None,
) -> tuple[CampaignStore | None, str | None, int, ObsLogger | None, str]:
    """Resume/create campaign + init observability in one call.

    Returns ``(campaign_store, cycle_id, resumed_from_round, obs, obs_campaign_id)``.
    """
    from promptpotter.infrastructure.tracing.observability_logger import ObsLogger

    campaign_store: CampaignStore | None = None
    cycle_id: str | None = None
    resumed_from_round = 0

    if config.project_root and config.backend_id:
        try:
            from promptpotter.infrastructure.store.campaign_store import CampaignStore

            campaign_store = CampaignStore(Path(config.project_root))
            cycle_id = cycle_id_override or cycle_config_identity(
                config, baseline_osp.render(), dataset, strict=config.strict_cycle_identity
            )
            logger.debug("Cycle identity: %s", cycle_id)
            resumed_from_round = campaign_store.resume_or_create(
                config.backend_id,
                cycle_id,
                config_snapshot=config.model_dump(mode="json"),
                baseline_accuracy=baseline_accuracy,
                hot_update_keys=TUNING_KEYS if cycle_id_override else frozenset(),
            )
        except (OSError, json.JSONDecodeError, KeyError):
            logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
            campaign_store, cycle_id, resumed_from_round = None, None, 0

    if not langfuse_session_id and cycle_id:
        langfuse_session_id = cycle_id
    obs_campaign_id = cycle_id or f"campaign_{started_at[:19].replace(':', '')}"

    obs = ObsLogger.start_campaign(
        config.project_root,
        config.backend_id,
        config_snapshot=config.model_dump(mode="json"),
        baseline_accuracy=baseline_accuracy,
        dataset=dataset,
        obs_campaign_id=obs_campaign_id,
        langfuse_session_id=langfuse_session_id,
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
    """Mark campaign on disk and finalize observability. Returns cloud trace id."""
    if campaign_store and cycle_id:
        campaign_store.mark_finished(
            config.backend_id,
            cycle_id,
            status=status,
            stop_reason=stop_reason,
            best_accuracy=state.best_accuracy,
            best_round=state.best_round,
            n_rounds=len(state.rounds),
            finished_at=finished_at,
        )
    if obs:
        return obs.end_campaign(
            obs_campaign_id,
            best_accuracy=state.best_accuracy,
            n_rounds=len(state.rounds),
            stop_reason=stop_reason,
            best_round=state.best_round,
        )
    return None
