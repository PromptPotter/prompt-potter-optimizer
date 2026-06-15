"""LLM-output validators + mid-round stop rules — two distinct contracts.

**LLMOutputValidator** — deterministic check on a parsed LLM-node output;
emits a :class:`ValidatorOutcome` naming the failing ``validator_id`` + its
``evidence``. A guard-breach outcome carries no owner field: post-parse breaches
always route to L3 via the non-empty-stream → ``escalate_l2`` mechanism, so the
owner is structural, not a stored choice.

**StopRule** — mid-round check on the running results stream; emits
:class:`EscalationSignal` to stop the candidate (ELIMINATE / LEADER_LOCKED) or
route to the optimizer (L2 / L3 / ABORT). Concretes in
``application/optimization/pobb/elimination/checks.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from promptpotter.domain.escalation_signals import EscalationSignal
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.scoring import QueryMeasurement


@dataclass(frozen=True)
class ValidatorOutcome:
    """One issue found in an LLM-node output; clean outputs return ``None`` instead.

    No ``passed`` (an outcome only exists for a failure), no ``score`` (the
    self-healers count events, not graded scores), and no owner field (a
    guard-breach always routes to L3 structurally — see the module docstring).
    """

    validator_id: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LLMOutputValidator:
    """Registry-shaped validator. Mirrors :class:`Evaluator`."""

    id: str
    check: Callable[..., ValidatorOutcome | None]

    def run(self, source_output: Mapping[str, Any], **context: Any) -> ValidatorOutcome | None:
        return self.check(source_output, **context)


def run_validators(
    validators: tuple[LLMOutputValidator, ...],
    source_output: Mapping[str, Any],
    opt_sp: OptSearchPoint,
) -> list[ValidatorOutcome]:
    """Run every validator in ``validators``; return the non-``None`` outcomes."""
    outcomes: list[ValidatorOutcome] = []
    for validator in validators:
        outcome = validator.run(source_output, opt_sp=opt_sp)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


@runtime_checkable
class StopRule(Protocol):
    """Mid-round stop rule on a running candidate's results stream.

    Implementations may carry extra state (PoBBCheck tracks priors/snapshots/lock-in);
    only ``name`` + ``check`` are contract. ``score_population`` consumes a list;
    first non-None signal wins.
    """

    name: str

    def check(
        self,
        results: list[QueryMeasurement],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None: ...


__all__ = [
    "LLMOutputValidator",
    "StopRule",
    "ValidatorOutcome",
    "run_validators",
]
