"""Round execution — generate, evaluate, select winner, update state.

Handles individual round mechanics for the feedback cycle. Escalation
logic (L2/L3) lives in ``escalation.py``; campaign lifecycle
(create/resume/finalize) lives in ``campaign_lifecycle.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from api.models.opt_search_point import OptSearchPoint

# Module-level import for test monkeypatching.
from api.services import llm_client as _llm_client
from api.services.campaign.callbacks import CycleCallbacks
from api.services.campaign.config import CycleConfig
from api.services.campaign.critique import (
    CritiqueAgent,
    CritiqueContext,
    format_critique_for_prompt,
    sample_thinking_styles,
    update_query_tracker,
)
from api.services.campaign.helpers import (
    candidate_summaries,
    emit_phase,
    graceful,
)
from api.services.campaign.results import CycleRoundResult
from api.services.campaign.state import LoopState
from api.services.obs.node_tracer import observed_node
from api.shared.constants import PROMPT_STRING_FIELDS

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema
    from api.services.campaign.escalation import DegradationCheck
    from api.services.campaign.l1_optimizer import L1EvalResult
    from api.services.obs.observability_logger import ObsLogger
    from api.services.stores.campaign_store import CampaignStore

logger = logging.getLogger(__name__)


class PauseForReviewError(Exception):
    """Raised when HITL mode pauses the loop for human/AI review."""

    def __init__(
        self,
        candidates: list[dict],
        round_num: int,
        pause_point: str = "l1_generate",
    ) -> None:
        self.candidates = candidates
        self.round_num = round_num
        self.pause_point = pause_point  # "l1_generate", "before_l2_eval", "user_pause"
        super().__init__(f"Paused at {pause_point}: {len(candidates)} candidates (round {round_num})")


def _candidate_keys_from_schema(schema: PipelineSchema | None) -> list[str]:
    """Derive pipeline_data candidate keys from schema's ranker/candidate_source nodes."""
    if not schema:
        return []
    keys: list[str] = []
    for node in schema.nodes:
        if node.node_type in ("ranker", "candidate_source"):
            keys.extend(node.output_keys)
    return keys


# ---------------------------------------------------------------------------
# Round execution helpers
# ---------------------------------------------------------------------------


async def _generate_or_load_candidates(
    round_num: int,
    state: LoopState,
    config: CycleConfig,
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
    opt_params = state.opt_sp.optimizer_params
    _n_variants = opt_params.get("n_variants", config.n_variants)
    _creativity = opt_params.get("creativity", config.creativity)
    prompt_preview = state.opt_sp.render()[:120]

    assert state.current_sp is not None
    emit_phase(
        on_phase,
        "l1_generate",
        "enter",
        round=round_num,
        current_accuracy=state.current_accuracy,
        prompt_preview=prompt_preview,
        n_variants=_n_variants,
        creativity=_creativity,
        model=config.model or "(default)",
        has_scan_context=config.scan_context is not None,
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
                "l1_generate",
                "exit",
                round=round_num,
                n_candidates=len(persisted),
                n_eval_queries=n_eval_queries,
                loaded_from_disk=True,
                candidates=candidate_summaries(persisted),
            )
            return persisted

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    from api.services.campaign.formatting import build_l1_search_memory_context
    from api.services.campaign.l1_optimizer import l1_generate

    sm_ctx = build_l1_search_memory_context(search_memory)

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
            scan_context=config.scan_context,
            is_probe_round=state.probe_next_round,
            scan_compact=(round_num > 0),
            failure_analysis=state.failure_analysis,
            search_memory_context=sm_ctx,
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
        "l1_generate",
        "exit",
        round=round_num,
        n_candidates=len(candidates),
        n_eval_queries=n_eval_queries,
        loaded_from_disk=False,
        candidates=candidate_summaries(candidates),
    )

    return candidates


