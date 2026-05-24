"""L2/L3 transition runner + `escalate_l2` cascade.

V1 contract:
* L2 writes `task_context` + `action` + optional `axis_targeted` / `l1_layout` / `l1_overrides`.
* L3 writes `plan` (required) + optional `note` (sticky L3→L2; wholesale-replaced on each L3 fire).

`LayerStrategy` spec lives in `optimization/transitions.py`; L2/L3 instances + their
parse/apply/enter/exit in `.l2_driver` / `.l3_driver`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.dispatch.hub import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimization.dispatch.llm_call import (
    load_optimizer_prompt,
    run_optimizer_node,
)
from promptpotter.application.optimization.escalation.firing.l2_driver import L2
from promptpotter.application.optimization.escalation.firing.l3_driver import L3
from promptpotter.application.optimization.escalation.state import NextAction
from promptpotter.application.optimization.resume_and_fork import (
    ResumeCheckpointKind,
    record_decision,
)
from promptpotter.application.optimization.transitions import LayerStrategy
from promptpotter.domain.l1_layout import coerce_l1_layout, validate_l1_layout
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import PhaseEvent, StopReason, emit_phase
from promptpotter.domain.run_records import ForkPayload
from promptpotter.infrastructure import llm as _llm_client
from promptpotter.infrastructure.tracing import LayerApplied, observed_node
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fork payload — applies operator/LLM-issued OSP deltas at fork mint time.
# ---------------------------------------------------------------------------


def apply_fork_payload_to_osp(opt_sp: OptSearchPoint, payload: ForkPayload) -> None:
    """Stamp a fork payload's L1-surface deltas on the OSP — same shape L2 writes. Assumes
    `payload.l1_layout is not None` (callers without deltas guard at the call site).
    """
    if payload.l1_layout is not None:
        layout = coerce_l1_layout(payload.l1_layout)
        if layout is None:
            raise ValueError(
                f"Fork payload l1_layout is unparseable: {payload.l1_layout!r}. "
                "Expect a dict whose keys are L1 layout slots and values are lists of "
                "placeholder names."
            )
        result = validate_l1_layout(layout, prior_layout=opt_sp.l1_layout)
        if not result.is_valid:
            ids = sorted({o.validator_id for o in result.outcomes if not o.passed})
            raise ValueError(f"Fork payload l1_layout failed hard validators: {ids}")
        opt_sp.l1_layout = layout


# ---------------------------------------------------------------------------
# Cascade — escalate_l2 walks the L2/L3 patience ladder + wound-4 force-L3.
# ---------------------------------------------------------------------------


async def _run_transition(
    transition: LayerStrategy,
    cycle: Cycle,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    obs: ObservabilityBridge | None,
    tracing_campaign_id: str,
) -> None:
    """enter → LLM → parse → adopt → LayerApplied → side-effects → exit.
    Layer-agnostic — everything layer-specific reads off the `LayerStrategy` spec.
    """
    assert cycle.tracking.current_sp is not None
    client = _llm_client.get_llm_client(config.optimizer_llm.provider)
    current_pp = cycle.tracking.current_sp.pipeline_params

    emit_phase(
        on_phase, transition.phase, "enter", round=round_num, **transition.enter_payload_fn(cycle)
    )
    async with observed_node(
        f"{transition.template_name}_r{round_num}",
        "llm/meta",
        obs=obs,
        campaign_id=tracing_campaign_id,
        round_num=round_num,
    ):
        template = load_optimizer_prompt(transition.template_name)
        prompt_vars = DispatchHub.fill_fixed(template, build_bundle(cycle))
        raw, prompt = await run_optimizer_node(
            template_name=transition.template_name,
            prompt_vars=prompt_vars,
            llm_client=client,
            model=config.optimizer_llm.model,
            temperature=transition.default_temperature,
            ledger=cycle.session.state.ledger,
            round_num=round_num,
            optimizer_call_cache=cycle.session.store.optimizer_calls,
        )
        result = transition.parse(raw, cycle.opt_sp, prompt)

    new_opt = result.opt_search_point
    cycle.opt_sp.copy_memory_to(new_opt)
    cycle.opt_sp = new_opt
    cycle.tracking.current_sp = new_opt.to_job_search_point(
        base_pipeline_params=current_pp, schema=pipeline_schema
    )
    if obs is not None:
        with graceful(f"LayerApplied({transition.layer_id}) emit failed"):
            obs.emit_write_point(
                LayerApplied,
                layer=transition.layer_id,
                campaign_id=tracing_campaign_id,
                round_num=round_num,
                changes_description=result.opt_search_point.lineage.changes_description or "",
            )
    transition.apply(cycle, result, round_num)
    emit_phase(
        on_phase,
        transition.phase,
        "exit",
        round=round_num,
        **transition.exit_payload_fn(cycle, result),
    )


def _trigger_payload(
    cycle: Cycle,
    round_num: int,
    patience: int | None,
    *,
    layer: str,
    track_accuracy: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(inputs_ref, data) for an L2/L3 escalation-trigger decision."""
    esc = cycle.escalation
    counter_round = getattr(esc, f"{layer}_round")
    inputs_ref = {
        "round_num": round_num,
        f"{layer}_patience": patience,
        "entry_round": counter_round if counter_round > 0 else -1,
    }
    data: dict[str, Any] = {
        f"{layer}_round": counter_round,
        "stall_count": getattr(esc, f"{layer}_stall_count"),
        "best_composite_fitness_at_entry": getattr(esc, f"{layer}_best_composite_fitness_at_entry"),
        "best_composite_fitness_this_round": cycle.tracking.best_composite_fitness,
    }
    if track_accuracy:
        data["best_accuracy"] = cycle.tracking.best_accuracy
    return inputs_ref, data


