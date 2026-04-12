"""Round execution — generate, evaluate, select winner, update state.

Handles individual round mechanics for the feedback cycle, including
adaptive eval set sampling.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Any

from promptpotter.domain.opt_search_point import OptSearchPoint

# Module-level import for test monkeypatching.
from promptpotter.infrastructure.llm import client as _llm_client
from promptpotter.infrastructure.tracing.observability_logger import observed_node
from promptpotter.services.campaign.config import LoopConfig
from promptpotter.services.campaign.nodes.critique import (
    CritiqueAgent,
    RoundSnapshot,
    format_critique_for_prompt,
    sample_thinking_styles,
    update_query_tracker,
)
from promptpotter.services.campaign.nodes.formatting import (
    build_critique_search_memory_digest,
    candidate_summaries,
)
from promptpotter.services.campaign.state import (
    CampaignPhase,
    LoopState,
    RoundResult,
    RunCallbacks,
    emit_phase,
)
from promptpotter.shared.constants import PROMPT_STRING_FIELDS
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.domain.analysis import QueryDifficulty
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store.campaign_store import CampaignStore
    from promptpotter.infrastructure.tracing.observability_logger import ObsLogger
    from promptpotter.services.campaign.nodes.escalation import DegradationCheck
    from promptpotter.services.campaign.nodes.score import L1ScoringResult

logger = logging.getLogger(__name__)

__all__ = ["PauseForReviewError", "adapt_eval_set", "execute_round", "update_round_state"]


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
    config: LoopConfig,
    campaign_store: CampaignStore | None,
    cycle_id: str | None,
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
        has_scan_brief=config.scan_brief is not None,
        has_critique=bool(state.opt_sp.critique_text),
        pipeline_params=state.current_sp.pipeline_params,
    )

    if campaign_store and cycle_id:
        persisted = campaign_store.load_round_candidates(
            config.backend_id,
            cycle_id,
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

    from promptpotter.services.campaign.nodes.formatting import build_l1_search_memory_digest
    from promptpotter.services.campaign.nodes.generate import l1_generate

    sm_ctx = build_l1_search_memory_digest(search_memory)

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
            scan_brief=config.scan_brief,
            is_probe_round=state.probe_next_round,
            scan_compact=(round_num > 0),
            failure_analysis=state.failure_analysis,
            search_memory_digest=sm_ctx,
            pipeline_schema=config.pipeline_schema,
        )

    if campaign_store and cycle_id:
        campaign_store.save_round_candidates(
            config.backend_id,
            cycle_id,
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
        search_memory_digest=build_critique_search_memory_digest(state.search_memory),
    )
    result = await agent.run(cctx)
    return format_critique_for_prompt(result)


async def _score_and_select(
    candidates: list[dict],
    round_num: int,
    state: LoopState,
    scoring_dataset: list[dict],
    config: LoopConfig,
    callbacks: RunCallbacks,
    obs: ObsLogger | None = None,
    trace_id: str | None = None,
    degradation_checks: list[DegradationCheck] | None = None,
) -> L1ScoringResult:
    """Evaluate candidates, run critique, select winner."""
    from promptpotter.services.campaign.nodes.score import l1_score

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
        assert state.scoring_ctx is not None
        assert state.current_sp is not None
        scoring_result = await l1_score(
            candidates,
            scoring_dataset,
            current_best,
            state.scoring_ctx,
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
    scoring_dataset: list[dict],
    config: LoopConfig,
    obs_campaign_id: str,
    campaign_store: CampaignStore | None,
    cycle_id: str | None,
    callbacks: RunCallbacks,
    degradation_checks: list[DegradationCheck] | None = None,
    search_memory: Any = None,
) -> RoundResult:
    """Execute one optimization round: generate → evaluate → select winner → obs log."""
    obs = state.scoring_ctx.obs if state.scoring_ctx else None
    trace_id = obs.get_file_trace_id(obs_campaign_id) if obs else None
    if obs:
        with graceful("ObsLogger.log_round_start failed"):
            obs.log_round_start(obs_campaign_id, round_num)

    candidates = await _generate_or_load_candidates(
        round_num,
        state,
        config,
        campaign_store,
        cycle_id,
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
        scoring_dataset,
        config,
        callbacks,
        obs=obs,
        trace_id=trace_id,
        degradation_checks=degradation_checks,
    )

    # Update state with critique + thinking styles from eval output
    state.opt_sp.critique_text = scoring_result.critique_text
    state.opt_sp.thinking_styles = scoring_result.thinking_styles

    # Compute failure analysis for next round's L1 context (Wave 1c)
    if scoring_result.winner_results and config.pipeline_schema:
        from promptpotter.services.metrics import compile_failure_analysis

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
        update_query_tracker(state.opt_sp.warning_inventory, _all_results)

    if obs:
        with graceful("ObsLogger.log_round_end failed"):
            obs.log_round_end(
                campaign_id=obs_campaign_id,
                round_num=round_num,
                accuracy=scoring_result.winner_accuracy,
                hits=scoring_result.hits,
                total=scoring_result.total,
                improved=scoring_result.improved,
                winner_prompt_fields_id=scoring_result.winner_prompt_fields.get("id", ""),
                candidate_scores=scoring_result.candidate_scores,
                model=config.model or "",
                n_variants=config.n_variants,
                optimizer_templates=["meta_scan_aware", "critique_negative"],
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


# Never drop more than this fraction of the eval set per adaptation
_MAX_DROP_FRACTION = 0.25


def adapt_eval_set(
    current_dataset: list[dict],
    query_difficulty: QueryDifficulty,
    full_pool: list[dict],
    *,
    seed: int = 42,
) -> tuple[list[dict], dict]:
    """Replace dead queries with discriminating ones from the full pool.

    Args:
        current_dataset: Current evaluation subset.
        query_difficulty: Precomputed difficulty classification.
        full_pool: Full evaluation dataset to draw replacements from.
        seed: Random seed for reproducible sampling.

    Returns:
        Tuple of (new_dataset, summary_dict).
    """
    current_queries = {d["query"] for d in current_dataset}
    pool_by_query = {d["query"]: d for d in full_pool}
    n_original = len(current_dataset)
    max_drop = max(1, int(n_original * _MAX_DROP_FRACTION))

    # Find dead queries in current eval set
    dead_in_current = {p.query for p in query_difficulty.dead if p.query in current_queries}
    to_drop = sorted(dead_in_current)[:max_drop]

    # Find discriminating queries NOT in current eval set
    disc_available = [
        p.query
        for p in query_difficulty.discriminating
        if p.query not in current_queries and p.query in pool_by_query
    ]

    if not to_drop or not disc_available:
        return current_dataset, {"dropped": 0, "added": 0, "unchanged": True}

    rng = random.Random(seed)
    rng.shuffle(disc_available)
    n_swap = min(len(to_drop), len(disc_available))
    replacements = disc_available[:n_swap]

    # Build new eval set — only drop as many as we can replace
    drop_set = set(to_drop[:n_swap])
    new_data = [d for d in current_dataset if d["query"] not in drop_set]
    for q in replacements:
        new_data.append(pool_by_query[q])

    logger.info(
        "Adaptive sampling: dropped %d dead queries, added %d discriminating",
        len(drop_set),
        len(replacements),
    )

    return new_data, {
        "dropped": len(drop_set),
        "added": len(replacements),
        "dropped_queries": list(drop_set),
        "added_queries": replacements,
        "unchanged": False,
    }
