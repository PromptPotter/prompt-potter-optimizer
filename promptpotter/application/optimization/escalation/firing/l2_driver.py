"""L2 driver — parse the LLM output, apply OSP mutations, build enter/exit
payloads, register the strategy.

L2's job: refine ``task_context`` (broadcast framing) and optionally edit
``l1_layout`` / ``l1_overrides`` for L1's next round. No
``pipeline_params`` deltas — those belong to L1's surface.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.dispatch.schemas import L2ContextOutput
from promptpotter.application.optimization.resume_and_fork import (
    ResumeCheckpointKind,
    record_decision,
)
from promptpotter.application.optimization.transitions import LayerStrategy, TransitionResult
from promptpotter.application.optimization.validators.l2_output import run_l2_output_validators
from promptpotter.domain.l1_layout import L1Layout, coerce_l1_layout, validate_l1_layout
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.shared import truncate

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle

logger = logging.getLogger(__name__)


def _parse_l2(raw: L2ContextOutput, opt_sp: OptSearchPoint, prompt: str) -> TransitionResult:
    rationale = raw.rationale or "L2 refine_strategy transition"
    changes: dict[str, Any] = {"changes_description": f"L2: {truncate(rationale, 80)}"}
    if raw.l1_overrides:
        changes["l1_overrides"] = {**opt_sp.l1_overrides, **raw.l1_overrides}

    new_task_context = None
    proposed_tc = raw.task_context if raw.task_context else None
    if proposed_tc:
        merged = opt_sp.task_context.merge(proposed_tc)
        if merged.to_dict() != opt_sp.task_context.to_dict():
            new_task_context = merged

    proposed_layout = coerce_l1_layout(raw.l1_layout)
    layout_outcomes: list[ValidatorOutcome] = []
    accepted_layout: L1Layout | None = None
    if proposed_layout is not None:
        layout_result = validate_l1_layout(proposed_layout, prior_layout=opt_sp.l1_layout)
        layout_outcomes = list(layout_result.outcomes)
        if layout_result.is_valid:
            accepted_layout = proposed_layout

    # Supplemental rules + situational examples: full-replace semantics — a
    # non-empty list ⇒ replace the L2-authored layer; omitted / empty ⇒
    # keep current (auto-triggered rules still render independently).
    new_supplemental = list(raw.l1_supplemental_rules) if raw.l1_supplemental_rules else None
    new_examples = list(raw.l1_situational_examples) if raw.l1_situational_examples else None

    failures = run_l2_output_validators(
        {
            "task_context_proposed": proposed_tc,
            "task_context_applied": new_task_context,
            "l1_supplemental_rules_proposed": new_supplemental,
            "l1_situational_examples_proposed": new_examples,
        },
        opt_sp,
    )
    failures.extend(layout_outcomes)
    if failures:
        logger.warning(
            "L2 output failed %d validator(s): %s",
            len(failures),
            ", ".join(o.validator_id for o in failures),
        )

    return TransitionResult(
        opt_search_point=opt_sp.mutate(source="l2_context", **changes),
        task_context=new_task_context,
        action=raw.action,
        axis_targeted=raw.axis_targeted,
        l1_layout=accepted_layout,
        l1_supplemental_rules=new_supplemental,
        l1_situational_examples=new_examples,
        l2_guard_breaches=failures,
        debug_prompt=prompt,
        debug_response=raw.model_dump(),
    )


def _apply_l2(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    osp = cycle.opt_sp
    if result.task_context:
        osp.task_context = result.task_context
    if result.l1_layout is not None:
        osp.l1_layout = result.l1_layout
    if result.l1_supplemental_rules is not None:
        osp.l1_supplemental_rules = result.l1_supplemental_rules
    if result.l1_situational_examples is not None:
        osp.l1_situational_examples = result.l1_situational_examples
    osp.wounds.l2_guard_breaches = list(result.l2_guard_breaches)
    cycle.escalation.record_l2_fired(
        best_accuracy=cycle.tracking.best_accuracy,
        best_composite_fitness=cycle.tracking.best_composite_fitness,
    )
    # Don't clobber prior axis when the LLM omits it — a stale axis is more
    # informative than an empty one for the next probe-outcome render.
    if result.axis_targeted:
        cycle.last_l2_axis = result.axis_targeted

    is_probe = result.action == "probe_round"
    record_decision(
        cycle.pending_decisions,
        ResumeCheckpointKind.PROBE_ROUND_COMMITMENT,
        {"round_num": round_num, "l2_round": cycle.escalation.l2_round},
        is_probe,
        data={
            "action": result.action,
            "task_context_changed": result.task_context is not None,
            "changes_description": result.opt_search_point.lineage.changes_description or "",
            "axis_targeted": result.axis_targeted,
        },
        round=round_num,
    )
    if is_probe:
        cycle.probe_next_round = True


def _l2_enter(cycle: Cycle) -> dict[str, Any]:
    return {
        "l2_round": cycle.escalation.l2_round,
        "l1_stall_count": cycle.escalation.l1_stall_count,
        "l1_overrides": cycle.opt_sp.l1_overrides,
        "current_accuracy": cycle.tracking.current_accuracy,
        "best_accuracy": cycle.tracking.best_accuracy,
    }


def _l2_exit(cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
    # ``l2_*_at_entry`` fields are read by ``EscalationState.fold`` on resume —
    # they're the canonical post-fire L2 state captured by ``record_l2_fired``.
    return {
        "l2_round": cycle.escalation.l2_round,
        "l2_stall_count": cycle.escalation.l2_stall_count,
        "l2_best_accuracy_at_entry": cycle.escalation.l2_best_accuracy_at_entry,
        "l2_best_composite_fitness_at_entry": cycle.escalation.l2_best_composite_fitness_at_entry,
        "param_changes_count": len(result.opt_search_point.l1_overrides),
        "task_context_changed": result.task_context is not None,
        "l1_layout_changed": result.l1_layout is not None,
        "l1_supplemental_rules_n": (
            len(result.l1_supplemental_rules) if result.l1_supplemental_rules is not None else None
        ),
        "l1_situational_examples_n": (
            len(result.l1_situational_examples)
            if result.l1_situational_examples is not None
            else None
        ),
        "changes_description": result.opt_search_point.lineage.changes_description or "",
        "action": result.action,
        "axis_targeted": result.axis_targeted,
        "warned_samples": len(cycle.warned_queries),
        "l2_prompt": result.debug_prompt,
        "l2_response": result.debug_response,
    }


L2 = LayerStrategy(
    layer_id="L2",
    template_name="l2_context",
    default_temperature=0.3,
    phase=CampaignPhase.REFINE_STRATEGY,
    parse=_parse_l2,
    apply=_apply_l2,
    enter_payload_fn=_l2_enter,
    exit_payload_fn=_l2_exit,
)


__all__ = ["L2"]
