from __future__ import annotations

from typing import Any, Protocol

from promptpotter.domain.run_records import CheckpointInputs, ResumeCheckpointRecord


class _DecisionSink(Protocol):
    def append(self, decision: ResumeCheckpointRecord, /) -> Any: ...


def record_decision(
    sink: _DecisionSink,
    inputs: CheckpointInputs,
    outcome: Any,
    *,
    node: str | None,
    round: int | None = None,
) -> Any:
    sink.append(
        ResumeCheckpointRecord(
            kind=inputs.kind, node=node, inputs_ref=inputs, outcome=outcome, round=round
        )
    )
    return outcome
