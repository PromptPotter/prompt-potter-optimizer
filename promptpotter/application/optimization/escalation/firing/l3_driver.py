"""L3 driver — parse the LLM output, apply OSP mutations, build enter/exit
payloads, register the strategy.

L3's job: rewrite ``plan`` (strategic frame inside which L2 refines + L1
generates). Optional ``note`` is the sticky L3→L2 pointer; survives L2
fires, replaced wholesale on each L3 fire. No ``pipeline_params`` deltas.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.dispatch.schemas import L3PlanOutput
from promptpotter.application.optimization.escalation.firing.executor import LayerStrategy
from promptpotter.application.optimization.transitions import TransitionResult
from promptpotter.application.optimization.validators.l2_l3 import run_l3_output_validators
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle

logger = logging.getLogger(__name__)


def _parse_l3(raw: L3PlanOutput, opt_sp: OptSearchPoint, prompt: str) -> TransitionResult:
    new_plan = raw.plan or opt_sp.plan
    rationale = raw.rationale or "L3 modify_plan transition"
    failures = run_l3_output_validators({"plan": new_plan}, opt_sp)
    if failures:
        logger.warning(
            "L3 output failed %d validator(s): %s",
            len(failures),
            ", ".join(o.validator_id for o in failures),
        )
    return TransitionResult(
        opt_search_point=opt_sp.mutate(
            plan=new_plan, changes_description=f"L3: {rationale[:80]}", source="l3_plan"
        ),
        l3_note=raw.note,
        l3_guard_breaches=failures,
        debug_prompt=prompt,
        debug_response=raw.model_dump(),
    )


def _apply_l3(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    # Order matters: ``_run_transition`` already ran ``copy_memory_to``
    # which carried the *prior* l3_note onto the new OSP. We overwrite it
    # with the L3 fire's output (possibly ``""`` when the LLM omitted
    # ``note``) — that's the "cleared only when L3 fires again" contract.
    cycle.opt_sp.wounds.l3_note = result.l3_note
    cycle.opt_sp.wounds.l3_guard_breaches = list(result.l3_guard_breaches)
    cycle.escalation.record_l3_fired(
        best_accuracy=cycle.tracking.best_accuracy,
        best_composite_fitness=cycle.tracking.best_composite_fitness,
    )


def _l3_enter(cycle: Cycle) -> dict[str, Any]:
    return {
        "l3_round": cycle.escalation.l3_round,
        "l2_stall_count": cycle.escalation.l2_stall_count,
        "current_plan_preview": str(cycle.opt_sp.plan)[:120],
    }


def _l3_exit(cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
    # ``l3_*_at_entry`` are read by ``EscalationState.fold`` on resume —
    # ``record_l3_fired`` also resets L2 state to these same origins.
    return {
        "l3_round": cycle.escalation.l3_round,
        "l3_stall_count": cycle.escalation.l3_stall_count,
        "l3_best_accuracy_at_entry": cycle.escalation.l3_best_accuracy_at_entry,
        "l3_best_composite_fitness_at_entry": cycle.escalation.l3_best_composite_fitness_at_entry,
        "new_plan_preview": str(result.opt_search_point.plan)[:120],
        "changes_description": result.opt_search_point.lineage.changes_description or "",
    }


L3 = LayerStrategy(
    layer_id="L3",
    template_name="l3_plan",
    default_temperature=0.5,
    phase=CampaignPhase.MODIFY_PLAN,
    parse=_parse_l3,
    apply=_apply_l3,
    enter_payload_fn=_l3_enter,
    exit_payload_fn=_l3_exit,
)


__all__ = ["L3"]