async def _evaluate_candidates(
    candidates: list[dict],
    round_num: int,
    state: LoopState,
    round_eval_data: list[dict],
    config: CycleConfig,
    callbacks: CycleCallbacks,
    obs: ObsLogger | None = None,
    trace_id: str | None = None,
    escalation_checks: list[DegradationCheck] | None = None,
) -> L1EvalResult:
    """Evaluate candidates and run critique analysis."""
    from api.services.campaign.l1_optimizer import l1_evaluate

    emit_phase(
        callbacks.on_phase,
        "l1_evaluate",
        "enter",
        round=round_num,
        n_candidates=len(candidates),
        n_queries=len(round_eval_data),
        current_best_accuracy=state.current_accuracy,
        improvement_threshold=config.improvement_threshold,
        current_pipeline_params=state.current_sp.pipeline_params if state.current_sp else None,
    )

    baseline_label = f"round_{round_num}" if round_num > 0 else "baseline"
    current_best = {
        "accuracy": state.current_accuracy,
        "composite": state.current_composite,
        "prompt_fields": state.opt_sp.prompt_field_dict(),
        "results": state.current_results,
        "label": baseline_label,
    }

    async with observed_node(
        f"l1_evaluate_r{round_num}", "evaluation", obs=obs, trace_id=trace_id, obs_type="span"
    ):
        assert state.eval_ctx is not None
        assert state.current_sp is not None
        eval_out = await l1_evaluate(
            candidates,
            round_eval_data,
            current_best,
            state.eval_ctx,
            pipeline_params=state.current_sp.pipeline_params,
            active_steps=config.active_steps,
            improvement_threshold=config.improvement_threshold,
            callbacks=callbacks,
            escalation_checks=escalation_checks,
        )

        # Critique analysis
        critique_result: dict = {}
        critique_text = ""
        if config.enable_critique and eval_out.winner_results:
            crit_llm = _llm_client.get_llm_client()
            agent = CritiqueAgent(crit_llm, model=config.model)
            # Build critique-specific SearchMemory context
            _crit_sm_ctx = None
            _sm = state.search_memory
            if _sm:
                _crit_sm_ctx = {}
                _disc = _sm.discriminating_queries()
                if _disc:
                    _crit_sm_ctx["discriminating_queries"] = (
                        f"{len(_disc)} queries vary across configs"
                    )
                _fc = _sm.failure_clusters(3)
                if _fc:
                    _crit_sm_ctx["failure_clusters"] = "; ".join(
                        f"{c.failure_mode} ({c.fraction:.0%})" for c in _fc
                    )
                if not _crit_sm_ctx:
                    _crit_sm_ctx = None
            cctx = CritiqueContext(
                results=eval_out.winner_results,
                accuracy=eval_out.winner_accuracy,
                composite=eval_out.winner_composite,
                degraded_queries=eval_out.degraded_queries,
                round_history=[
                    {
                        "round": r.round,
                        "accuracy": r.accuracy,
                        "composite": r.composite,
                        "pipeline_params": r.pipeline_params,
                        "degraded": getattr(r, "degraded_queries", 0),
                        "n_candidates": len(r.candidate_scores),
                    }
                    for r in state.rounds
                ],
                current_round=round_num,
                stall_count=state.stall_count,
                best_accuracy=state.best_accuracy,
                best_round=state.best_round,
                pipeline_params=(state.current_sp.pipeline_params if state.current_sp else None),
                candidate_keys=_candidate_keys_from_schema(config.pipeline_schema),
                pipeline_schema=config.pipeline_schema,
                degradation_threshold=config.critique_degradation_threshold,
                near_miss_ratio=config.critique_near_miss_ratio,
                search_memory_context=_crit_sm_ctx,
            )
            critique_result = await agent.run(cctx)
            critique_text = format_critique_for_prompt(critique_result)

        thinking_styles = sample_thinking_styles(
            n=3,
            seed=config.seed + round_num + 1,
        )
        eval_out.critique_text = critique_text
        eval_out.thinking_styles = thinking_styles

    emit_phase(
        callbacks.on_phase,
        "l1_evaluate",
        "exit",
        round=round_num,
        winner_label=eval_out.label,
        winner_accuracy=eval_out.winner_accuracy,
        winner_composite=eval_out.winner_composite,
        improved=eval_out.improved,
        candidate_scores=eval_out.candidate_scores,
        critique_text=eval_out.critique_text,
    )

    return eval_out


