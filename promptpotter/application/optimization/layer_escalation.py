"""L1→L2→L3 layer-escalation orchestration. Entry point: ``escalate_l2``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.decisions import record_decision
from promptpotter.application.optimization.nodes.layer_transitions import (
    L2RefineStrategy,
    L3ModifyPlan,
    LayerTransition,
)
from promptpotter.domain.phases import PhaseEvent, StopReason, emit_phase
from promptpotter.infrastructure.llm import client as _llm_client
from promptpotter.infrastructure.tracing import observed_node
from promptpotter.infrastructure.tracing.events import LayerApplied
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = ["build_escalation_entry", "escalate_l2"]


def build_escalation_entry(
    round_num: int,
    check_result: dict[str, Any],
    current_pipeline_params: dict | None,
) -> dict[str, Any]:
    """Shape a journal entry from a DegradationCheck result + live pipeline params."""
    dominant = check_result.get("dominant_warning", "unknown:unknown")
    problem_step = dominant.split(":")[0] if ":" in dominant else "unknown"
    step_cfg = (current_pipeline_params or {}).get(problem_step, {})
    return {
        "round": round_num,
        "degraded_rate": check_result.get("degraded_rate", 0),
        "problem_step": problem_step,
        "step_config": dict(step_cfg) if isinstance(step_cfg, dict) else {},
        "warning_types": check_result.get("warning_types", {}),
        "outcome_degraded_rate": None,
    }


_TEMP_ATTR: dict[str, str] = {"L2": "l2_temperature", "L3": "l3_temperature"}


async def _run_transition(
    transition: LayerTransition,
    cycle: Cycle,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    obs: ObservabilityBridge | None,
    obs_campaign_id: str,
    escalation_check_result: dict | None = None,
) -> Any:
    """Unified L2/L3 orchestrator: enter → call → adopt → LayerApplied → side-effects → exit."""
    assert cycle.current_sp is not None
    client = _llm_client.get_llm_client(config.optimizer_llm.provider)
    current_pp = cycle.current_sp.pipeline_params

    emit_phase(
        on_phase, transition.phase, "enter", round=round_num, **transition.enter_payload(cycle)
    )
    async with observed_node(
        f"{transition.template_name}_r{round_num}",
        "llm/meta",
        obs=obs,
        campaign_id=obs_campaign_id,
        round_num=round_num,
    ):
        result = await transition.run(
            cycle,
            client,
            model=config.optimizer_llm.model,
            temperature=getattr(config.optimization, _TEMP_ATTR[transition.layer]),
            pipeline_params=current_pp,
            round_num=round_num,
            escalation_check_result=escalation_check_result,
        )
    cycle.adopt_transition(
        result.opt_search_point,
        result.pipeline_params,
        schema=pipeline_schema,
    )
    if obs is not None:
        with graceful(f"LayerApplied({transition.layer}) emit failed"):
            obs.emit_write_point(
                LayerApplied,
                layer=transition.layer,
                campaign_id=obs_campaign_id,
                round_num=round_num,
                changes_description=result.opt_search_point.lineage.changes_description or "",
            )
    transition.apply_side_effects(cycle, result, round_num)
    emit_phase(
        on_phase,
        transition.phase,
        "exit",
        round=round_num,
        **transition.exit_payload(cycle, result),
    )
    return result


async def escalate_l2(
    cycle: Cycle,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    on_checkpoint: Callable[[str], str | None] | None = None,
    obs: ObservabilityBridge | None = None,
    obs_campaign_id: str = "",
    escalation_check_result: dict | None = None,
) -> StopReason | None:
    """L1→L2 (and optional L2→L3) escalation; vanilla patience-exhausts → next layer / stop."""
    opt = config.optimization
    esc = cycle.escalation
    esc.l2.record_outcome(cycle.best_composite)

    l2_stalled = opt.l2_patience is not None and esc.l2.stall_count >= opt.l2_patience
    # entry_round = round whose rescored best_composite is the stall baseline (-1 = never fired).
    entry_round_l2 = esc.l2.round if esc.l2.round > 0 else -1
    record_decision(
        cycle.pending_decisions,
        "l2_escalation_trigger",
        {
            "round_num": round_num,
            "l2_patience": opt.l2_patience,
            "entry_round": entry_round_l2,
        },
        not l2_stalled,
        data={
            "l2_round": esc.l2.round,
            "stall_count": esc.l2.stall_count,
            "best_composite_at_entry": esc.l2.best_composite_at_entry,
            "best_composite_this_round": cycle.best_composite,
            "best_accuracy": cycle.best_accuracy,
        },
    )
    if not l2_stalled:
        await _run_transition(
            L2RefineStrategy(),
            cycle,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            obs_campaign_id=obs_campaign_id,
            escalation_check_result=escalation_check_result,
        )
        return None

    if not opt.enable_l3:
        logger.debug("L2 patience exhausted (%d stalls) at round %d", esc.l2.stall_count, round_num)
        return StopReason.L2_PATIENCE

    esc.l3.record_outcome(cycle.best_composite)
    l3_exhausted = opt.l3_patience is not None and esc.l3.stall_count >= opt.l3_patience
    entry_round_l3 = esc.l3.round if esc.l3.round > 0 else -1
    record_decision(
        cycle.pending_decisions,
        "l3_escalation_trigger",
        {
            "round_num": round_num,
            "l3_patience": opt.l3_patience,
            "entry_round": entry_round_l3,
        },
        not l3_exhausted,
        data={
            "l3_round": esc.l3.round,
            "stall_count": esc.l3.stall_count,
            "best_composite_at_entry": esc.l3.best_composite_at_entry,
            "best_composite_this_round": cycle.best_composite,
        },
    )
    if not l3_exhausted:
        await _run_transition(
            L3ModifyPlan(),
            cycle,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            obs_campaign_id=obs_campaign_id,
        )
        return None

    logger.debug("L3 patience exhausted (%d stalls) at round %d", esc.l3.stall_count, round_num)
    return StopReason.L3_PATIENCE
