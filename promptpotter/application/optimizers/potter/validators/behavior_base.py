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
from promptpotter.application.optimizers.potter.records import L1Layout

__all__ = ["CheckFn", "ValidatorContext"]


@dataclass(frozen=True)
class ValidatorContext:
    """``exploration_budget`` gates the ``stall_exploration`` escape hatch; ``None`` means the
    wiring layer could not determine it, and those citations then fail open."""

    round_num: int
    prior_rounds: list[dict[str, Any]] = field(default_factory=list)
    l1_layout: L1Layout | None = None
    context_object: list[str] = field(default_factory=list)
    exploration_budget: str | None = None
    # Axes the round-start AxisIndex flagged as ``peaked``. Used by
    # ``evidence_grounding_present`` to reject variants that cite
    # ``axis_memory`` to justify mutating a peaked axis without naming a
    # rebut (the critique naming that axis, or exploration_budget=wide).
    # Populated by ``l1/stats.py::review_reading`` from each round's
    # ``PotterRoundState.axis_memory_peaked``, banked as L1 proposed.
    peaked_axes: frozenset[str] = field(default_factory=frozenset)


CheckFn = Callable[[dict[str, Any], ValidatorContext], CheckResult]
