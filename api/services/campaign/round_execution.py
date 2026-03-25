"""Round execution, escalation, and campaign finalization.

Handles individual round mechanics (generate → evaluate → state update),
L2/L3 layer transitions, escalation logic, and campaign persistence.
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from api.models.phase_event import PhaseEvent
from api.models.opt_search_point import OptSearchPoint, PROMPT_STRING_FIELDS
from api.services.campaign import layer_transitions
from api.services.campaign.models import (
    CycleConfig, CycleRoundResult, StopReason, _LoopState,
)
from api.services.campaign.critique import (
    CritiqueAgent, format_critique_for_prompt, sample_thinking_styles,
)
from api.services.campaign.critique_stats import (
    CritiqueContext, update_query_tracker, warning_summary,
)
from api.services.campaign.cycle_setup import (
    graceful, _emit_phase, _candidate_summaries,
)
from api.services.obs.step_tracer import observed_step

# Module-level import for test monkeypatching.
from api.services import llm_client as _llm_client

if TYPE_CHECKING:
    from api.services.obs.observability_logger import ObsLogger
    from api.services.stores.campaign_store import CampaignStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Round execution helpers
# ---------------------------------------------------------------------------


async def _generate_or_load_candidates(
    round_num: int,
    state: _LoopState,
    config: CycleConfig,
    campaign_store: "CampaignStore | None",
    cycle_id: str | None,
    on_phase: Callable[[PhaseEvent], None] | None = None,
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

    _emit_phase(on_phase, "l1_generate", "enter", round=round_num,
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
            _emit_phase(on_phase, "l1_generate", "exit", round=round_num,
                        n_candidates=len(persisted),
                        n_eval_queries=n_eval_queries,
                        loaded_from_disk=True,
                        candidates=_candidate_summaries(
                            persisted, state.opt_sp.prompt_field_dict()))
            return persisted

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    from api.services.l1_optimizer import l1_generate

    client = _llm_client.get_llm_client(config.provider)
    async with observed_step(f"l1_generate_r{round_num}", "llm/meta",
                             obs=obs, trace_id=trace_id):
        candidates = await l1_generate(
            state.opt_sp, state.current_accuracy, state.current_results,
            _n_variants, _creativity, client,
            model=config.model,
            scan_context=config.scan_context,
            is_probe_round=state.probe_next_round,
        )

    if campaign_store and cycle_id:
        campaign_store.save_round_candidates(
            config.backend_id, cycle_id, round_num, candidates,
        )

    _emit_phase(on_phase, "l1_generate", "exit", round=round_num,
                n_candidates=len(candidates),
                n_eval_queries=n_eval_queries,
                loaded_from_disk=False,
                candidates=_candidate_summaries(
                    candidates, state.opt_sp.prompt_field_dict()))

    return candidates


async def _evaluate_candidates(
    candidates: list[dict],
    round_num: int,
    state: _LoopState,
    round_eval_data: list[dict],
    config: CycleConfig,
    on_candidate_eval: Callable[[int, int, dict], None] | None,
    on_query_eval: Callable[[int, int, int, int, dict], None] | None,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: "ObsLogger | None" = None,
    trace_id: str | None = None,
    escalation_checks: list | None = None,
) -> dict:
    """Evaluate candidates and run critique analysis."""
    _emit_phase(on_phase, "l1_evaluate", "enter", round=round_num,
                n_candidates=len(candidates),
                n_queries=len(round_eval_data),
                current_best_accuracy=state.current_accuracy,
                improvement_threshold=config.improvement_threshold,
                current_pipeline_params=state.current_sp.pipeline_params)

    baseline_label = f"round_{round_num}" if round_num > 0 else "baseline"
    current_best = {
        "accuracy": state.current_accuracy,
        "composite": state.current_composite,
        "prompt_state": state.opt_sp.prompt_field_dict(),
        "results": state.current_results,
        "label": baseline_label,
    }

    from api.services.l1_optimizer import l1_evaluate

    async with observed_step(f"l1_evaluate_r{round_num}", "evaluation",
                             obs=obs, trace_id=trace_id, obs_type="span"):
        eval_out = await l1_evaluate(
            candidates, round_eval_data, current_best, state.eval_ctx,
            improvement_threshold=config.improvement_threshold,
            on_candidate_eval=on_candidate_eval,
            on_query_eval=on_query_eval,
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

    _emit_phase(on_phase, "l1_evaluate", "exit", round=round_num,
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
            winner_prompt_state_id=eval_out["winner_prompt_state"].get("id", ""),
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
        winner_fields = eval_out["winner_prompt_state"]
        winner_osp = OptSearchPoint.from_prompt_fields(winner_fields)
        obs.log_prompt_version(
            prompt_state_id=winner_osp.id,
            rendered_prompt=winner_osp.render(),
            layer1_fields={
                f: getattr(winner_osp, f)
                for f in PROMPT_STRING_FIELDS
            },
            parent_id=winner_osp.parent_id,
        )


async def _execute_round(
    round_num: int,
    state: _LoopState,
    round_eval_data: list[dict],
    config: CycleConfig,
    obs_campaign_id: str,
    campaign_store: "CampaignStore | None",
    cycle_id: str | None,
    on_candidate_eval: Callable[[int, int, dict], None] | None,
    on_query_eval: Callable[[int, int, int, int, dict], None] | None,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    escalation_checks: list | None = None,
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
        round_num, state, config, campaign_store, cycle_id, on_phase,
        n_eval_queries=len(round_eval_data),
        n_variants=n_variants, creativity=creativity,
        obs=obs, trace_id=trace_id,
    )

    eval_out = await _evaluate_candidates(
        candidates, round_num, state, round_eval_data, config,
        on_candidate_eval, on_query_eval, on_phase,
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
        label=eval_out["winner"].get("label", f"round_{round_num}"),
        accuracy=eval_out["winner_accuracy"],
        composite=eval_out.get("winner_composite", eval_out["winner_accuracy"]),
        hits=eval_out["winner"].get("hits", 0),
        total=eval_out["winner"].get("total", 0),
        improved=eval_out["improved"],
        next_action=eval_out["next_action"],
        prompt_state=eval_out["winner_prompt_state"],
        pipeline_params=eval_out.get("winner_pipeline_params"),
        results=eval_out["winner_results"],
        candidates_evaluated=eval_out["winner"].get("candidates_evaluated", 0),
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


def _finalize_campaign(
    campaign_store: "CampaignStore | None",
    cycle_id: str | None,
    config: CycleConfig,
    state: _LoopState,
    stop_reason: str,
    finished_at: str,
    obs: "ObsLogger | None",
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
            campaign_store.update(config.backend_id, cycle_id, {
                "status": status,
                "stop_reason": stop_reason,
                "best_accuracy": state.best_accuracy,
                "best_round": state.best_round,
                "n_rounds": len(state.rounds),
                "finished_at": finished_at,
            })

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


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def _update_round_state(
    state: _LoopState, rr: CycleRoundResult, round_num: int,
) -> None:
    """Apply round result to loop state (shared by escalation + normal paths)."""
    state.rounds.append(rr)
    # Sync winner prompt fields to OptSearchPoint (source of truth)
    winner_fields = rr.prompt_state  # dict of prompt fields
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


def _maybe_emit_backend_warning(
    state: _LoopState,
    config: CycleConfig,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
) -> None:
    """Emit a one-shot backend warning after repeated degradation resets."""
    opt = state.opt_sp
    if opt.backend_warning_emitted or config.backend_warning_threshold <= 0:
        return
    if opt.degradation_reset_count < config.backend_warning_threshold:
        return

    opt.backend_warning_emitted = True
    count = opt.degradation_reset_count

    steps: set[str] = set()
    wtypes: dict[str, int] = {}
    for e in opt.escalation_journal:
        if e.get("problem_step"):
            steps.add(e["problem_step"])
        for wt, n in e.get("warning_types", {}).items():
            wtypes[wt] = wtypes.get(wt, 0) + n

    _emit_phase(
        on_phase, "backend_warning", "notify", round=round_num,
        message=(
            f"Repeated pipeline degradation \u2014 {count} investigation "
            "cycles exhausted. Likely a backend server issue."
        ),
        advice=(
            "Paste warnings + connector code into Claude Code "
            "\u2192 docs/connectors"
        ),
        degradation_reset_count=count,
        problem_steps=sorted(steps),
        persistent_warning_types=wtypes,
    )
    logger.warning(
        "Backend warning at round %d (%d resets, steps: %s)",
        round_num, count, sorted(steps),
    )


def _degradation_reset(
    state: _LoopState,
    config: CycleConfig,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    reset_l3: bool = False,
) -> None:
    """Reset L2 (and optionally L3) counters during degradation investigation."""
    state.l2_stall_count = 0
    state.l2_round = 0
    if reset_l3:
        state.l3_stall_count = 0
        state.l3_round = 0
    state.opt_sp.degradation_reset_count += 1
    _maybe_emit_backend_warning(state, config, round_num, on_phase)


async def _do_l2_transition(
    state: _LoopState,
    config: CycleConfig,
    round_num: int,
    eval_data: list[dict],
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: "ObsLogger | None" = None,
    trace_id: str | None = None,
    escalation_context: dict | None = None,
) -> layer_transitions.TransitionResult:
    """Perform L2 refine_context transition. Updates state in-place."""
    current_pp = state.current_sp.pipeline_params

    stalled_rounds = [
        {
            "round": r.round,
            "accuracy": r.accuracy,
            "results": r.results,
        }
        for r in state.rounds[-config.patience:]
    ]
    _emit_phase(on_phase, "refine_context", "enter", round=round_num,
                l2_round=state.l2_round,
                stall_count=state.stall_count,
                current_params=state.opt_sp.optimizer_params,
                current_accuracy=state.current_accuracy,
                best_accuracy=state.best_accuracy)

    client = _llm_client.get_llm_client(config.provider)
    async with observed_step(f"l2_refine_r{round_num}", "llm/meta",
                             obs=obs, trace_id=trace_id):
        tr = await layer_transitions.refine_context(
            state.opt_sp, stalled_rounds, eval_data, client,
            model=config.model,
            temperature=config.l2_temperature,
            pipeline_params=current_pp,
            pipeline_schema=config.pipeline_schema,
            escalation_context=escalation_context,
        )
    # Update task_context if L2 refined it
    if tr.task_context:
        state.opt_sp.task_context = tr.task_context
    # Store L2 directive for next L1 round (sliding window=1)
    state.opt_sp.l2_directive = tr.l2_directive
    # L2 does NOT set pipeline_params — only L1 Generate does that
    # Update opt_sp from L2 result, then rebuild JobSearchPoint
    state.opt_sp = tr.opt_search_point
    state.current_sp = state.opt_sp.to_job_search_point(
        model=state.current_sp.model,
        temperature=state.current_sp.temperature,
        base_pipeline_params=state.current_sp.pipeline_params,
    )
    state.l2_round += 1
    state.best_accuracy_at_l2_entry = state.best_accuracy
    state.best_composite_at_l2_entry = state.best_composite
    # Build warning inventory one-liner for display
    _warned_count, _top_warning = warning_summary(state.opt_sp.warning_inventory)

    _emit_phase(on_phase, "refine_context", "exit", round=round_num,
                l2_round=state.l2_round,
                param_changes_count=len(tr.opt_search_point.optimizer_params),
                task_context_changed=tr.task_context is not None,
                changes_description=tr.opt_search_point.changes_description or "",
                pipeline_params_changed=tr.pipeline_params is not None,
                pipeline_params=tr.pipeline_params,
                action=tr.action,
                warned_queries=_warned_count,
                top_warning=_top_warning,
                l2_prompt=tr.debug_prompt,
                l2_response=tr.debug_response)
    # Flag next round as probe if L2 requested it
    if tr.action == "probe":
        state.probe_next_round = True
        logger.info("L2 requested probe — next round uses warned queries")

    logger.info(
        "L2 refine_context at round %d (l2_round=%d)",
        round_num, state.l2_round,
    )
    return tr


async def _do_l3_transition(
    state: _LoopState,
    config: CycleConfig,
    round_num: int,
    eval_data: list[dict],
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: "ObsLogger | None" = None,
    trace_id: str | None = None,
) -> layer_transitions.TransitionResult:
    """Perform L3 modify_plan transition. Updates state in-place."""
    current_pp = state.current_sp.pipeline_params

    l2_history = [{
        "l2_round": state.l2_round,
        "optimizer_params": state.opt_sp.optimizer_params,
        "accuracy_change": state.best_composite - state.best_composite_at_l3_entry,
    }]
    _emit_phase(on_phase, "modify_plan", "enter", round=round_num,
                l3_round=state.l3_round,
                l2_stall_count=state.l2_stall_count,
                current_plan_preview=str(state.opt_sp.plan)[:120])

    client = _llm_client.get_llm_client(config.provider)
    async with observed_step(f"l3_modify_plan_r{round_num}", "llm/meta",
                             obs=obs, trace_id=trace_id):
        tr = await layer_transitions.modify_plan(
            state.opt_sp, l2_history, eval_data, client,
            model=config.model,
            temperature=config.l3_temperature,
            pipeline_params=current_pp,
            pipeline_schema=config.pipeline_schema,
        )
    # Update opt_sp from L3 result, then rebuild JobSearchPoint
    state.opt_sp = tr.opt_search_point
    _pp = tr.pipeline_params or state.current_sp.pipeline_params
    state.current_sp = state.opt_sp.to_job_search_point(
        model=state.current_sp.model,
        temperature=state.current_sp.temperature,
        base_pipeline_params=_pp,
    )
    state.l3_round += 1
    state.best_accuracy_at_l3_entry = state.best_accuracy
    state.best_composite_at_l3_entry = state.best_composite
    state.l2_stall_count = 0
    state.l2_round = 0
    state.best_accuracy_at_l2_entry = state.best_accuracy
    state.best_composite_at_l2_entry = state.best_composite
    _emit_phase(on_phase, "modify_plan", "exit", round=round_num,
                l3_round=state.l3_round,
                new_plan_preview=str(tr.opt_search_point.plan)[:120],
                changes_description=tr.opt_search_point.changes_description or "",
                pipeline_params_changed=tr.pipeline_params is not None)
    logger.info(
        "L3 modify_plan at round %d (l3_round=%d)",
        round_num, state.l3_round,
    )
    return tr


async def _escalate_l2(
    state: _LoopState,
    config: CycleConfig,
    round_num: int,
    eval_data: list[dict],
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: "ObsLogger | None" = None,
    trace_id: str | None = None,
    from_degradation: bool = False,
    escalation_context: dict | None = None,
) -> str | None:
    """Handle L1→L2 escalation and optionally L2→L3.

    Returns a stop_reason string if the cycle should stop, or None to continue.
    When *from_degradation* is True, L2/L3 patience exhaustion resets counters
    instead of stopping — the degradation investigation loop continues.
    """
    _l2_kwargs = dict(obs=obs, trace_id=trace_id, escalation_context=escalation_context)

    # Track L2 stall
    l2_improved = state.best_composite > state.best_composite_at_l2_entry
    state.l2_stall_count = 0 if l2_improved or state.l2_round == 0 else state.l2_stall_count + 1

    # Not stalled → plain L2 transition
    l2_stalled = config.l2_patience is not None and state.l2_stall_count >= config.l2_patience
    if not l2_stalled:
        await _do_l2_transition(state, config, round_num, eval_data, on_phase, **_l2_kwargs)
        return None

    # L2 stalled, L3 disabled → exhaust or reset
    if not config.enable_l3:
        if from_degradation:
            logger.info(
                "L2 patience exhausted during degradation — resetting at round %d", round_num,
            )
            _degradation_reset(state, config, round_num, on_phase)
            await _do_l2_transition(state, config, round_num, eval_data, on_phase, **_l2_kwargs)
            return None
        logger.info(
            "L2 patience exhausted (%d stalls) at round %d", state.l2_stall_count, round_num,
        )
        return StopReason.L2_PATIENCE

    # L2 stalled, L3 enabled — track L3 stall
    l3_improved = state.best_composite > state.best_composite_at_l3_entry
    state.l3_stall_count = 0 if l3_improved or state.l3_round == 0 else state.l3_stall_count + 1

    l3_exhausted = config.l3_patience is not None and state.l3_stall_count >= config.l3_patience
    if not l3_exhausted:
        await _do_l3_transition(
            state, config, round_num, eval_data, on_phase, obs=obs, trace_id=trace_id,
        )
        return None

    # L3 exhausted → exhaust or reset
    if from_degradation:
        logger.info(
            "L3 patience exhausted during degradation — resetting L2/L3 at round %d", round_num,
        )
        _degradation_reset(state, config, round_num, on_phase, reset_l3=True)
        await _do_l2_transition(state, config, round_num, eval_data, on_phase, **_l2_kwargs)
        return None
    logger.info("L3 patience exhausted (%d stalls) at round %d", state.l3_stall_count, round_num)
    return StopReason.L3_PATIENCE
