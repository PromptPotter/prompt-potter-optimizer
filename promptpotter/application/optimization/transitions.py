"""L2/L3 transition primitives — layer-agnostic types + LLM-call template.

The actual ``L2`` / ``L3`` ``LayerStrategy`` instances live in
``escalation.py``; this module is what they share — ``TransitionResult``
and ``run_layer_transition``. V1 strips the L2 ``action`` channel, the
L1 surface override fan-out, and the L3 ``pipeline_params`` channel —
those are all deferred (see the plan at the top of this directory).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from promptpotter.application.optimization.llm_call import run_optimizer_node
from promptpotter.domain.l1_layout import L1Layout
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.llm import LLMClientBase

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.escalation import LayerStrategy

__all__ = [
    "TransitionResult",
    "run_layer_transition",
]


@dataclass
class TransitionResult:
    """L2/L3 transition result.

    L2 may write any combination of ``l2_brief``, ``l1_layout``,
    ``optimizer_params`` and ``task_context``. L3 only writes ``plan``.
    The validator outcomes ride alongside so the caller can persist them
    to the OSP for cross-fire self-healing.
    """

    opt_search_point: OptSearchPoint
    task_context: TaskDecomposition | None = None
    l2_brief: str = ""
    l1_layout: L1Layout | None = None
    l2_output_failures: list[ValidatorOutcome] = field(default_factory=list)
    l3_output_failures: list[ValidatorOutcome] = field(default_factory=list)
    debug_prompt: str = ""
    debug_response: dict | None = None


async def run_layer_transition(
    transition: LayerStrategy,
    cycle: Cycle,
    llm_client: LLMClientBase,
    *,
    model: str | None = None,
    temperature: float | None = None,
    round_num: int = 0,
) -> TransitionResult:
    """Shared LLM-call template for L2/L3 transitions.

    Walks: ``build_prompt_vars`` → meta-prompt LLM call →
    ``build_result``. ``apply_side_effects`` / ``enter_payload`` /
    ``exit_payload`` are called by ``escalation._run_transition``.
    """
    prompt_vars = transition.build_prompt_vars(cycle)
    raw, prompt = await run_optimizer_node(
        template_name=transition.template_name,
        prompt_vars=prompt_vars,
        llm_client=llm_client,
        model=model,
        temperature=transition.default_temperature if temperature is None else temperature,
        recorder=cycle.session.state.audit_projection,
        optimizer_call_cache=cycle.session.store.optimizer_calls,
    )
    return transition.build_result(raw, cycle.opt_sp, prompt)


# logger reserved for future diagnostic hooks; declared once so callers can
# `from .transitions import logger` without re-deriving it.
logger = logging.getLogger(__name__)
