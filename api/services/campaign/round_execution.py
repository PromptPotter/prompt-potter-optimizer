"""Round execution — generate, evaluate, select winner, update state.

Handles individual round mechanics for the feedback cycle. Escalation
logic (L2/L3) lives in ``escalation.py``; campaign lifecycle
(create/resume/finalize) lives in ``campaign_lifecycle.py``.
"""

import logging
from typing import TYPE_CHECKING

from api.models.opt_search_point import OptSearchPoint, PROMPT_STRING_FIELDS
from api.services.campaign.models import (
    CycleCallbacks, CycleConfig, CycleRoundResult, LoopState,
)
from api.services.campaign.critique import (
    CritiqueAgent, CritiqueContext, format_critique_for_prompt,
    sample_thinking_styles, update_query_tracker,
)
from api.services.campaign.helpers import (
    graceful, emit_phase, _candidate_summaries,
)
from api.services.obs.node_tracer import observed_node

# Module-level import for test monkeypatching.
from api.services import llm_client as _llm_client

if TYPE_CHECKING:
    from api.services.campaign.escalation import EscalationCheck
    from api.services.obs.observability_logger import ObsLogger
    from api.services.stores.campaign_store import CampaignStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Round execution helpers
# ---------------------------------------------------------------------------


async def _generate_or_load_candidates(
    round_num: int,
    state: LoopState,
    config: CycleConfig,
    campaign_store: "CampaignStore | None",
    cycle_id: str | None,
    on_phase=None,
    n_eval_queries: int = 0,
    *,
    n_variants: int | None = None,
    creativity: float | None = None,
    obs: "ObsLogger | None" = None,
    trace_id: str | None = None,
) -> list[dict]:
    """Load persisted candidates or generate fresh ones via LLM."""
    _n_variants = n_variants if n_variants is not None else config.n_variants
    _creativity = creativity if creativity is not None else config.creativity
    prompt_preview = state.opt_sp.render()[:120]

    emit_phase(on_phase, "l1_generate", "enter", round=round_num,
                current_accuracy=state.current_accuracy,
                prompt_preview=prompt_preview,
                n_variants=_n_variants,
                creativity=_creativity,
                model=config.model or "(default)",
                has_scan_context=config.scan_context is not None,
                has_critique=bool(state.opt_sp.critique_text),
                pipeline_params=state.current_sp.pipeline_params,
                temperature=state.current_sp.temperature)

    if campaign_store and cycle_id:
        persisted = campaign_store.load_round_candidates(
            config.backend_id, cycle_id, round_num,
        )
        if persisted is not None:
            logger.info(
                "Loaded %d persisted candidates for round %d",
                len(persisted), round_num,
            )
            emit_phase(on_phase, "l1_generate", "exit", round=round_num,
                        n_candidates=len(persisted),
                        n_eval_queries=n_eval_queries,
                        loaded_from_disk=True,
                        candidates=_candidate_summaries(
                            persisted, state.opt_sp.prompt_field_dict()))
            return persisted

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    from api.services.l1_optimizer import l1_generate

    client = _llm_client.get_llm_client(config.provider)
    async with observed_node(f"l1_generate_r{round_num}", "llm/meta",
                             obs=obs, trace_id=trace_id):
        candidates = await l1_generate(
            state.opt_sp, state.current_accuracy, state.current_results,
            _n_variants, _creativity, client,
            model=config.model,
            scan_context=config.scan_context,
            is_probe_round=state.probe_next_round,
            max_failures=config.max_failures,
        )

    if campaign_store and cycle_id:
        campaign_store.save_round_candidates(
            config.backend_id, cycle_id, round_num, candidates,
        )

    emit_phase(on_phase, "l1_generate", "exit", round=round_num,
                n_candidates=len(candidates),
                n_eval_queries=n_eval_queries,
                loaded_from_disk=False,
                candidates=_candidate_summaries(
                    candidates, state.opt_sp.prompt_field_dict()))

    return candidates


