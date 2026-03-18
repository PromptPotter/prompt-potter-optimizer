"""
Feedback cycling orchestrator for iterative prompt optimization.

Runs ``generate_candidates()`` → ``evaluate_and_select_winner()`` in a
counter-based loop.  Each round generates Layer 1 variants and evaluates
them against the current best.

Stopping conditions:
- ``max_rounds`` reached
- ``patience`` consecutive non-improving rounds exhausted
- ``next_action == "stop"`` from suggestion analysis (when enabled)
- ``winner_accuracy >= 1.0`` (perfect score)
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from api.models.phase_event import PhaseEvent
from api.models.prompt_state import PromptState, diff as prompt_diff
from api.models.search_point import SearchPoint
from api.models.opt_search_point import OptSearchPoint
from api.nodes.optimizer_nodes import (
    InitNode, L1GenerateNode, L1EvaluateNode, L2RefineNode, L3ModifyPlanNode,
)
from api.services.backend_client import BackendClient
from api.services.campaign import layer_transitions
from api.services.constants import DATASET_NAME
from api.services.campaign.models import (
    CycleConfig, CycleResult, CycleRoundResult, _LoopState,
)
from api.services.campaign.critique import CritiqueAgent, sample_thinking_styles
from api.services.prompt_eval import EvalContext, compute_composite_score
from api.services.query_utils import subsample_queries

# Module-level import for test monkeypatching.
from api.services import llm_client as _llm_client

if TYPE_CHECKING:
    from api.services.obs.observability_logger import ObsLogger
    from api.services.stores.campaign_store import CampaignStore

logger = logging.getLogger(__name__)


def _emit_phase(
    on_phase: Callable[[PhaseEvent], None] | None,
    phase: str,
    event: str,
    *,
    round: int | None = None,
    **data: Any,
) -> None:
    """Construct a PhaseEvent and call the callback if set."""
    if on_phase is None:
        return
    pe = PhaseEvent(phase=phase, event=event, round=round, data=data)
    on_phase(pe)


def _candidate_summaries(
    candidates: list[dict],
    current_ps_dict: dict,
) -> list[dict]:
    """Build compact per-candidate summary dicts for phase event data."""
    current_ps = PromptState(**current_ps_dict)
    summaries = []
    for i, c in enumerate(candidates):
        # Non-destructive read of pipeline override
        pp_override = c.get("__pipeline_params_override__")

        # Diff to find changed fields
        # Strip the dunder key before constructing PromptState
        c_clean = {k: v for k, v in c.items() if k != "__pipeline_params_override__"}
        try:
            candidate_ps = PromptState(**c_clean)
            delta = prompt_diff(current_ps, candidate_ps)
            changed_fields = [fc.field for fc in delta.field_changes]
        except Exception:
            logger.debug("Failed to diff candidate %d", i, exc_info=True)
            changed_fields = []

        summary: dict = {
            "idx": i,
            "changes_description": c.get("changes_description", ""),
            "changed_fields": changed_fields,
        }
        if pp_override:
            summary["pipeline_params_override"] = pp_override
        summaries.append(summary)
    return summaries


def cycle_config_identity(
    config: CycleConfig,
    baseline_rendered: str,
    eval_data: list[dict],
) -> str:
    """Compute a stable identity hash for a feedback cycle configuration.

    Covers optimization-relevant fields only so the same config produces the
    same cycle_id across kernel restarts.  Infrastructure fields (backend_url,
    project_root, etc.) are excluded.
    """
    payload = json.dumps(
        {
            "max_rounds": config.max_rounds,
            "patience": config.patience,
            "n_variants": config.n_variants,
            "creativity": config.creativity,
            "improvement_threshold": config.improvement_threshold,
            "model": config.model,
            "provider": config.provider,
            "temperature": config.temperature,
            "sample_size": config.sample_size,
            "seed": config.seed,
            "baseline_rendered": baseline_rendered,
            "eval_data_pairs": sorted(
                (d.get("query", ""), d.get("ground_truth", ""))
                for d in eval_data
            ),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"cycle_{digest}"


def _init_obs(
    config: CycleConfig,
    obs_campaign_id: str,
    baseline_accuracy: float,
    eval_data: list[dict],
    langfuse_session_id: str | None,
) -> tuple[Any | None, str | None, dict[str, str] | None]:
    """Create ObsLogger, log campaign start, register dataset.

    Returns:
        (obs, dataset_name, dataset_item_map)
    """
    from api.services.obs.observability_logger import ObsLogger  # lazy: heavy dep

    obs: ObsLogger | None = None
    if config.project_root and config.backend_id:
        try:
            obs = ObsLogger(config.project_root, config.backend_id)
        except Exception:
            logger.warning("Failed to create ObsLogger", exc_info=True)

    dataset_name: str | None = None
    dataset_item_map: dict[str, str] | None = None
    if not obs:
        return obs, dataset_name, dataset_item_map

    try:
        obs.log_campaign_start(
            campaign_id=obs_campaign_id,
            config=config.model_dump(),
            baseline_accuracy=baseline_accuracy,
            session_id=langfuse_session_id,
        )
    except Exception:
        logger.warning("ObsLogger.log_campaign_start failed", exc_info=True)

    try:
        dataset_name = DATASET_NAME
        dataset_item_map = obs.register_dataset(dataset_name, eval_data)
        if dataset_item_map:
            logger.info(
                "Registered %d dataset items for '%s'",
                len(dataset_item_map), dataset_name,
            )
    except Exception:
        logger.warning("Dataset registration failed", exc_info=True)
        dataset_item_map = None

    return obs, dataset_name, dataset_item_map


def _validate_config_match(
    config: CycleConfig,
    stored_cfg: dict,
    cycle_id: str,
) -> None:
    """Validate current config matches stored campaign config.

    Raises ValueError with a clear diff if key optimization fields differ.
    This enforces the EXPERIMENT_ID invariant: no silent config drift.
    """
    # Only data-affecting fields — loop-control fields (max_rounds,
    # patience, l2_patience, l3_patience, degradation_threshold) can
    # change freely between runs of the same experiment.
    check_keys = [
        "n_variants", "creativity",
        "improvement_threshold", "model", "sample_size",
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
            f"or update campaign_config to match.\n"
            + "\n".join(mismatches)
        )


def _resume_or_create_campaign(
    config: CycleConfig,
    eval_data: list[dict],
    current_ps: dict,
    baseline_prompt_state: dict | None,
    baseline_accuracy: float,
    *,
    cycle_id_override: str | None = None,
) -> tuple[Any | None, str | None, int]:
    """Handle campaign resume detection.

    Args:
        cycle_id_override: Explicit campaign ID (skips hash computation).
            Used when the user sets EXPERIMENT_ID in the notebook.
            When set and campaign exists, validates config matches stored.

    Returns:
        (campaign_store, cycle_id, resumed_from_round)
    """
    if not (config.project_root and config.backend_id):
        return None, None, 0

    try:
        from api.services.stores.campaign_store import CampaignStore  # lazy: heavy dep

        store_base = Path(config.project_root)
        campaign_store = CampaignStore(store_base)

        if cycle_id_override:
            cycle_id = cycle_id_override
        else:
            bl_ps = baseline_prompt_state if baseline_prompt_state is not None else current_ps
            cycle_id = cycle_config_identity(config, PromptState(**bl_ps).render(), eval_data)
        logger.info("Cycle identity: %s", cycle_id)

        existing = campaign_store.load(config.backend_id, cycle_id)
        if existing is not None:
            # Validate config matches when resuming with explicit ID
            if cycle_id_override:
                stored_cfg = existing.get("config", {})
                if stored_cfg:
                    _validate_config_match(config, stored_cfg, cycle_id)
                    # Update loop-control fields in stored config
                    _LOOP_CONTROL_KEYS = [
                        "max_rounds", "patience", "l2_patience",
                        "l3_patience", "degradation_threshold",
                    ]
                    current_cfg = config.model_dump()
                    cfg_updated = False
                    for k in _LOOP_CONTROL_KEYS:
                        if stored_cfg.get(k) != current_cfg.get(k):
                            stored_cfg[k] = current_cfg.get(k)
                            cfg_updated = True
                    if cfg_updated:
                        campaign_store.update(
                            config.backend_id, cycle_id, {"config": stored_cfg},
                        )
                        logger.info("Updated loop-control config for %s", cycle_id)
            resumed_from = len(existing.get("trials", []))
            if resumed_from:
                logger.info(
                    "Resuming cycle %s — %d prior round(s) on disk",
                    cycle_id, resumed_from,
                )
        else:
            resumed_from = 0
            campaign_store.create(config.backend_id, cycle_id, {
                "type": "feedback_cycle",
                "config": config.model_dump(),
                "baseline_accuracy": baseline_accuracy,
            })

        return campaign_store, cycle_id, resumed_from
    except ValueError:
        raise  # config mismatch — propagate to user
    except Exception:
        logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
        return None, None, 0


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
    current_ps = state.current_sp.prompt_state
    prompt_preview = current_ps.render()[:120]

    _emit_phase(on_phase, "l1_generate", "enter", round=round_num,
                current_accuracy=state.current_accuracy,
                prompt_preview=prompt_preview,
                n_variants=_n_variants,
                creativity=_creativity,
                model=config.model or "(default)",
                has_scan_context=config.scan_context is not None,
                has_critique=bool(state.critique_text),
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
                            persisted, state.current_sp.prompt_state.model_dump()))
            return persisted

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    l1_gen_node = L1GenerateNode(
        node_id=f"l1_generate_r{round_num}",
        config={"model": config.model, "provider": config.provider,
                "n_variants": _n_variants, "creativity": _creativity},
        obs=obs, trace_id=trace_id,
    )
    gen_result = await l1_gen_node.process({
        "prompt_state": current_ps.model_dump(),
        "accuracy": state.current_accuracy,
        "results": state.current_results,
        "critique_text": state.critique_text,
        "thinking_styles": state.thinking_styles,
        "scan_context": config.scan_context,
        "escalation_journal": state.escalation_journal,
    })
    candidates = gen_result.candidates

    if campaign_store and cycle_id:
        campaign_store.save_round_candidates(
            config.backend_id, cycle_id, round_num, candidates,
        )

    _emit_phase(on_phase, "l1_generate", "exit", round=round_num,
                n_candidates=len(candidates),
                n_eval_queries=n_eval_queries,
                loaded_from_disk=False,
                candidates=_candidate_summaries(
                    candidates, state.current_sp.prompt_state.model_dump()))

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
    """Evaluate candidates via L1EvaluateNode, optionally generate suggestions."""
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
        "prompt_state": state.current_sp.prompt_state.model_dump(),
        "results": state.current_results,
        "label": baseline_label,
    }

    l1_eval_node = L1EvaluateNode(
        node_id=f"l1_evaluate_r{round_num}",
        config={
            "eval_ctx": state.eval_ctx,
            "improvement_threshold": config.improvement_threshold,
            "on_candidate_eval": on_candidate_eval,
            "on_query_eval": on_query_eval,
            "model": config.model,
            "provider": config.provider,
            "enable_critique": config.enable_critique,
            "critique_positive_threshold": config.critique_positive_threshold,
            "thinking_styles_seed": config.seed + round_num + 1,
            "escalation_checks": escalation_checks,
            # Rich critique context
            "round_history": [
                {"round": r.round, "accuracy": r.accuracy,
                 "composite": r.composite,
                 "pipeline_params": r.pipeline_params,
                 "degraded": getattr(r, "degraded_queries", 0),
                 "n_candidates": len(r.candidate_scores)}
                for r in state.rounds
            ],
            "current_round": round_num,
            "stall_count": state.stall_count,
            "best_accuracy": state.best_accuracy,
            "best_round": state.best_round,
            "scan_context": config.scan_context,
            "pipeline_params": (
                state.current_sp.pipeline_params if state.current_sp else None
            ),
        },
        obs=obs, trace_id=trace_id,
    )
    eval_result = await l1_eval_node.process({
        "candidates": candidates,
        "eval_data": round_eval_data,
        "current_best": current_best,
    })

    eval_out: dict = {
        "winner": eval_result.winner,
        "winner_prompt_state": eval_result.winner_prompt_state,
        "winner_accuracy": eval_result.winner_accuracy,
        "improved": eval_result.improved,
        "next_action": eval_result.next_action,
        "candidate_scores": eval_result.candidate_scores,
        "winner_results": eval_result.winner_results,
        "critique_text": eval_result.critique_text,
        "thinking_styles": eval_result.thinking_styles,
        "winner_composite": eval_result.winner_composite,
        "winner_pipeline_params": eval_result.winner_pipeline_params,
        "escalation_signal": eval_result.escalation_signal,
        "degraded_queries": eval_result.degraded_queries,
    }

    # Suggestion generation stays in orchestrator (ADR-4)
    if config.generate_suggestions:
        try:
            from api.services.prompt_optimizer import generate_suggestions

            llm_client = _llm_client.get_llm_client(config.provider)
            rounds_for_suggestions = [
                {"round": r.round, "label": r.label, "accuracy": r.accuracy,
                 "prompt_state": PromptState(**r.prompt_state),
                 "results": r.results}
                for r in state.rounds
            ]
            rounds_for_suggestions.append({
                "round": round_num, "label": eval_out["winner"]["label"],
                "accuracy": eval_out["winner_accuracy"],
                "prompt_state": PromptState(**eval_out["winner_prompt_state"]),
                "results": eval_out["winner_results"],
            })
            suggestions = await generate_suggestions(
                rounds_for_suggestions, round_eval_data, {},
                llm_client, model=config.model,
                suggestion_temperature=config.suggestion_temperature,
            )
            next_action = suggestions.get("next_action", "generate")
            if next_action in ("generate", "stop", "refine_context", "modify_plan"):
                eval_out["next_action"] = next_action
            eval_out["suggestions"] = suggestions
        except Exception:
            logger.warning("Suggestion generation failed", exc_info=True)

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


def _infer_optimizer_templates(config: CycleConfig) -> list[str]:
    """Infer which optimizer prompt templates were used in a round."""
    gen = "meta_scan_aware" if config.scan_context else "meta_freeform"
    return [gen, "critique_negative", "critique_positive"]


def _log_round_obs(
    eval_out: dict,
    round_num: int,
    config: CycleConfig,
    obs: "ObsLogger",
    obs_campaign_id: str,
) -> None:
    """Log round-end metrics and prompt version to observability."""
    try:
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
            optimizer_templates=_infer_optimizer_templates(config),
        )
    except Exception:
        logger.warning("ObsLogger.log_round_end failed", exc_info=True)

    try:
        winner_ps = PromptState(**eval_out["winner_prompt_state"])
        obs.log_prompt_version(
            prompt_state_id=winner_ps.id,
            rendered_prompt=winner_ps.render(),
            layer1_fields={
                f: getattr(winner_ps, f)
                for f in [
                    "persona", "task_intent", "problem_description",
                    "instruction", "thinking_style", "answer_format",
                ]
            },
            parent_id=winner_ps.parent_id,
        )
    except Exception:
        logger.warning("ObsLogger.log_prompt_version failed", exc_info=True)


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
        try:
            obs.log_round_start(obs_campaign_id, round_num)
        except Exception:
            logger.warning("ObsLogger.log_round_start failed", exc_info=True)

    # Resolve L2 meta-param overrides from PromptState.parameters
    ps_params = state.current_sp.prompt_state.parameters
    n_variants = ps_params.get("n_variants", config.n_variants)
    creativity = ps_params.get("creativity", config.creativity)

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
    state.critique_text = eval_out.pop("critique_text", "")
    state.thinking_styles = eval_out.pop("thinking_styles", [])

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
        try:
            campaign_store.update(config.backend_id, cycle_id, {
                "status": status,
                "stop_reason": stop_reason,
                "best_accuracy": state.best_accuracy,
                "best_round": state.best_round,
                "n_rounds": len(state.rounds),
                "finished_at": finished_at,
            })
        except Exception:
            logger.warning("Campaign completion update failed", exc_info=True)

    cloud_trace_id: str | None = None
    if obs:
        try:
            obs.log_campaign_end(
                campaign_id=obs_campaign_id,
                best_accuracy=state.best_accuracy,
                n_rounds=len(state.rounds),
                stop_reason=stop_reason,
                best_round=state.best_round,
            )
            obs.flush()
            cloud_trace_id = obs.get_cloud_trace_id(obs_campaign_id)
        except Exception:
            logger.warning("ObsLogger campaign end failed", exc_info=True)

    return cloud_trace_id


# ---------------------------------------------------------------------------
# Layer escalation helpers
# ---------------------------------------------------------------------------


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
    """Perform L2 refine_context transition via L2RefineNode. Updates state in-place."""
    current_ps = state.current_sp.prompt_state
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
                current_params=current_ps.parameters,
                current_accuracy=state.current_accuracy,
                best_accuracy=state.best_accuracy)

    l2_node = L2RefineNode(
        node_id=f"l2_refine_r{round_num}",
        config={
            "model": config.model,
            "provider": config.provider,
            "temperature": config.l2_temperature,
            "pipeline_schema": config.pipeline_schema,
        },
        obs=obs, trace_id=trace_id,
    )
    l2_result = await l2_node.process({
        "prompt_state": current_ps.model_dump(),
        "stalled_rounds": stalled_rounds,
        "eval_data": eval_data,
        "pipeline_params": current_pp,
        "escalation_context": escalation_context,
        "escalation_journal": state.escalation_journal,
    })

    new_ps = PromptState(**l2_result.prompt_state)
    tr = layer_transitions.TransitionResult(
        prompt_state=new_ps,
        pipeline_params=l2_result.pipeline_params,
        debug_prompt=getattr(l2_result, "debug_prompt", ""),
        debug_response=getattr(l2_result, "debug_response", None),
    )
    state.current_sp = state.current_sp.derive(
        prompt_state=tr.prompt_state,
        **({"pipeline_params": tr.pipeline_params} if tr.pipeline_params else {}),
    )
    state.l2_round += 1
    state.best_accuracy_at_l2_entry = state.best_accuracy
    state.best_composite_at_l2_entry = state.best_composite
    _emit_phase(on_phase, "refine_context", "exit", round=round_num,
                l2_round=state.l2_round,
                param_changes_count=len(tr.prompt_state.parameters),
                context_changed=tr.prompt_state.context != current_ps.context,
                changes_description=tr.prompt_state.changes_description or "",
                pipeline_params_changed=tr.pipeline_params is not None,
                pipeline_params=tr.pipeline_params,
                l2_prompt=tr.debug_prompt,
                l2_response=tr.debug_response)
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
    """Perform L3 modify_plan transition via L3ModifyPlanNode. Updates state in-place."""
    current_ps = state.current_sp.prompt_state
    current_pp = state.current_sp.pipeline_params

    l2_history = [{
        "l2_round": state.l2_round,
        "parameters": current_ps.parameters,
        "accuracy_change": state.best_composite - state.best_composite_at_l3_entry,
    }]
    _emit_phase(on_phase, "modify_plan", "enter", round=round_num,
                l3_round=state.l3_round,
                l2_stall_count=state.l2_stall_count,
                current_plan_preview=str(current_ps.plan)[:120])

    l3_node = L3ModifyPlanNode(
        node_id=f"l3_modify_plan_r{round_num}",
        config={
            "model": config.model,
            "provider": config.provider,
            "temperature": config.l3_temperature,
            "pipeline_schema": config.pipeline_schema,
        },
        obs=obs, trace_id=trace_id,
    )
    l3_result = await l3_node.process({
        "prompt_state": current_ps.model_dump(),
        "l2_history": l2_history,
        "eval_data": eval_data,
        "pipeline_params": current_pp,
    })

    new_ps = PromptState(**l3_result.prompt_state)
    tr = layer_transitions.TransitionResult(
        prompt_state=new_ps,
        pipeline_params=l3_result.pipeline_params,
    )
    state.current_sp = state.current_sp.derive(
        prompt_state=tr.prompt_state,
        **({"pipeline_params": tr.pipeline_params} if tr.pipeline_params else {}),
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
                new_plan_preview=str(tr.prompt_state.plan)[:120],
                changes_description=tr.prompt_state.changes_description or "",
                pipeline_params_changed=tr.pipeline_params is not None)
    logger.info(
        "L3 modify_plan at round %d (l3_round=%d)",
        round_num, state.l3_round,
    )
    return tr


def _update_round_state(
    state: _LoopState, rr: CycleRoundResult, round_num: int,
) -> None:
    """Apply round result to loop state (shared by escalation + normal paths)."""
    state.rounds.append(rr)
    winner_ps = PromptState(**rr.prompt_state)
    derive_kwargs: dict = {"prompt_state": winner_ps}
    if rr.pipeline_params is not None:
        derive_kwargs["pipeline_params"] = rr.pipeline_params
    state.current_sp = state.current_sp.derive(**derive_kwargs)
    state.current_accuracy = rr.accuracy
    state.current_composite = rr.composite
    state.current_results = list(rr.results)
    if state.current_composite > state.best_composite:
        state.best_composite = state.current_composite
        state.best_accuracy = state.current_accuracy
        state.best_round = round_num
        state.best_sp = state.current_sp


def _escalation_obs(
    state: _LoopState, obs_campaign_id: str,
) -> tuple["ObsLogger | None", str | None]:
    """Get obs logger + trace ID for escalation calls."""
    obs = state.eval_ctx.obs if state.eval_ctx else None
    trace_id = obs.get_file_trace_id(obs_campaign_id) if obs else None
    return obs, trace_id


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
    # Check if L2 itself has stalled (no improvement since last L2 entry)
    l2_improved = state.best_composite > state.best_composite_at_l2_entry
    if not l2_improved and state.l2_round > 0:
        state.l2_stall_count += 1
    else:
        state.l2_stall_count = 0

    # L2 stalled → check L3
    if config.l2_patience is not None and state.l2_stall_count >= config.l2_patience:
        if config.enable_l3:
            l3_improved = state.best_composite > state.best_composite_at_l3_entry
            if not l3_improved and state.l3_round > 0:
                state.l3_stall_count += 1
            else:
                state.l3_stall_count = 0

            if config.l3_patience is not None and state.l3_stall_count >= config.l3_patience:
                if from_degradation:
                    logger.info(
                        "L3 patience exhausted during degradation — "
                        "resetting L2/L3 counters at round %d", round_num,
                    )
                    state.l2_stall_count = 0
                    state.l3_stall_count = 0
                    state.l2_round = 0
                    state.l3_round = 0
                    await _do_l2_transition(
                        state, config, round_num, eval_data, on_phase,
                        obs=obs, trace_id=trace_id,
                        escalation_context=escalation_context,
                    )
                    return None  # continue
                logger.info(
                    "L3 patience exhausted (%d stalls) at round %d",
                    state.l3_stall_count, round_num,
                )
                return "l3_patience_exhausted"

            await _do_l3_transition(
                state, config, round_num, eval_data, on_phase,
                obs=obs, trace_id=trace_id,
            )
            return None  # continue
        else:
            if from_degradation:
                logger.info(
                    "L2 patience exhausted during degradation — "
                    "resetting L2 counters at round %d", round_num,
                )
                state.l2_stall_count = 0
                state.l2_round = 0
                await _do_l2_transition(
                    state, config, round_num, eval_data, on_phase,
                    obs=obs, trace_id=trace_id,
                    escalation_context=escalation_context,
                )
                return None  # continue
            logger.info(
                "L2 patience exhausted (%d stalls) at round %d",
                state.l2_stall_count, round_num,
            )
            return "l2_patience_exhausted"

    await _do_l2_transition(
        state, config, round_num, eval_data, on_phase,
        obs=obs, trace_id=trace_id,
        escalation_context=escalation_context,
    )
    return None  # continue


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_feedback_cycle(
    instruction: str,
    eval_data: list[dict[str, Any]],
    config: CycleConfig,
    *,
    improvement_areas: str = "",
    baseline_prompt_state: dict | None = None,
    baseline_accuracy: float = 0.0,
    baseline_results: list | None = None,
    on_round_complete: Callable[[CycleRoundResult, int], None] | None = None,
    on_candidate_eval: Callable[[int, int, dict], None] | None = None,
    on_query_eval: Callable[[int, int, int, int, dict], None] | None = None,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    langfuse_session_id: str | None = None,
    scan_context: dict | None = None,
    cycle_id: str | None = None,
    experiment_id: str = "",
) -> CycleResult:
    """Run iterative optimization with feedback cycling.

    Executes:
    1. InitNode — create baseline PromptState (skipped if baseline_prompt_state provided)
    2. Loop: generate_candidates → evaluate_and_select_winner
    3. Stop when patience exhausted, max_rounds reached, or perfect score

    Args:
        instruction: Raw prompt instruction text (used by InitNode if no baseline).
        eval_data: Evaluation dataset (list of query dicts).
        config: Cycle configuration.
        improvement_areas: Domain-expert guidance for context restructuring.
        baseline_prompt_state: Existing baseline PromptState (serialized dict).
            If provided, InitNode is skipped and this is used as the starting point.
        baseline_accuracy: Accuracy of the baseline (used when baseline_prompt_state
            is provided).
        baseline_results: Eval results for the baseline (for failure analysis).
        on_round_complete: Optional callback after each round:
            ``(round_result: CycleRoundResult, stall_count: int)``
        on_candidate_eval: Optional callback after each candidate evaluation:
            ``(candidate_idx: int, n_candidates: int, scores: dict)``
        langfuse_session_id: Optional session ID for grouping Langfuse traces.
        scan_context: Scan analytics context for scan-aware candidate generation.
            When provided, overrides config.scan_context for this run.

    Returns:
        CycleResult with all rounds and final winner.
    """
    if scan_context is not None:
        config = config.model_copy(update={"scan_context": scan_context})
    started_at = datetime.now(timezone.utc).isoformat()

    _emit_phase(on_phase, "init", "enter",
                max_rounds=config.max_rounds,
                patience=config.patience,
                n_variants=config.n_variants,
                model=config.model or "(default)",
                sample_size=config.sample_size,
                enable_l2=config.enable_l2,
                enable_l3=config.enable_l3,
                eval_data_count=len(eval_data),
                baseline_accuracy=baseline_accuracy,
                has_scan_context=config.scan_context is not None,
                enable_critique=config.enable_critique,
                pipeline_params=config.pipeline_params)

    # Initialize backend session (required before /matches calls)
    if config.session_terms:
        bc = BackendClient(config.backend_url)
        await bc.init_session(config.session_terms)

    round_eval_data = subsample_queries(eval_data, config.sample_size, config.seed)

    # -- Step 1: Initialize baseline --
    if baseline_prompt_state is not None:
        current_ps = PromptState(**baseline_prompt_state) if isinstance(
            baseline_prompt_state, dict,
        ) else baseline_prompt_state
        current_accuracy = baseline_accuracy
        current_results: list = baseline_results or []
        logger.info("Using provided baseline (acc=%.3f)", current_accuracy)
    else:
        init_node = InitNode(
            node_id="cycle_init",
            config={"model": config.model, "provider": config.provider},
        )
        init_out = await init_node.process({
            "instruction": instruction,
            "improvement_areas": improvement_areas,
        })
        current_ps = PromptState(**init_out.prompt_state) if isinstance(
            init_out.prompt_state, dict,
        ) else init_out.prompt_state
        current_accuracy = 0.0
        current_results = []

    # -- Step 2: Resume detection --
    campaign_store, cycle_id, resumed_from_round = _resume_or_create_campaign(
        config, eval_data, current_ps.model_dump(),
        baseline_prompt_state, baseline_accuracy,
        cycle_id_override=cycle_id,
    )
    # -- Step 3: Observability setup --
    # Default Langfuse session_id to cycle_id for experiment-level grouping
    if not langfuse_session_id and cycle_id:
        langfuse_session_id = cycle_id
    obs_campaign_id = cycle_id or f"campaign_{started_at[:19].replace(':', '')}"
    obs, dataset_name, dataset_item_map = _init_obs(
        config, obs_campaign_id, baseline_accuracy, eval_data, langfuse_session_id,
    )

    # -- Step 4: Initialize loop state (always fresh from baseline) --
    # Even on resume, start fresh — cached rounds replay from disk/cache.
    if current_results:
        _bl_scores = compute_composite_score(current_results, config.pipeline_schema)
        _bl_composite = _bl_scores["composite"]
    else:
        _bl_composite = current_accuracy
    baseline_sp = SearchPoint(
        prompt_state=current_ps,
        model=config.model or "",
        temperature=config.temperature,
        pipeline_params=config.pipeline_params,
    )
    state = _LoopState(
        current_sp=baseline_sp,
        current_accuracy=current_accuracy,
        current_composite=_bl_composite,
        current_results=current_results,
        best_accuracy=current_accuracy,
        best_composite=_bl_composite,
        best_sp=baseline_sp,
    )

    # -- Step 4b: Bootstrap critique + thinking styles --
    bootstrap_critique = ""
    if config.enable_critique and current_results:
        try:
            _crit_llm = _llm_client.get_llm_client(config.provider)
            agent = CritiqueAgent(_crit_llm, model=config.model)
            bootstrap_critique = await agent.run(
                current_results, current_accuracy,
                threshold=config.critique_positive_threshold,
            )
            state.critique_text = bootstrap_critique
            logger.info("Bootstrap critique: %s", bootstrap_critique[:100])
        except Exception:
            logger.warning("Bootstrap critique failed", exc_info=True)
    state.thinking_styles = sample_thinking_styles(n=3, seed=config.seed)

    _emit_phase(on_phase, "init", "exit",
                cycle_id=cycle_id,
                resumed_from_round=resumed_from_round,
                baseline_accuracy=current_accuracy,
                obs_enabled=obs is not None,
                sample_count=len(round_eval_data),
                enable_critique=config.enable_critique,
                critique_text=bootstrap_critique)

    # Build shared EvalContext once (reused across all rounds)
    _bc = BackendClient(config.backend_url)
    _store = None
    if config.project_root:
        from api.services.project_store import ProjectStore
        _store = ProjectStore(config.project_root)
    state.eval_ctx = EvalContext(
        backend_client=_bc,
        store=_store,
        backend_id=config.backend_id,
        pipeline_schema=config.pipeline_schema,
        obs=obs,
        dataset_name=dataset_name if dataset_name and dataset_item_map else None,
        dataset_item_map=dataset_item_map if dataset_name and dataset_item_map else None,
        source="feedback_cycle",
        model=config.model or "",
        temperature=config.temperature,
        pipeline_params=config.pipeline_params,
        experiment_id=experiment_id or (cycle_id.replace("cycle_", "")[:12] if cycle_id else ""),
    )

    stop_reason: str | None = None
    interrupted = False

    # -- Step 5: Build escalation checks + round loop --
    from api.services.campaign.escalation import build_escalation_checks
    escalation_checks = build_escalation_checks(config)

    _HARD_CAP = 100  # safety valve for runaway degradation loops

    try:
        round_num = 0
        clean_rounds = 0
        max_rounds = config.max_rounds or 999

        while clean_rounds < max_rounds and round_num < _HARD_CAP:
            # Sync eval_ctx pipeline_params from current search point
            state.eval_ctx.pipeline_params = state.current_sp.pipeline_params

            logger.info(
                "Feedback cycle round %d (clean=%d/%d, acc=%.3f, stall=%d/%d)",
                round_num, clean_rounds, max_rounds,
                state.current_accuracy, state.stall_count, config.patience,
            )

            round_result = await _execute_round(
                round_num, state, round_eval_data, config,
                obs_campaign_id,
                campaign_store, cycle_id,
                on_candidate_eval, on_query_eval, on_phase,
                escalation_checks=escalation_checks,
            )

            # Common state update (both escalation + normal paths)
            _update_round_state(state, round_result, round_num)

            # --- Escalation path ---
            if round_result.escalation_signal:
                signal = round_result.escalation_signal
                # No stall_count reset — degradation didn't produce L1 signal

                _emit_phase(
                    on_phase, "escalation", "enter", round=round_num,
                    check_name=signal["check_name"],
                    target=signal["target"],
                    degraded_rate=signal["context"].get("degraded_rate"),
                    warning_types=signal["context"].get("warning_types"),
                )
                logger.warning(
                    "Escalation '%s' at round %d — target=%s, "
                    "degraded_rate=%.1f%%",
                    signal["check_name"], round_num, signal["target"],
                    signal["context"].get("degraded_rate", 0) * 100,
                )

                # Classify warnings for exit event
                from api.services.campaign.escalation import classify_warnings
                _wt = signal["context"].get("warning_types", {})
                _classifications = classify_warnings(_wt, [
                    {"round": r.round, "pipeline_params": r.pipeline_params,
                     "warning_types": {}}
                    for r in state.rounds
                ])
                _exit_data = {
                    "classifications": [c.to_dict() for c in _classifications],
                }

                # Dispatch by target
                _esc_ctx = signal["context"]
                if signal["target"] in ("l2", "l3") and config.enable_l2:
                    # Fill outcome of previous journal entry (if any)
                    if (state.escalation_journal
                            and state.escalation_journal[-1].get("outcome_degraded_rate") is None):
                        state.escalation_journal[-1]["outcome_degraded_rate"] = (
                            _esc_ctx.get("degraded_rate", 0)
                        )

                    _obs, _tid = _escalation_obs(state, obs_campaign_id)
                    try:
                        _l2_stop = await _escalate_l2(
                            state, config, round_num, round_eval_data, on_phase,
                            obs=_obs, trace_id=_tid,
                            from_degradation=True,
                            escalation_context=_esc_ctx,
                        )
                    except (KeyboardInterrupt, asyncio.CancelledError):
                        raise
                    except Exception:
                        logger.warning("L2 escalation failed", exc_info=True)
                        _l2_stop = None
                    if _l2_stop:
                        stop_reason = _l2_stop
                        break

                    # Record journal entry after L2 transition
                    _ws_cfg = (
                        (state.current_sp.pipeline_params or {}).get("web_search", {})
                    )
                    state.escalation_journal.append({
                        "round": round_num,
                        "degraded_rate": _esc_ctx.get("degraded_rate", 0),
                        "query_prefix": _ws_cfg.get("query_prefix", ""),
                        "query_suffix": _ws_cfg.get("query_suffix", ""),
                        "web_search_config": {
                            k: _ws_cfg[k]
                            for k in ("max_sites", "num_results", "content_char_limit")
                            if k in _ws_cfg
                        },
                        "warning_types": _esc_ctx.get("warning_types", {}),
                        "l2_action": (
                            state.current_sp.prompt_state.changes_description or ""
                        ),
                        "outcome_degraded_rate": None,
                    })
                elif signal["target"] == "abort":
                    stop_reason = "escalation_abort"
                    break

                # Common escalation exit (L2 continued, retry, or L2 disabled)
                if campaign_store and cycle_id:
                    campaign_store.delete_round_candidates(
                        config.backend_id, cycle_id, round_num + 1,
                    )
                _emit_phase(on_phase, "escalation", "exit", round=round_num,
                            **_exit_data)
                # Degradation rounds don't count toward max_rounds
                round_num += 1
                continue

            # --- Normal path (no escalation) ---
            state.stall_count = (
                0 if round_result.improved else state.stall_count + 1
            )

            if on_round_complete:
                on_round_complete(round_result, state.stall_count)

            # Checkpoint round to disk
            if campaign_store and cycle_id:
                try:
                    opt_sp = OptSearchPoint(
                        critique_text=state.critique_text,
                        thinking_styles=state.thinking_styles,
                        plan=state.current_sp.prompt_state.plan,
                        context=state.current_sp.prompt_state.context,
                        parameters=state.current_sp.prompt_state.parameters,
                    )
                    campaign_store.add_trial(config.backend_id, cycle_id, {
                        "trial_id": f"round_{round_num}",
                        "round": round_num,
                        "label": round_result.label,
                        "accuracy": round_result.accuracy,
                        "composite": round_result.composite,
                        "hits": round_result.hits,
                        "total": round_result.total,
                        "improved": round_result.improved,
                        "next_action": round_result.next_action,
                        "prompt_state": round_result.prompt_state,
                        "results": round_result.results,
                        "candidates_evaluated": round_result.candidates_evaluated,
                        "candidate_scores": [
                            s if isinstance(s, dict) else s
                            for s in round_result.candidate_scores
                        ],
                        "stall_count": state.stall_count,
                        "l2_stall_count": state.l2_stall_count,
                        "l3_stall_count": state.l3_stall_count,
                        "l2_round": state.l2_round,
                        "l3_round": state.l3_round,
                        "opt_search_point": opt_sp.model_dump(),
                    })
                except Exception:
                    logger.warning("Round checkpoint failed", exc_info=True)

            # Stopping conditions
            if state.current_accuracy >= 1.0:
                stop_reason = "perfect_score"
                break
            if round_result.next_action == "stop":
                stop_reason = "next_action_stop"
                break
            if state.stall_count >= config.patience:
                if config.enable_l2:
                    _obs, _tid = _escalation_obs(state, obs_campaign_id)
                    _l2_stop = await _escalate_l2(
                        state, config, round_num, round_eval_data, on_phase,
                        obs=_obs, trace_id=_tid,
                    )
                    if _l2_stop:
                        stop_reason = _l2_stop
                        break
                    state.stall_count = 0  # L2/L3 changed prompt — fresh window
                else:
                    stop_reason = "patience_exhausted"
                    break

            round_num += 1
            clean_rounds += 1

        # Loop completed without break
        if stop_reason is None:
            if round_num >= _HARD_CAP:
                stop_reason = "hard_cap_reached"
            else:
                stop_reason = "max_rounds"

    except (KeyboardInterrupt, asyncio.CancelledError):
        stop_reason = "interrupted"
        interrupted = True
        logger.warning(
            "Feedback cycle interrupted at round %d. "
            "Completed rounds are checkpointed.",
            len(state.rounds),
        )

    finally:
        # Best-effort httpx cleanup — prevents event loop leak on interrupt.
        # Same pattern as NodeBase.process() RC-5 fix (commit d898e64).
        try:
            await _bc.aclose()
        except Exception:
            pass

    # -- Step 6: Finalize --
    # Skip I/O-heavy finalization on interrupt — Langfuse/obs calls hang
    # on corrupted Windows asyncio event loop (same root cause as RC-5).
    finished_at = datetime.now(timezone.utc).isoformat()
    cloud_trace_id = None
    if not interrupted:
        campaign_status = "completed"
        cloud_trace_id = _finalize_campaign(
            campaign_store, cycle_id, config, state, stop_reason, finished_at,
            obs, obs_campaign_id, status=campaign_status,
        )

    return CycleResult(
        rounds=state.rounds,
        n_rounds=len(state.rounds),
        best_accuracy=state.best_accuracy,
        best_round=state.best_round,
        baseline_accuracy=(
            state.rounds[0].accuracy if state.rounds else state.current_accuracy
        ),
        winner_prompt_state=state.best_sp.prompt_state.model_dump() if state.best_sp else {},
        winner_pipeline_params=state.best_sp.pipeline_params if state.best_sp else None,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        langfuse_trace_id=cloud_trace_id,
        cycle_id=cycle_id,
        resumed_from_round=resumed_from_round,
    )
