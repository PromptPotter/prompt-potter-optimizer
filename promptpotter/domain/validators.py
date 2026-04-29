"""Validators on LLM-node output.

A validator is a deterministic check on an LLM node's parsed output dict.
Each validator emits a :class:`ValidatorOutcome` that carries an Evaluator-
shaped payload (``id``, ``passed``, ``score``, ``evidence``) plus a
``nurse_target`` naming which other LLM heals the producer.

Pattern:

    L1's output --[L1 validator]--> ValidatorOutcome (nurse_target="l2") --> L2 heals L1
    L2's output --[L2 validator]--> ValidatorOutcome (nurse_target="l3") --> L3 heals L2

The ``score`` field mirrors ``Evaluator.compute``'s return shape (1.0 = clean,
0.0 = full failure) so future L4 composite scoring can read validator outcomes
through the same channel as evaluators.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

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