async def _evaluate_candidates(
    candidates: list[dict],
    round_num: int,
    state: LoopState,
    round_eval_data: list[dict],
    config: CycleConfig,
    callbacks: CycleCallbacks,
    obs: "ObsLogger | None" = None,
    trace_id: str | None = None,
    escalation_checks: "list[EscalationCheck] | None" = None,
) -> dict:
    """Evaluate candidates and run critique analysis."""
    emit_phase(callbacks.on_phase, "l1_evaluate", "enter", round=round_num,
                n_candidates=len(candidates),
                n_queries=len(round_eval_data),
                current_best_accuracy=state.current_accuracy,
                improvement_threshold=config.improvement_threshold,
                current_pipeline_params=state.current_sp.pipeline_params)

    baseline_label = f"round_{round_num}" if round_num > 0 else "baseline"
    current_best = {
        "accuracy": state.current_accuracy,
        "composite": state.current_composite,
        "prompt_fields": state.opt_sp.prompt_field_dict(),
        "results": state.current_results,
        "label": baseline_label,
    }

    from api.services.l1_optimizer import l1_evaluate

    async with observed_node(f"l1_evaluate_r{round_num}", "evaluation",
                             obs=obs, trace_id=trace_id, obs_type="span"):
        eval_out = await l1_evaluate(
            candidates, round_eval_data, current_best, state.eval_ctx,
            model=state.current_sp.model,
            temperature=state.current_sp.temperature,
            pipeline_params=state.current_sp.pipeline_params,
            improvement_threshold=config.improvement_threshold,
            callbacks=callbacks,
            escalation_checks=escalation_checks,
        )

        # Critique analysis
        critique_result: dict = {}
        critique_text = ""
        if config.enable_critique and eval_out.get("winner_results"):
            crit_llm = _llm_client.get_llm_client(config.provider)
            agent = CritiqueAgent(crit_llm, model=config.model)
            cctx = CritiqueContext(
                results=eval_out["winner_results"],
                accuracy=eval_out["winner_accuracy"],
                composite=eval_out.get("winner_composite", eval_out["winner_accuracy"]),
                degraded_queries=eval_out.get("degraded_queries", 0),
                round_history=[
                    {"round": r.round, "accuracy": r.accuracy,
                     "composite": r.composite,
                     "pipeline_params": r.pipeline_params,
                     "degraded": getattr(r, "degraded_queries", 0),
                     "n_candidates": len(r.candidate_scores)}
                    for r in state.rounds
                ],
                current_round=round_num,
                stall_count=state.stall_count,
                best_accuracy=state.best_accuracy,
                best_round=state.best_round,
                scan_context=config.scan_context,
                pipeline_params=(
                    state.current_sp.pipeline_params if state.current_sp else None
                ),
                warning_inventory=state.opt_sp.warning_inventory or None,
                task_context=state.opt_sp.task_context or None,
            )
            critique_result = await agent.run(cctx)
            critique_text = format_critique_for_prompt(critique_result)

        thinking_styles = sample_thinking_styles(
            n=3, seed=config.seed + round_num + 1,
        )
        eval_out["critique_text"] = critique_text
        eval_out["critique"] = critique_result
        eval_out["thinking_styles"] = thinking_styles

    emit_phase(callbacks.on_phase, "l1_evaluate", "exit", round=round_num,
                winner_label=eval_out["winner"].get("label", ""),
                winner_accuracy=eval_out["winner_accuracy"],
                winner_composite=eval_out.get("winner_composite",
                                              eval_out["winner_accuracy"]),
                improved=eval_out["improved"],
                next_action=eval_out["next_action"],
                candidate_scores=eval_out["candidate_scores"],
                critique_text=eval_out.get("critique_text", ""))

    return eval_out


def _log_round_obs(
    eval_out: dict,
    round_num: int,
    config: CycleConfig,
    obs: "ObsLogger",
    obs_campaign_id: str,
) -> None:
    """Log round-end metrics and prompt version to observability."""
    with graceful("ObsLogger.log_round_end failed"):
        obs.log_round_end(
            campaign_id=obs_campaign_id,
            round_num=round_num,
            accuracy=eval_out["winner_accuracy"],
            hits=eval_out["winner"].get("hits", 0),
            total=eval_out["winner"].get("total", 0),
            improved=eval_out["improved"],
            next_action=eval_out["next_action"],
            winner_prompt_fields_id=eval_out["winner_prompt_fields"].get("id", ""),
            candidate_scores=eval_out["candidate_scores"],
            model=config.model or "",
            temperature=config.temperature,
            n_variants=config.n_variants,
            optimizer_templates=[
                "meta_scan_aware",
                "critique_negative",
            ],
        )

    with graceful("ObsLogger.log_prompt_version failed"):
        winner_fields = eval_out["winner_prompt_fields"]
        winner_osp = OptSearchPoint.from_prompt_fields(winner_fields)
        obs.log_prompt_version(
            prompt_fields_id=winner_osp.id,
            rendered_prompt=winner_osp.render(),
            layer1_fields={
                f: getattr(winner_osp, f)
                for f in PROMPT_STRING_FIELDS
            },
            parent_id=winner_osp.parent_id,
        )


