from __future__ import annotations

from typing import Any, Protocol

from promptpotter.domain.records.run_records import CheckpointInputs, ResumeCheckpointRecord


class _DecisionSink[T](Protocol):
    def append(self, decision: ResumeCheckpointRecord, /) -> T: ...


def record_decision[T](
    sink: _DecisionSink[T],
    inputs: CheckpointInputs,
    outcome: Any,
    *,
    node: str | None,
    round: int | None = None,
) -> T:
    """What *sink* hands back for the append: a ledger's offset, which a fork's cut takes."""
    return sink.append(
        ResumeCheckpointRecord(
            kind=inputs.kind, node=node, inputs_ref=inputs, outcome=outcome, round=round
        )
    )
