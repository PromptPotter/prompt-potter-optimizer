"""L2/L3 transition types — the layer-agnostic shapes the firing path shares.

``TransitionResult`` is what an L2/L3 fire produces; ``LayerStrategy`` is the
static per-layer spec (template, phase, temperature, and the four
parse/apply/enter/exit callables) that ``escalation.firing.executor`` reads
while running the shared transition mechanics. V1 keeps the L2 ``action``
channel (``normal_round`` vs ``probe_round``) but strips the L1 surface
override fan-out and the L3 ``pipeline_params`` channel.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.optimization.dispatch.schemas import ForkProposal
from promptpotter.domain.l1_layout import L1Layout
from promptpotter.domain.opt_search_point import (
    L1SituationalExample,
    L1SupplementalRule,
    OptSearchPoint,
)
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.validators import ValidatorOutcome

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle

__all__ = [
    "LayerStrategy",
    "OptimizerAction",
    "TransitionResult",
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


# Per-layer callable slots carried on ``LayerStrategy``.
ParseFn = Callable[[Any, OptSearchPoint, str], TransitionResult]
ApplyFn = Callable[["Cycle", TransitionResult, int], None]
PayloadFn = Callable[["Cycle"], dict[str, Any]]
ExitFn = Callable[["Cycle", TransitionResult], dict[str, Any]]


@dataclass(frozen=True)
class LayerStrategy:
    """Static per-layer spec for one escalation layer (L2 or L3).

    Pure data: the layer-specific knobs ``executor._run_transition`` reads
    while it runs the shared enter → LLM call → adopt → apply → exit
    mechanics. The ``L2`` / ``L3`` instances live in ``firing/l2_driver.py``
    and ``firing/l3_driver.py`` next to the callables they bundle.
    """

    layer_id: Literal["L2", "L3"]
    template_name: str
    default_temperature: float
    phase: CampaignPhase
    parse: ParseFn
    apply: ApplyFn
    enter_payload_fn: PayloadFn
    exit_payload_fn: ExitFn
