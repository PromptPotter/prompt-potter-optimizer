"""L2/L3 transition primitives — types + the shared LLM-call template.

``L2RefineStrategy`` and ``L3ModifyPlan`` (the strategy classes that own
``apply_side_effects`` and the cycle mutation perimeter) live in
``escalation.py``. This module owns the LAYER-AGNOSTIC transition
primitives — ``OptimizerAction``, ``TransitionResult``, the
``run_layer_transition`` shared template, and the L3 prompt-extras builder
``compile_l3_extras`` (counterpart to ``l2_surface.compile_l2_extras``).
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.formatting import (
    format_axis_digest_block,
    format_l2_output_failures_for_l3,
    format_pipeline_section,
    format_runtime_failures_for_l3,
)
from promptpotter.application.optimization.llm_call import run_optimizer_node
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.llm import LLMClientBase

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.escalation import L2RefineStrategy, L3ModifyPlan

__all__ = [
    "OptimizerAction",
    "TransitionResult",
    "compile_l3_extras",
    "run_layer_transition",
]


_L3_AXIS_LABELS: dict[str, str] = {
    "axis_rankings": "Axis impact rankings",
    "bottleneck_distribution": "Bottleneck distribution",
    "failure_clusters": "Failure clusters",
    "persistent_failures": "Persistent failures",
}


class OptimizerAction(enum.StrEnum):
    """Whether the next round runs as a normal round or as a probe round.

    Probe rounds scope evaluation to warned queries only; normal rounds
    run on the full scoring set.
    """

    NORMAL_ROUND = "normal_round"
    PROBE_ROUND = "probe_round"


@dataclass
class TransitionResult:
    """L2/L3 transition result.

    L2 may write any combination of these fields on the next OSP — they
    are independent. Surface mutations (`scheme_overrides`,
    `text_overrides`, `template_override`) target L1's prompt surface;
    `directive`, `optimizer_params`, and `task_context` are L2's strategic
    levers; `action` controls whether the next round is normal or a probe.
    """

    opt_search_point: OptSearchPoint
    pipeline_params: dict | None = None
    task_context: TaskDecomposition | None = None
    l2_directive: str = ""
    action: OptimizerAction = OptimizerAction.NORMAL_ROUND
    scheme_overrides: dict[str, bool] = field(default_factory=dict)
    text_overrides: dict[str, str] = field(default_factory=dict)
    template_override: str = ""
    l2_output_failures: list[ValidatorOutcome] = field(default_factory=list)
    debug_prompt: str = ""
    debug_response: dict | None = None


async def run_layer_transition(
    transition: L2RefineStrategy | L3ModifyPlan,
    cycle: Cycle,
    llm_client: LLMClientBase,
    *,
    model: str | None = None,
    temperature: float | None = None,
    pipeline_params: dict | None = None,
    round_num: int = 0,
    escalation_check_result: dict | None = None,
) -> TransitionResult:
    """Shared LLM-call template for L2/L3 transitions.

    Walks: ``build_compile_vars`` → meta-prompt LLM call →
    ``build_result``. ``apply_side_effects`` / ``enter_payload`` /
    ``exit_payload`` are called by ``escalation._run_transition``.
    """
    compile_vars = transition.build_compile_vars(
        cycle,
        pipeline_params=pipeline_params,
        round_num=round_num,
        escalation_check_result=escalation_check_result,
    )
    raw, prompt = await run_optimizer_node(
        template_name=transition.template_name,
        compile_vars=compile_vars,
        llm_client=llm_client,
        model=model,
        temperature=transition.default_temperature if temperature is None else temperature,
        recorder=cycle.session.state.round_recorder,
        cache=cycle.session.store.optimizer_calls,
    )
    return transition.build_result(raw, cycle.opt_sp, prompt, pipeline_params=pipeline_params)


def compile_l3_extras(cycle: Cycle, pipeline_params: dict | None) -> dict[str, str]:
    """Build L3's prompt vars (no override processing, no ``\\n\\n`` suffix).

    L3 has no L2-mutated section overrides — it owns the strategic plan
    directly. ``current_plan``, ``rendered_prompt``, ``pipeline_section``,
    ``axes_digest`` are plain values; ``runtime_failures_section`` and
    ``l2_output_failures_section`` carry their own leading ``\\n\\n``
    when non-empty so the template stays inert when they collapse.
    ``l2_summary`` is a single-entry synthetic recap of the most recent
    L2 round (params + accuracy change since L3 last fired).
    """
    opt_sp = cycle.opt_sp
    pipeline_schema = cycle.session.pipeline_schema

    l2_history: list[dict[str, Any]] = [
        {
            "l2_round": cycle.escalation.l2.round,
            "optimizer_params": opt_sp.optimizer_params,
            "accuracy_change": cycle.tracking.best_composite
            - cycle.escalation.l3.best_composite_at_entry,
        }
    ]
    l2_summary = "\n".join(
        f"  L2 round {rd.get('l2_round', '?')}: "
        f"params={rd.get('parameters', {})}, "
        f"acc_change={rd.get('accuracy_change', 0):+.1%}"
        for rd in l2_history[-3:]
    )

    runtime_failures_section = format_runtime_failures_for_l3(
        [rf.to_dict() for rf in opt_sp.runtime_failures]
    )
    l2_output_failures_section = format_l2_output_failures_for_l3(list(opt_sp.l2_output_failures))

    return {
        "current_plan": opt_sp.plan or "(none — default strategy)",
        "l2_summary": l2_summary,
        "rendered_prompt": opt_sp.render(),
        "pipeline_section": format_pipeline_section(pipeline_params, pipeline_schema),
        "runtime_failures_section": (
            "\n\n" + runtime_failures_section if runtime_failures_section else ""
        ),
        "l2_output_failures_section": (
            "\n\n" + l2_output_failures_section if l2_output_failures_section else ""
        ),
        "axes_digest": format_axis_digest_block(
            cycle.axes.digest_for_l3() if cycle.axes else None,
            _L3_AXIS_LABELS,
            header="HISTORICAL CONTEXT:",
        ),
    }


# logger reserved for future diagnostic hooks; declared once so callers can
# `from .transitions import logger` without re-deriving it.
logger = logging.getLogger(__name__)
