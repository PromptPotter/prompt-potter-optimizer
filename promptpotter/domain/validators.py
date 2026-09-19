"""Two distinct contracts: ``LLMOutputValidator`` checks one parsed node output, ``StopRule`` checks the running results
stream. An outcome is EVIDENCE, never a control signal — what one costs is decided at the site that raised it."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from promptpotter.domain.escalation_signals import EscalationSignal
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement


@dataclass(frozen=True)
class ValidatorOutcome:
    """One issue found in an LLM-node output; a clean output returns ``None`` instead. No ``passed`` (an outcome only exists
    for an issue), no ``score`` and no severity: a SOFT report rides the same stream, so no consumer may escalate on the stream alone."""

    validator_id: str
    evidence: dict[str, Any] = field(default_factory=dict)


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
    """Mid-round stop rule over a candidate's results stream. Implementations may carry extra state; only ``name``,
    ``check`` and ``earliest_stop`` are contract, and the first non-``None`` signal wins."""

    name: str

    def check(self, results: list[QueryMeasurement]) -> EscalationSignal | None: ...

    def earliest_stop(
        self,
        results: list[QueryMeasurement],
        upcoming: Sequence[tuple[Sample, QueryMeasurement | None]],
    ) -> int | None:
        """The fewest rows at which ``check`` could fire, over every way the ``upcoming`` cells can
        still resolve — ``None`` if not before they are all in. A cell already measured out of order
        carries its row, and is a fact rather than an unknown. It may answer EARLY but never late:
        the walk launches one cell past it, so a late answer is paid in discarded calls. A rule that
        fires on a single row's content cannot be foreseen, and leaves that row out of this answer."""
        ...


__all__ = [
    "LLMOutputValidator",
    "StopRule",
    "ValidatorOutcome",
    "run_validators",
]
