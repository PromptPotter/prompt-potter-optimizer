"""A decision the loop took, banked with what its replay reads: one kind per package taking it."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, SerializeAsAny, model_validator

from promptpotter.domain.fork import ForkSpec
from promptpotter.domain.optimizer_state import STATE_KINDS, StatePart
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.records import CycleRecord
from promptpotter.shared.clock import utcnow_iso


class DecisionInputs(StatePart, kind=None):
    """One kind of decision, declared in the package taking it: everything its writer banks and its replay reads."""


class DecisionRecord(CycleRecord):
    """``inputs`` + ``outcome`` drive divergence; parsed by kind, so a record its writer never writes refuses at the read."""

    record_type: Literal["decision"] = "decision"
    kind: str
    # The manifest node whose member took the decision; ``None`` for the bench's own.
    node: str | None = None
    inputs: SerializeAsAny[DecisionInputs]
    outcome: Any = None
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @model_validator(mode="before")
    @classmethod
    def _read_by_kind(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        inputs = STATE_KINDS.parse(raw["kind"], raw["inputs"])
        if not isinstance(inputs, DecisionInputs):
            raise ValueError(f"{raw['kind']!r} is a state kind, never a decision's")
        return {**raw, "inputs": inputs}


class ForkCutFrom(DecisionInputs, kind="fork_cut"):
    from_round: int
    fork: ForkSpec


class PanelCoverage(DecisionInputs, kind="panel_coverage"):
    # Every arm of the round's panel, holed or not.
    holes_by_arm: dict[ArmKey, int]


class OriginGatePass(DecisionInputs, kind="origin_gate_pass"):
    """The operator's pass past the origin the gate stopped: round 0's, standing while it does."""

    by: str
