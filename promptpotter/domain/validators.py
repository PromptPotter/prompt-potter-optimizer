"""Two distinct contracts: ``LLMOutputValidator`` checks one parsed node output, ``StopRule`` checks the running results
stream. A guard breach carries no owner — post-parse breaches always route to L3, structurally."""

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
    """One issue found in an LLM-node output; a clean output returns ``None`` instead. No ``passed`` (an outcome only exists
    for a failure), no ``score``, and no owner field (a guard breach routes to L3 structurally)."""

    validator_id: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LLMOutputValidator:
    id: str
    check: Callable[..., ValidatorOutcome | None]

    def run(self, source_output: Mapping[str, Any], **context: Any) -> ValidatorOutcome | None:
        return self.check(source_output, **context)


def run_validators(
    validators: tuple[LLMOutputValidator, ...],
    source_output: Mapping[str, Any],
    opt_sp: OptSearchPoint,
) -> list[ValidatorOutcome]:
    outcomes: list[ValidatorOutcome] = []
    for validator in validators:
        outcome = validator.run(source_output, opt_sp=opt_sp)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


@runtime_checkable
class StopRule(Protocol):
    """Mid-round stop rule over a candidate's results stream. Implementations may carry extra state; only ``name`` +
    ``check`` are contract, and the first non-``None`` signal wins."""

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