async def escalate_l2(
    cycle: Cycle,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: ObservabilityBridge | None = None,
    tracing_campaign_id: str = "",
) -> StopReason | None:
    """Run the L2/L3 patience cascade + L3 force-trigger heal on L2 validator failures.
    Called from `FIRE_L2` dispatch and the mid-round DegradationCheck path.
    """
    opt = config.optimization
    esc = cycle.escalation

    event = esc.observe_l2_escalation(
        current_composite_fitness=cycle.tracking.best_composite_fitness,
        l2_patience=opt.l2_patience,
        l3_patience=opt.l3_patience,
    )

    # L2 trigger decision is replayed for divergence — record fired-or-not.
    l2_inputs, l2_data = _trigger_payload(
        cycle, round_num, opt.l2_patience, layer="l2", track_accuracy=True
    )
    record_decision(
        cycle.pending_decisions,
        ResumeCheckpointKind.L2_ESCALATION_TRIGGER,
        l2_inputs,
        event.next_action == NextAction.FIRE_L2,
        data=l2_data,
        round=round_num,
    )

    if event.next_action == NextAction.FIRE_L2:
        await _run_transition(
            L2,
            cycle,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            tracing_campaign_id=tracing_campaign_id,
        )
        # Wound 4: post-L2 validator failure → L3 force-trigger. Deterministic from L2 output,
        # so resume reproduces without a separate decision record.
        # Exception: a SOLE soft-reject (paraphrase/verbatim repeat — already discarded by the
        # validator) skips L3. The fork_f13331ff cascade — L3 short plan → L1 malformed plan →
        # empty L1 — showed firing L3 on a single soft-reject layers escalations instead of
        # letting the loop self-correct.
        breaches = cycle.opt_sp.wounds.l2_guard_breaches
        SOFT_REJECT_IDS = {
            "l2_task_context_verbatim_repeat",
            "l2_task_context_paraphrase_repeat",
        }
        if breaches and not all(b.validator_id in SOFT_REJECT_IDS for b in breaches):
            logger.warning(
                "L3 force-triggered by %d L2-output validator failure(s) at round %d",
                len(breaches),
                round_num,
            )
            await _run_transition(
                L3,
                cycle,
                config,
                pipeline_schema,
                round_num,
                on_phase,
                obs=obs,
                tracing_campaign_id=tracing_campaign_id,
            )
        elif breaches:
            logger.info(
                "L2 produced %d soft-reject breach(es) at round %d (%s) — skipping L3 "
                "force-trigger; prior task_context retained, L1 continues next round",
                len(breaches),
                round_num,
                ", ".join(b.validator_id for b in breaches),
            )
        return None

    # FIRE_L3 or STOP_L3_PATIENCE — record L3 trigger decision either way.
    l3_inputs, l3_data = _trigger_payload(cycle, round_num, opt.l3_patience, layer="l3")
    record_decision(
        cycle.pending_decisions,
        ResumeCheckpointKind.L3_ESCALATION_TRIGGER,
        l3_inputs,
        event.next_action == NextAction.FIRE_L3,
        data=l3_data,
        round=round_num,
    )

    if event.next_action == NextAction.FIRE_L3:
        await _run_transition(
            L3,
            cycle,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            tracing_campaign_id=tracing_campaign_id,
        )
        return None

    return StopReason.L3_PATIENCE


__all__ = [
    "apply_fork_payload_to_osp",
    "escalate_l2",
]
