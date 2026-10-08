"""The vocabulary every behaviour check speaks, owned by neither layer that speaks it.

``*_behavior`` modules SCORE conformance into ``review.md`` and the round file; they never
block a candidate (`../CLAUDE.md` § A validator either REJECTS or SCORES). The shapes that
posture is expressed in — one context, one signature — are layer-agnostic, so no L2 check
imports L1 to speak them. The result is the bench's ``nodes.CheckResult``, since ``review.md``
renders it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from promptpotter.application.optimizers.nodes import CheckResult

__all__ = ["CheckFn", "ValidatorContext"]


@dataclass(frozen=True)
class ValidatorContext:
    """What one round's generation call offered, off the round's banked state: the citation menu on
    its wire and the budget it is composed under. Both ``None`` on a round that banked no call."""

    prior_rounds: list[dict[str, Any]] = field(default_factory=list)
    citable: tuple[str, ...] | None = None
    context_object: list[str] = field(default_factory=list)
    exploration_budget: str | None = None
    # Axes the round-start AxisIndex flagged ``peaked``: citing ``axis_memory`` to mutate one
    # needs a rebut — the critique naming that axis, or a wide exploration budget.
    peaked_axes: frozenset[str] = field(default_factory=frozenset)


CheckFn = Callable[[dict[str, Any], ValidatorContext], CheckResult]
