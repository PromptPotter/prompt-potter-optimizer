"""LLM-output validators + mid-round stop rules — two distinct contracts.

**LLMOutputValidator** — deterministic check on a parsed LLM-node output;
emits :class:`ValidatorOutcome` whose ``nurse_target`` names which LLM heals
the producer (L1 output → L2 heals; L2 output → L3 heals). ``score`` mirrors
``Evaluator.compute`` (1.0 clean, 0.0 fail) so composite_fitness can ingest it.

**StopRule** — mid-round check on the running results stream; emits
:class:`EscalationSignal` to stop the candidate (ELIMINATE / LEADER_LOCKED) or
route to the optimizer (L2 / L3 / ABORT). Concretes in
``application/optimization/pobb/elimination/checks.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from promptpotter.domain.escalation_signals import EscalationSignal
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.scoring import QueryMeasurement

NurseTarget = Literal["l2", "l3"]


@dataclass(frozen=True)
class ValidatorOutcome:
    """Issues found in one LLM-node output; clean outputs return ``None`` instead."""

    validator_id: str
    passed: bool
    score: float
    evidence: dict[str, Any] = field(default_factory=dict)
    nurse_target: NurseTarget = "l2"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LLMOutputValidator:
    """Registry-shaped validator. Mirrors :class:`Evaluator`."""

    id: str
    nurse_target: NurseTarget
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
    "NurseTarget",
    "StopRule",
    "ValidatorOutcome",
    "run_validators",
]
