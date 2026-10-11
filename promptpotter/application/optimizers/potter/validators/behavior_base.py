from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from promptpotter.application.optimizers.nodes import CheckResult
from promptpotter.domain.round_audit import RoundAudit

__all__ = ["CheckFn", "ValidatorContext"]


@dataclass(frozen=True)
class ValidatorContext:
    """``citable`` and ``exploration_budget`` are ``None`` on a round that banked no call."""

    prior_rounds: list[RoundAudit] = field(default_factory=list)
    citable: tuple[str, ...] | None = None
    context_object: list[str] = field(default_factory=list)
    exploration_budget: str | None = None
    peaked_axes: frozenset[str] = field(default_factory=frozenset)


CheckFn = Callable[[RoundAudit, ValidatorContext], CheckResult]