async def _execute_round(
    round_num: int,
    state: LoopState,
    round_eval_data: list[dict],
    config: CycleConfig,
    obs_campaign_id: str,
    campaign_store: "CampaignStore | None",
    cycle_id: str | None,
    callbacks: CycleCallbacks,
    escalation_checks: "list[EscalationCheck] | None" = None,
) -> CycleRoundResult:
    """Execute one optimization round: generate → evaluate → select winner → obs log."""
    obs = state.eval_ctx.obs if state.eval_ctx else None
    trace_id = obs.get_file_trace_id(obs_campaign_id) if obs else None
    if obs:
        with graceful("ObsLogger.log_round_start failed"):
            obs.log_round_start(obs_campaign_id, round_num)

    # Resolve L2 meta-param overrides from OptSearchPoint.optimizer_params
    opt_params = state.opt_sp.optimizer_params
    n_variants = opt_params.get("n_variants", config.n_variants)
    creativity = opt_params.get("creativity", config.creativity)

    candidates = await _generate_or_load_candidates(
        round_num, state, config, campaign_store, cycle_id, callbacks.on_phase,
        n_eval_queries=len(round_eval_data),
        n_variants=n_variants, creativity=creativity,
        obs=obs, trace_id=trace_id,
    )

    eval_out = await _evaluate_candidates(
        candidates, round_num, state, round_eval_data, config,
        callbacks,
        obs=obs, trace_id=trace_id,
        escalation_checks=escalation_checks,
    )

    # Update state with critique + thinking styles from eval output
    state.opt_sp.critique_text = eval_out.pop("critique_text", "")
    state.opt_sp.critique = eval_out.pop(
        "critique", {"summary": state.opt_sp.critique_text},
    )
    state.opt_sp.thinking_styles = eval_out.pop("thinking_styles", [])

    round_result = CycleRoundResult(
        round=round_num,
        label=eval_out["winner"]["label"],
        accuracy=eval_out["winner_accuracy"],
        composite=eval_out["winner_composite"],
        hits=eval_out["winner"]["hits"],
        total=eval_out["winner"]["total"],
        improved=eval_out["improved"],
        next_action=eval_out["next_action"],
        prompt_fields=eval_out["winner_prompt_fields"],
        pipeline_params=eval_out.get("winner_pipeline_params"),
        results=eval_out["winner_results"],
        candidates_evaluated=eval_out["winner"]["candidates_evaluated"],
        candidate_scores=eval_out["candidate_scores"],
        degraded_queries=eval_out.get("degraded_queries", 0),
        escalation_signal=eval_out.get("escalation_signal"),
    )

    # Update per-query warning inventory from ALL candidate results
    # (not just winner — aborted candidates carry the pipeline warnings)
    _all_results = eval_out.get("all_eval_results", [])
    if _all_results:
        update_query_tracker(state.opt_sp.warning_inventory, _all_results)

    if obs:
        _log_round_obs(eval_out, round_num, config, obs, obs_campaign_id)

    return round_result


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def _update_round_state(
    state: LoopState, rr: CycleRoundResult, round_num: int,
) -> None:
    """Apply round result to loop state (shared by escalation + normal paths)."""
    state.rounds.append(rr)
    # Sync winner prompt fields to OptSearchPoint (source of truth)
    winner_fields = rr.prompt_fields  # dict of prompt fields
    for f in PROMPT_STRING_FIELDS:
        setattr(state.opt_sp, f, winner_fields.get(f, ""))
    # Rebuild JobSearchPoint from opt_sp
    _pp = rr.pipeline_params if rr.pipeline_params is not None else state.current_sp.pipeline_params
    state.current_sp = state.opt_sp.to_job_search_point(
        model=state.current_sp.model,
        temperature=state.current_sp.temperature,
        base_pipeline_params=_pp,
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
