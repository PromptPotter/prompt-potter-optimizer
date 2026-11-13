"""An outcome is EVIDENCE, never a control signal: what one costs is decided at the site that raised it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from promptpotter.domain.arms import DegradationContext
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell


@dataclass(frozen=True)
class ValidatorOutcome:
    """No severity: a SOFT report rides the same stream, so the stream alone escalates nothing."""

    validator_id: str
    evidence: dict[str, Any] = field(default_factory=dict)


#: How a stop ends the walk: cut against the arm, or locked in for it.
WalkStop = Literal["broken", "eliminated", "locked_in"]


@dataclass(frozen=True)
class StopSignal:
    check_name: str
    outcome: WalkStop
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
