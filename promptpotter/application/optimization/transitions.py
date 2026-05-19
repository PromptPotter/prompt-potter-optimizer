"""L2/L3 transition primitives — layer-agnostic types + LLM-call template.

The actual ``L2`` / ``L3`` ``LayerStrategy`` instances live in
``escalation.py``; this module is what they share — ``TransitionResult``
and ``run_layer_transition``. V1 keeps the L2 ``action`` channel
(``normal_round`` vs ``probe_round``) but strips the L1 surface override
fan-out and the L3 ``pipeline_params`` channel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from promptpotter.application.optimization.dispatch.llm_call import run_optimizer_node
from promptpotter.application.optimization.dispatch.schemas import ForkProposal
from promptpotter.domain.l1_layout import L1Layout
from promptpotter.domain.opt_search_point import (
    L1SituationalExample,
    L1SupplementalRule,
    OptSearchPoint,
)
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.llm import LLMClientBase

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.escalation.firing import LayerStrategy

__all__ = [
    "OptimizerAction",
    "TransitionResult",
    "run_layer_transition",
]


OptimizerAction = Literal["normal_round", "probe_round"]


@dataclass
class TransitionResult:
    """L2/L3 transition result.

    L2 may write any combination of ``task_context`` (broadcast framing
    refinement — the L2→all channel), ``l1_layout`` and
    ``l1_overrides``, plus an ``action`` selecting
    ``normal_round`` (default) or ``probe_round`` (re-run only the
    warned-query subset under the same OSP). L3 writes ``plan`` and
    optionally ``l3_note`` — a sticky pointer to the L2-layer that
    survives across L2 fires until the next L3 fire replaces it. L3
    may also emit ``fork_proposal`` to flag that the current subtree
    is exhausted and a deferred ancestor looks more promising
    (observation-only in v1; recorded to ``round_NNNN.json`` for the
    operator to read and act on manually). The validator outcomes ride
    alongside so the caller can persist them to the OSP for cross-fire
    self-healing. ``axis_targeted`` names the axis the L2 fire tests;
    required prose when ``action="probe_round"``, optional otherwise.
    """

    opt_search_point: OptSearchPoint
    task_context: TaskDecomposition | None = None
    l3_note: str = ""
    action: OptimizerAction = "normal_round"
    axis_targeted: str = ""
    l1_layout: L1Layout | None = None
    # L2 may also full-replace the L1 supplemental-rules + situational-examples
    # layers. ``None`` ⇒ keep current (L2 didn't author this fire); a list
    # (including ``[]``) ⇒ replace. The driver only writes when not None.
    l1_supplemental_rules: list[L1SupplementalRule] | None = None
    l1_situational_examples: list[L1SituationalExample] | None = None
    l2_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    fork_proposal: ForkProposal | None = None
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
        ledger=cycle.session.state.ledger,
        round_num=round_num,
        optimizer_call_cache=cycle.session.store.optimizer_calls,
    )
    return transition.build_result(raw, cycle.opt_sp, prompt)
