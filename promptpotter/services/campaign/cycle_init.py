"""Optimization loop initialization — baseline, resume, eval context, SearchMemory.

Extracted from ``optimization_loop.py`` to separate init-once logic
from the round loop itself.
"""

from __future__ import annotations

__all__ = ["CycleInit", "init_cycle_state"]

import hashlib
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.models.scoring_context import ScoringContext
from promptpotter.models.task_context import TaskContext
from promptpotter.services.campaign.campaign_setup import BackendContext
from promptpotter.services.campaign.config import RunConfig
from promptpotter.services.campaign.critique import sample_thinking_styles
from promptpotter.services.campaign.lifecycle import init_campaign
from promptpotter.services.campaign.state import (
    CampaignPhase,
    LoopState,
    PhaseEvent,
    emit_phase,
)
from promptpotter.services.dataset_builder import sample_dataset
from promptpotter.services.metrics import compute_composite_score
from promptpotter.services.search.search_memory import SearchMemory
from promptpotter.shared.hashing import HASH_TRUNCATE

if TYPE_CHECKING:
    from promptpotter.services.backend_client import BackendClient
    from promptpotter.services.campaign.escalation import DegradationCheck
    from promptpotter.services.campaign.persistence_emitter import CampaignPersistenceEmitter
    from promptpotter.services.stores.campaign_store import CampaignStore

logger = logging.getLogger(__name__)


class CycleInit(NamedTuple):
    """Return type for ``init_cycle_state()``."""

    state: LoopState
    campaign_store: CampaignStore | None
    cycle_id: str | None
    obs_campaign_id: str
    eval_dataset: list[dict[str, Any]]
    degradation_checks: list[DegradationCheck]
    resumed_from_round: int
    search_memory: SearchMemory | None
    persistence_emitter: CampaignPersistenceEmitter | None


def _build_baseline_state(
    config: RunConfig,
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
        task_context=config.task_context or TaskContext(),
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
    config: RunConfig,
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
        state.escalation.l2_round = _latest_trial.get("l2_round", 0)
        state.escalation.l3_round = _latest_trial.get("l3_round", 0)
        state.escalation.l2_stall_count = _latest_trial.get("l2_stall_count", 0)
        state.escalation.l3_stall_count = _latest_trial.get("l3_stall_count", 0)
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
    config: RunConfig,
    instruction: str,
    baseline_osp: OptSearchPoint,
    backend_client: BackendClient,
    obs: Any,
    experiment_id: str,
    cycle_id: str | None,
    session: BackendContext | None = None,
) -> list:
    """Wire up ScoringContext on state and build escalation checks.

    Returns:
        degradation_checks list.
    """
    from promptpotter.shared.scoring import compile_scorer

    _store = session.store if session else None
    state.eval_ctx = ScoringContext(
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

    from promptpotter.services.campaign.escalation import build_degradation_checks

    return build_degradation_checks(config)


async def init_cycle_state(
    instruction: str,
    dataset: list[dict[str, Any]],
    config: RunConfig,
    baseline_prompt_fields: dict | None,
    baseline_accuracy: float,
    baseline_results: list | None,
    on_phase: Callable[[PhaseEvent], None] | None,
    langfuse_session_id: str | None,
    cycle_id: str | None,
    experiment_id: str,
    session: BackendContext | None,
    started_at: str,
) -> CycleInit:
    """Initialize all cycle state: baseline, resume, obs, eval context."""
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
        has_scan_context=config.scan_context is not None,
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
        raise ValueError("session (BackendContext) is required for init_cycle_state")
    _bc = session.backend_client
    _index_terms = session.index_terms or None
    if _index_terms:
        await _bc.init_session(_index_terms)

    eval_dataset = sample_dataset(dataset, config.sp_budget_ttest, config.seed)

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
    _store = state.eval_ctx.store if state.eval_ctx else None
    if _store and config.backend_id:
        from pathlib import Path

        _sm_path = Path(_store.base_dir) / config.backend_id / "search_memory.json"
        search_memory = SearchMemory.load(_sm_path)
        if search_memory.refresh(_store, config.backend_id):
            search_memory.save(_sm_path)

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
        sample_count=len(eval_dataset),
        enable_critique=config.enable_critique,
        restored_state=_restored,
    )

    # 5. Persistence emitter — auto-created for all entry points
    persistence_emitter = None
    if config.project_root and config.backend_id and config.session_id:
        from pathlib import Path

        from promptpotter.services.campaign.persistence_emitter import CampaignPersistenceEmitter

        _session_dir = (
            Path(config.project_root) / config.backend_id / "sessions" / config.session_id
        )
        _session_store = None
        if _store:
            from promptpotter.services.stores.stores import SessionStore

            _session_store = SessionStore(Path(config.project_root))

        resume_from = CampaignPersistenceEmitter.load_resume_state(
            _session_dir,
            baseline=baseline_accuracy,
        )
        persistence_emitter = CampaignPersistenceEmitter(
            _session_dir,
            config,
            session_store=_session_store,
            resume_from=resume_from,
            cycle_id=cycle_id,
        )

    return CycleInit(
        state=state,
        campaign_store=campaign_store,
        cycle_id=cycle_id,
        obs_campaign_id=obs_campaign_id,
        eval_dataset=eval_dataset,
        degradation_checks=degradation_checks,
        resumed_from_round=resumed_from_round,
        search_memory=search_memory,
        persistence_emitter=persistence_emitter,
    )
