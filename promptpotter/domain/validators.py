"""An outcome is EVIDENCE, never a control signal: what one costs is decided at the site that raised it."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import asyncio

    from promptpotter.domain.results import ArmOutcome, DegradationContext
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell


@dataclass(frozen=True)
class ValidatorOutcome:
    """No severity: a SOFT report rides the same stream, so the stream alone escalates nothing."""

    validator_id: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMOutputValidator:
    id: str
    check: Callable[..., ValidatorOutcome | None]


def run_validators(
    validators: tuple[LLMOutputValidator, ...],
    source_output: Mapping[str, Any],
    **context: Any,
) -> list[ValidatorOutcome]:
    outcomes: list[ValidatorOutcome] = []
    for validator in validators:
        outcome = validator.check(source_output, **context)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


@dataclass(frozen=True)
class StopSignal:
    check_name: str
    outcome: ArmOutcome
    check_result: Mapping[str, Any]


@dataclass(frozen=True)
class BrokenSignal(StopSignal):
    """A walk the BENCH stopped (run-health rule, gateway abort), never an eliminator's cut."""

    check_result: DegradationContext


@runtime_checkable
class StopRule(Protocol):
    name: str

    def check(self, results: Sequence[GradedCell]) -> StopSignal | None: ...

    def earliest_stop(
        self,
        results: Sequence[GradedCell],
        upcoming: Sequence[tuple[Sample, GradedCell | None]],
    ) -> int | None:
        """May answer EARLY but never late: the walk launches one cell past it, so a late answer is paid in discarded calls."""
        ...


class CatchUps(Protocol):
    """A prior's configuration on the cell just taken: awaited before judged, then committed."""

    def start_backfill(self, sample: Sample, room: int) -> list[asyncio.Future[Any]]: ...

    def owed_backfills(self, sample: Sample) -> int: ...

    def backfills_in_flight(self) -> list[asyncio.Future[Any]]: ...

    def backfills_for(self, sample: Sample) -> list[asyncio.Future[Any]]: ...

    def commit_backfills(self, sample: Sample) -> None: ...

    def bank_backfills(self, samples: Sequence[Sample]) -> None: ...

    def discard_backfills(self) -> None: ...


__all__ = [
    "BrokenSignal",
    "CatchUps",
    "LLMOutputValidator",
    "StopRule",
    "StopSignal",
    "ValidatorOutcome",
    "run_validators",
]