async def execute_round(
    round_num: int,
    state: LoopState,
    round_eval_data: list[dict],
    config: CycleConfig,
    obs_campaign_id: str,
    campaign_store: CampaignStore | None,
    cycle_id: str | None,
    callbacks: CycleCallbacks,
    escalation_checks: list[DegradationCheck] | None = None,
    search_memory: Any = None,
) -> CycleRoundResult:
    """Execute one optimization round: generate → evaluate → select winner → obs log."""
    obs = state.eval_ctx.obs if state.eval_ctx else None
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
        n_eval_queries=len(round_eval_data),
        obs=obs,
        trace_id=trace_id,
        search_memory=search_memory,
    )

    if config.pause_before_eval:
        raise PauseForReviewError(candidates, round_num)

    eval_out = await _evaluate_candidates(
        candidates,
        round_num,
        state,
        round_eval_data,
        config,
        callbacks,
        obs=obs,
        trace_id=trace_id,
        escalation_checks=escalation_checks,
    )

    # Update state with critique + thinking styles from eval output
    state.opt_sp.critique_text = eval_out.critique_text
    state.opt_sp.thinking_styles = eval_out.thinking_styles

    # Compute failure analysis for next round's L1 context (Wave 1c)
    if eval_out.winner_results and config.pipeline_schema:
        from api.services.metrics import compile_failure_analysis

        state.failure_analysis = compile_failure_analysis(
            eval_out.winner_results, config.pipeline_schema,
        )
    else:
        state.failure_analysis = None

    round_result = CycleRoundResult(
        round=round_num,
        label=eval_out.label,
        accuracy=eval_out.winner_accuracy,
        composite=eval_out.winner_composite,
        hits=eval_out.hits,
        total=eval_out.total,
        improved=eval_out.improved,
        prompt_fields=eval_out.winner_prompt_fields,
        pipeline_params=eval_out.winner_pipeline_params,
        results=eval_out.winner_results,
        candidates_evaluated=eval_out.candidates_evaluated,
        candidate_scores=eval_out.candidate_scores,
        degraded_queries=eval_out.degraded_queries,
        escalation_signal=eval_out.escalation_signal,
    )

    # Update per-query warning inventory from ALL candidate results
    # (not just winner — aborted candidates carry the pipeline warnings)
    _all_results = eval_out.all_eval_results
    if _all_results:
        update_query_tracker(state.opt_sp.warning_inventory, _all_results)

    if obs:
        with graceful("ObsLogger.log_round_end failed"):
            obs.log_round_end(
                campaign_id=obs_campaign_id,
                round_num=round_num,
                accuracy=eval_out.winner_accuracy,
                hits=eval_out.hits,
                total=eval_out.total,
                improved=eval_out.improved,
                winner_prompt_fields_id=eval_out.winner_prompt_fields.get("id", ""),
                candidate_scores=eval_out.candidate_scores,
                model=config.model or "",
                n_variants=config.n_variants,
                optimizer_templates=["meta_scan_aware", "critique_negative"],
            )
        with graceful("ObsLogger.log_prompt_version failed"):
            winner_fields = eval_out.winner_prompt_fields
            winner_osp = OptSearchPoint.from_prompt_fields(winner_fields)
            obs.log_prompt_version(
                prompt_fields_id=winner_osp.id,
                rendered_prompt=winner_osp.render(),
                layer1_fields={f: getattr(winner_osp, f) for f in PROMPT_STRING_FIELDS},
                parent_id=winner_osp.parent_id,
            )

    return round_result


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def update_round_state(
    state: LoopState,
    rr: CycleRoundResult,
    round_num: int,
    *,
    active_steps: tuple[str, ...] = (),
    prompt_node: str = "",
) -> None:
    """Apply round result to loop state (shared by escalation + normal paths)."""
    state.rounds.append(rr)
    # Sync winner prompt fields to OptSearchPoint (source of truth)
    winner_fields = rr.prompt_fields  # dict of prompt fields
    for f in PROMPT_STRING_FIELDS:
        setattr(state.opt_sp, f, winner_fields.get(f, ""))
    # Rebuild JobSearchPoint from opt_sp
    assert state.current_sp is not None
    _pp = rr.pipeline_params if rr.pipeline_params is not None else state.current_sp.pipeline_params
    state.current_sp = state.opt_sp.to_job_search_point(
        base_pipeline_params=_pp,
        active_steps=active_steps,
        prompt_node=prompt_node,
    )
    state.current_accuracy = rr.accuracy
    state.current_composite = rr.composite
    state.current_results = list(rr.results)
    if state.current_composite > state.best_composite:
        state.best_composite = state.current_composite
        state.best_accuracy = state.current_accuracy
        state.best_round = round_num
        state.best_sp = state.current_sp


# ---------------------------------------------------------------------------
# Layer escalation helpers
# ---------------------------------------------------------------------------
