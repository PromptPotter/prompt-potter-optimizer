"""L2/L3 transition types shared by the firing path.

``TransitionResult`` = one L2/L3 fire's output; ``LayerStrategy`` = static
per-layer spec (template, phase, the four parse/apply/enter/exit callables)
that ``escalation.firing.executor`` reads. Provider/model/temperature are
sourced from the layer's optimizer node config
(``datasets/_optimizer/pipeline.json``) inside ``llm_call``, not held here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.optimization.dispatch.schemas import (
    ForkProposal,
    OptimizerAction,
    TerminateProposal,
)
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
    "TransitionResult",
]


@dataclass
class TransitionResult:
    """L2/L3 transition result.

    L2 writes ``task_context``/``l1_layout``/``l1_overrides`` + ``action``
    (``normal_round`` default | ``probe_round`` for warned-query re-run on
    the same OSP). L3 writes ``plan``, optional ``l3_note`` (sticky until
    next L3 fire). Both layers may emit ``fork_proposal`` — the
    ``_run_transition`` post-apply hook stashes it on ``cycle.rebase_request``
    and raises ``StopLoop(StopReason.REBASED)``; ``runner.entry`` resolves
    the request post-finalize into an automatic ``_mint_fork`` +
    observer rebuild + loop re-entry on the new fork. ``axis_targeted``
    is required prose when ``action='probe_round'``.
    """

    opt_search_point: OptSearchPoint
    task_context: TaskDecomposition | None = None
    l3_note: str = ""
    action: OptimizerAction = "normal_round"
    axis_targeted: str = ""
    l1_layout: L1Layout | None = None
    # ``None`` ⇒ keep current; any list (incl. ``[]``) ⇒ full-replace.
    l1_supplemental_rules: list[L1SupplementalRule] | None = None
    l1_situational_examples: list[L1SituationalExample] | None = None
    l2_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None
    debug_prompt: str = ""
    debug_response: dict[str, Any] | None = None


# Per-layer callable slots carried on ``LayerStrategy``.
ParseFn = Callable[[Any, OptSearchPoint, str], TransitionResult]
ApplyFn = Callable[["Cycle", TransitionResult, int], None]
PayloadFn = Callable[["Cycle"], dict[str, Any]]
ExitFn = Callable[["Cycle", TransitionResult], dict[str, Any]]


@dataclass(frozen=True)
class LayerStrategy:
    """Static per-layer spec for one escalation layer (L2 or L3).

    Pure data read by ``executor._run_transition``; the ``L2``/``L3`` instances
    and their parse/apply/enter/exit callables live in ``escalation/firing/executor.py``.
    """

    layer_id: Literal["L2", "L3"]
    template_name: str
    phase: CampaignPhase
    parse: ParseFn
    apply: ApplyFn
    enter_payload_fn: PayloadFn
    exit_payload_fn: ExitFn
