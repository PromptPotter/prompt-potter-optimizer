"""Round execution — generate, evaluate, select winner, update state.

Handles individual round mechanics for the feedback cycle, including
adaptive eval set sampling.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign.callbacks import RunCallbacks
from promptpotter.application.campaign.config import LoopConfig
from promptpotter.application.optimization.loop_env import LoopEnv
from promptpotter.application.optimization.loop_state import LoopState
from promptpotter.application.optimization.nodes.critique import (
    CritiqueAgent,
    format_critique_for_prompt,
    sample_thinking_styles,
)
from promptpotter.application.optimization.nodes.critique_payload import (
    RoundSnapshot,
    update_query_tracker,
)
from promptpotter.application.optimization.nodes.formatting import candidate_summaries
from promptpotter.application.optimization.phases import CampaignPhase, emit_phase
from promptpotter.application.optimization.results import RoundResult
from promptpotter.domain.opt_search_point import OptSearchPoint

# Module-level import for test monkeypatching.
from promptpotter.infrastructure.llm import client as _llm_client
from promptpotter.infrastructure.tracing.observability_logger import observed_node
from promptpotter.shared.constants import PROMPT_STRING_FIELDS
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.optimization.nodes.escalation import DegradationCheck
    from promptpotter.application.optimization.nodes.score import L1ScoringResult
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.tracing.observability_logger import ObsLogger

logger = logging.getLogger(__name__)

__all__ = ["PauseForReviewError", "execute_round", "update_round_state"]


class PauseForReviewError(Exception):
    """Raised when HITL mode pauses the loop for human/AI review."""

    def __init__(
        self,
        candidates: list[dict],
        round_num: int,
        pause_point: str,
    ) -> None:
        self.candidates = candidates
        self.round_num = round_num
        self.pause_point = pause_point  # CampaignPhase.L1_GENERATE, "before_l2_eval", "user_pause"
        super().__init__(
            f"Paused at {pause_point}: {len(candidates)} candidates (round {round_num})"
        )


async def _generate_or_load_candidates(
    round_num: int,
    state: LoopState,
    env: LoopEnv,
    config: LoopConfig,
    on_phase=None,
    n_eval_queries: int = 0,
    *,
    obs: ObsLogger | None = None,
    trace_id: str | None = None,
    search_memory: Any = None,
) -> list[dict]:
    """Load persisted candidates or generate fresh ones via LLM."""
    # Resolve L2 meta-param overrides from OptSearchPoint.optimizer_params
    # Cap n_variants to 3× config to prevent L2 from blowing up eval budget
    opt_params = state.opt_sp.optimizer_params
    _n_variants = min(opt_params.get("n_variants", config.n_variants), config.n_variants * 3)
    _creativity = opt_params.get("creativity", config.creativity)
    prompt_preview = state.opt_sp.render()[:120]

    assert state.current_sp is not None
    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "enter",
        round=round_num,
        current_accuracy=state.current_accuracy,
        prompt_preview=prompt_preview,
        n_variants=_n_variants,
        creativity=_creativity,
        model=config.model or "(default)",
        has_recon_brief=config.recon_brief is not None,
        has_critique=bool(state.opt_sp.memory.critique_text),
        pipeline_params=state.current_sp.pipeline_params,
    )

    if env.campaign_store and env.cycle_id:
        persisted = env.campaign_store.load_round_candidates(
            config.backend_id,
            env.cycle_id,
            round_num,
        )
        if persisted is not None:
            logger.debug(
                "Loaded %d persisted candidates for round %d",
                len(persisted),
                round_num,
            )
            emit_phase(
                on_phase,
                CampaignPhase.L1_GENERATE,
                "exit",
                round=round_num,
                n_candidates=len(persisted),
                n_eval_queries=n_eval_queries,
                loaded_from_disk=True,
                candidates=candidate_summaries(persisted),
            )
            return persisted

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    from promptpotter.application.optimization.nodes.generate import l1_generate

    sm_ctx = search_memory.to_l1_digest() if search_memory else None

    client = _llm_client.get_llm_client()
    async with observed_node(f"l1_generate_r{round_num}", "llm/meta", obs=obs, trace_id=trace_id):
        candidates = await l1_generate(
            state.opt_sp,
            state.current_accuracy,
            state.current_results,
            _n_variants,
            _creativity,
            client,
            model=config.model,
            recon_brief=config.recon_brief,
            is_probe_round=state.probe_next_round,
            scan_compact=(round_num > 0),
            failure_analysis=state.failure_analysis,
            search_memory_digest=sm_ctx,
            pipeline_schema=config.pipeline_schema,
        )

    if env.campaign_store and env.cycle_id:
        env.campaign_store.save_round_candidates(
            config.backend_id,
            env.cycle_id,
            round_num,
            candidates,
        )

    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "exit",
        round=round_num,
        n_candidates=len(candidates),
        n_eval_queries=n_eval_queries,
        loaded_from_disk=False,
        candidates=candidate_summaries(candidates),
    )

    return candidates


async def _run_critique(
    scoring_result: L1ScoringResult,
    round_num: int,
    state: LoopState,
    config: LoopConfig,
) -> str:
    """Run critique analysis on evaluation results. Returns formatted critique text."""
    if not config.enable_critique or not scoring_result.winner_results:
        return ""

    crit_llm = _llm_client.get_llm_client()
    agent = CritiqueAgent(crit_llm, model=config.model)

    cctx = RoundSnapshot.from_round_state(
        state,
        scoring_result,
        config,
        round_num=round_num,
        search_memory_digest=(
            state.search_memory.to_critique_digest() if state.search_memory else None
        ),
    )
    result = await agent.run(cctx)
    return format_critique_for_prompt(result)


async def _score_and_select(
    candidates: list[dict],
    round_num: int,
    state: LoopState,
    env: LoopEnv,
    scoring_dataset: list[dict],
    config: LoopConfig,
    callbacks: RunCallbacks,
    obs: ObsLogger | None = None,
    trace_id: str | None = None,
    degradation_checks: list[DegradationCheck] | None = None,
) -> L1ScoringResult:
    """Evaluate candidates, run critique, select winner."""
    from promptpotter.application.optimization.nodes.score import l1_score

    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "enter",
        round=round_num,
        n_candidates=len(candidates),
        n_queries=len(scoring_dataset),
        current_best_accuracy=state.current_accuracy,
        improvement_threshold=config.improvement_threshold,
        current_pipeline_params=state.current_sp.pipeline_params if state.current_sp else None,
    )

    current_best = {
        "accuracy": state.current_accuracy,
        "composite": state.current_composite,
        "prompt_fields": state.opt_sp.prompt_field_dict(),
        "results": state.current_results,
        "label": f"round_{round_num}" if round_num > 0 else "baseline",
    }

    async with observed_node(
        f"l1_score_r{round_num}", "scoring", obs=obs, trace_id=trace_id, obs_type="span"
    ):
        assert env.scoring_ctx is not None
        assert state.current_sp is not None
        scoring_result = await l1_score(
            candidates,
            scoring_dataset,
            current_best,
            env.scoring_ctx,
            pipeline_params=state.current_sp.pipeline_params,
            improvement_threshold=config.improvement_threshold,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            elimination_n_min=config.elimination_n_min,
            elimination_alpha=config.elimination_alpha,
        )

        scoring_result.critique_text = await _run_critique(scoring_result, round_num, state, config)
        scoring_result.thinking_styles = sample_thinking_styles(
            n=3, seed=config.seed + round_num + 1
        )

    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "exit",
        round=round_num,
        winner_label=scoring_result.label,
        winner_accuracy=scoring_result.winner_accuracy,
        winner_composite=scoring_result.winner_composite,
        improved=scoring_result.improved,
        candidate_scores=scoring_result.candidate_scores,
        critique_text=scoring_result.critique_text,
    )

    return scoring_result


async def execute_round(
    round_num: int,
    state: LoopState,
    env: LoopEnv,
    scoring_dataset: list[dict],
    config: LoopConfig,
    callbacks: RunCallbacks,
    degradation_checks: list[DegradationCheck] | None = None,
    search_memory: Any = None,
) -> RoundResult:
    """Execute one optimization round: generate → evaluate → select winner → obs log."""
    obs = env.scoring_ctx.obs if env.scoring_ctx else None
    trace_id = obs.get_file_trace_id(env.obs_campaign_id) if obs else None
    if obs:
        with graceful("ObsLogger.log_round_start failed"):
            obs.log_round_start(env.obs_campaign_id, round_num)

    candidates = await _generate_or_load_candidates(
        round_num,
        state,
        env,
        config,
        callbacks.on_phase,
        n_eval_queries=len(scoring_dataset),
        obs=obs,
        trace_id=trace_id,
        search_memory=search_memory,
    )

    if config.pause_before_scoring:
        raise PauseForReviewError(candidates, round_num, pause_point="before_scoring")

    scoring_result = await _score_and_select(
        candidates,
        round_num,
        state,
        env,
        scoring_dataset,
        config,
        callbacks,
        obs=obs,
        trace_id=trace_id,
        degradation_checks=degradation_checks,
    )

    # Update state with critique + thinking styles from eval output
    state.opt_sp.memory.critique_text = scoring_result.critique_text
    state.opt_sp.memory.thinking_styles = scoring_result.thinking_styles

    # Compute failure analysis for next round's L1 context (Wave 1c)
    if scoring_result.winner_results and config.pipeline_schema:
        from promptpotter.application.scoring.metrics import compile_failure_analysis

        state.failure_analysis = compile_failure_analysis(
            scoring_result.winner_results,
            config.pipeline_schema,
        )
    else:
        state.failure_analysis = None

    round_result = RoundResult(
        round=round_num,
        label=scoring_result.label,
        accuracy=scoring_result.winner_accuracy,
        composite=scoring_result.winner_composite,
        hits=scoring_result.hits,
        total=scoring_result.total,
        improved=scoring_result.improved,
        prompt_fields=scoring_result.winner_prompt_fields,
        pipeline_params=scoring_result.winner_pipeline_params,
        results=scoring_result.winner_results,
        candidates_scored=scoring_result.candidates_scored,
        candidate_scores=scoring_result.candidate_scores,
        degraded_queries=scoring_result.degraded_queries,
        escalation_signal=scoring_result.escalation_signal,
    )

    # Update per-query warning inventory from ALL candidate results
    # (not just winner — aborted candidates carry the pipeline warnings)
    _all_results: list = [r for rs in scoring_result.all_candidate_results.values() for r in rs]
    if _all_results:
        update_query_tracker(state.opt_sp.memory.warning_inventory, _all_results)

    if obs:
        with graceful("ObsLogger.log_round_end failed"):
            obs.log_round_end(
                campaign_id=env.obs_campaign_id,
                round_num=round_num,
                accuracy=scoring_result.winner_accuracy,
                hits=scoring_result.hits,
                total=scoring_result.total,
                improved=scoring_result.improved,
                winner_prompt_fields_id=scoring_result.winner_prompt_fields.get("id", ""),
                candidate_scores=scoring_result.candidate_scores,
                model=config.model or "",
                n_variants=config.n_variants,
                optimizer_templates=["meta_scan_aware", "critique"],
            )
        with graceful("ObsLogger.log_prompt_version failed"):
            winner_fields = scoring_result.winner_prompt_fields
            winner_osp = OptSearchPoint.from_prompt_fields(winner_fields)
            obs.log_prompt_version(
                prompt_fields_id=winner_osp.id,
                rendered_prompt=winner_osp.render(),
                layer1_fields={f: getattr(winner_osp, f) for f in PROMPT_STRING_FIELDS},
                parent_id=winner_osp.parent_id,
            )

    return round_result


def update_round_state(
    state: LoopState,
    rr: RoundResult,
    round_num: int,
    *,
    schema: PipelineSchema | None = None,
) -> None:
    """Apply round result to loop state (shared by escalation + normal paths)."""
    state.rounds.append(rr)
    # Sync winner prompt fields to OptSearchPoint (source of truth)
    winner_fields = rr.prompt_fields  # dict of prompt fields
    for f in PROMPT_STRING_FIELDS:
        setattr(state.opt_sp, f, winner_fields.get(f, ""))
    # Rebuild JobSearchPoint from opt_sp and update current/best tracking
    assert state.current_sp is not None
    _pp = rr.pipeline_params if rr.pipeline_params is not None else state.current_sp.pipeline_params
    new_sp = state.opt_sp.to_job_search_point(
        base_pipeline_params=_pp,
        schema=schema,
    )
    state.update_current(rr, new_sp, round_num)
