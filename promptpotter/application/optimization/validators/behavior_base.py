"""The vocabulary every behaviour check speaks, owned by neither layer that speaks it.

``*_behavior`` modules SCORE conformance into ``review.md`` and the round file; they never
block a candidate (`../CLAUDE.md` § A validator either REJECTS or SCORES). The shapes that
posture is expressed in — one result, one context, one signature — are layer-agnostic, and
living in ``l1_behavior.py`` made every consumer import L1 to talk about L2: ``l2_behavior``
took both types from it and then re-declared ``CheckFn`` verbatim beside them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = ["CheckFn", "CheckResult", "ValidatorContext"]


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class ValidatorContext:
    """``exploration_budget`` gates the ``stall_exploration`` escape hatch; ``None`` means the
    wiring layer could not determine it, and those citations then fail open."""

    round_num: int
    prior_rounds: list[dict[str, Any]] = field(default_factory=list)
    opt_sp: dict[str, Any] = field(default_factory=dict)
    context_object: list[str] = field(default_factory=list)
    exploration_budget: str | None = None
    # Axes the round-start AxisIndex flagged as ``peaked``. Used by
    # ``evidence_grounding_present`` to reject variants that cite
    # ``axis_memory`` to justify mutating a peaked axis without naming a
    # rebut (the critique naming that axis, or exploration_budget=wide).
    # Populated by ``review_md.py::_compute_behavior_per_round`` from each round's
    # ``axis_memory_peaked`` field, stashed by ``persist_round``.
    peaked_axes: frozenset[str] = field(default_factory=frozenset)


CheckFn = Callable[[dict[str, Any], ValidatorContext], CheckResult]
