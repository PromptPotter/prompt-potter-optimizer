"""LLM-output validators and mid-round stop rules.

Two contracts live here, named to keep callers from mixing them up:

**LLM-output validator** — :class:`LLMOutputValidator`. Deterministic check on
an LLM node's parsed output dict; emits a :class:`ValidatorOutcome` whose
``nurse_target`` names which other LLM heals the producer. Drives self-healing.

    L1's output --[L1 validator]--> ValidatorOutcome (nurse_target="l2") --> L2 heals L1
    L2's output --[L2 validator]--> ValidatorOutcome (nurse_target="l3") --> L3 heals L2

The ``score`` field mirrors ``Evaluator.compute``'s return shape (1.0 = clean,
0.0 = full failure) so future L4 composite_fitness scoring can read validator outcomes
through the same channel as evaluators.

**Stop rule** — :class:`StopRule`. Mid-round check on the running result stream
of a candidate-under-evaluation; emits an :class:`EscalationSignal` carrying
an :class:`EscalationTarget` that terminates the round early
(``ELIMINATE_CANDIDATE`` / ``LEADER_LOCKED``) or routes to the optimizer
(``L2`` / ``L3`` / ``ABORT_CAMPAIGN``). Concrete stop rules: ``DegradationCheck``,
``PoBBCheck`` in ``application/optimization/elimination.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from promptpotter.domain.analysis import EscalationSignal
    from promptpotter.domain.scoring import QueryMeasurement

NurseTarget = Literal["l2", "l3"]


@dataclass(frozen=True)
class ValidatorOutcome:
    """Result of running one validator against one LLM-node output.

    Empty/clean outputs produce no outcome (the validator's ``check`` returns
    ``None``). A non-None outcome represents one or more issues found in the
    same output dict, bundled together for atomic recording.
    """

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
    description: str
    nurse_target: NurseTarget
    check: Callable[..., ValidatorOutcome | None]

    def run(self, source_output: Mapping[str, Any], **context: Any) -> ValidatorOutcome | None:
        return self.check(source_output, **context)


@runtime_checkable
class StopRule(Protocol):
    """Mid-round stop rule on a running candidate's results stream.

    Implementations may carry additional state and methods — e.g. ``PoBBCheck``
    tracks priors, snapshot callbacks, and lock-in thresholds — but only
    ``name`` and ``check`` are part of the Protocol contract. The registry
    consumed by ``score_population`` is ``list[StopRule]``: first non-None
    signal wins.
    """

    name: str

    def check(
        self,
        results: list[QueryMeasurement],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None: ...
